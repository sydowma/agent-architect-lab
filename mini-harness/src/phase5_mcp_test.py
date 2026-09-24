"""Unit and Integration Tests for Phase 5 Topic 3: MCP 2.0 Protocol and Tool Discovery.

Verifies:
1. JSON-RPC 2.0 Framing, Serialization, and Error Code validation.
2. MCP Handshake and Capability Negotiation (Client-Server).
3. Dynamic Tool Discovery (tools/list) and Parameter-validated execution (tools/call).
4. Addressable Resource Discovery (resources/list) and Reading (resources/read).
5. Dynamic Tool List Changed Notification and Client Cache Auto-Invalidation.
6. Error handling (MethodNotFound, InvalidParams, ServerNotInitialized).
7. Multimodal Browser Environment (Playwright / Computer Use emulation).
8. End-to-end integration: PicoAgent with PicoMCPBridge.
"""

import time
import unittest
from phase5_mcp_client import (
    InMemoryDuplexTransport,
    JSONRPCErrorCode,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    MCPBrowserServer,
    MCPProtocolClient,
    MCPProtocolError,
    MCPServer,
    PicoMCPBridge,
    parse_jsonrpc_message,
)
from phase5_pico_kernel import (
    AgentContext,
    AgentMessage,
    PicoAgent,
    Role,
)


class TestMCPProtocol(unittest.TestCase):
    """Test suite for MCP 2.0 protocol specifications and components."""

    def test_01_jsonrpc_framing_and_serialization(self):
        """JSON-RPC 2.0 messages serialize, deserialize, and enforce protocol invariants."""
        # 1. Request
        req = JSONRPCRequest(id=1, method="tools/list")
        raw_req = req.to_json()
        self.assertIn('"jsonrpc": "2.0"', raw_req)
        parsed_req = parse_jsonrpc_message(raw_req)
        self.assertIsInstance(parsed_req, JSONRPCRequest)
        self.assertEqual(parsed_req.method, "tools/list")
        self.assertEqual(parsed_req.id, 1)

        # 2. Response
        resp = JSONRPCResponse(id=1, result={"status": "ok"})
        parsed_resp = parse_jsonrpc_message(resp.to_json())
        self.assertIsInstance(parsed_resp, JSONRPCResponse)
        self.assertEqual(parsed_resp.result, {"status": "ok"})

        # 3. Notification (No id)
        notif = JSONRPCNotification(method="notifications/tools/list_changed")
        raw_notif = notif.to_json()
        self.assertNotIn('"id"', raw_notif)
        parsed_notif = parse_jsonrpc_message(raw_notif)
        self.assertIsInstance(parsed_notif, JSONRPCNotification)
        self.assertEqual(parsed_notif.method, "notifications/tools/list_changed")

        # 4. Malformed JSON defense
        with self.assertRaises(MCPProtocolError):
            parse_jsonrpc_message("not valid json")

        # 5. Missing jsonrpc version
        with self.assertRaises(MCPProtocolError):
            parse_jsonrpc_message('{"id": 1, "method": "test"}')

    def test_02_mcp_handshake_and_negotiation(self):
        """Client and Server perform handshake, exchanging capabilities and entering READY state."""
        client_trans, server_trans = InMemoryDuplexTransport.create_pair()
        server = MCPServer(name="test-server", version="1.5.0")
        server.attach_transport(server_trans)
        server.start_loop()

        client = MCPProtocolClient(transport=client_trans, name="test-client", version="2.0.0")

        self.assertFalse(client.is_initialized)
        result = client.initialize()

        self.assertTrue(client.is_initialized)
        self.assertEqual(result["serverInfo"]["name"], "test-server")
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertTrue(result["capabilities"]["tools"]["listChanged"])

        server.stop()

    def test_03_mcp_tool_discovery_and_execution(self):
        """Server exposes registered tools, Client discovers schema and executes successfully."""
        client_trans, server_trans = InMemoryDuplexTransport.create_pair()
        server = MCPServer(name="calculator-server")

        def add_handler(a: int, b: int) -> str:
            return str(a + b)

        server.register_tool(
            name="math_add",
            description="Adds two integers.",
            input_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
            handler=add_handler,
        )

        server.attach_transport(server_trans)
        server.start_loop()

        client = MCPProtocolClient(transport=client_trans)
        client.initialize()

        # 1. Discover tools
        tools = client.list_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "math_add")
        self.assertEqual(tools[0].input_schema["required"], ["a", "b"])

        # 2. Invoke tool
        call_res = client.call_tool("math_add", {"a": 40, "b": 2})
        self.assertFalse(call_res.is_error)
        self.assertEqual(call_res.text_content, "42")

        # 3. Missing required parameter raises protocol error
        with self.assertRaises(MCPProtocolError) as ctx:
            client.call_tool("math_add", {"a": 40})
        self.assertEqual(ctx.exception.code, JSONRPCErrorCode.INVALID_PARAMS)

        # 4. Handler runtime failure returns is_error=True
        server.register_tool(
            name="faulty_tool",
            description="Fails at runtime",
            input_schema={"type": "object"},
            handler=lambda: 1 / 0,
        )
        call_fault = client.call_tool("faulty_tool", {})
        self.assertTrue(call_fault.is_error)
        self.assertIn("division by zero", call_fault.text_content)

        server.stop()

    def test_04_mcp_resource_read(self):
        """Addressable resources can be enumerated and read via URI scheme."""
        client_trans, server_trans = InMemoryDuplexTransport.create_pair()
        server = MCPServer(name="resource-server")

        server.register_resource(
            uri="config://system/database",
            name="DB Config",
            description="Database configuration details",
            reader_fn=lambda: "host=localhost;port=5432;db=production",
            mime_type="text/plain",
        )

        server.attach_transport(server_trans)
        server.start_loop()

        client = MCPProtocolClient(transport=client_trans)
        client.initialize()

        resources = client.list_resources()
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0].uri, "config://system/database")

        content = client.read_resource("config://system/database")
        self.assertEqual(content.text, "host=localhost;port=5432;db=production")
        self.assertEqual(content.mime_type, "text/plain")

        server.stop()

    def test_05_mcp_tool_list_changed_notification(self):
        """Dynamic registration of tools triggers notification and invalidates client cache."""
        client_trans, server_trans = InMemoryDuplexTransport.create_pair()
        server = MCPServer(name="dynamic-server")
        server.attach_transport(server_trans)
        server.start_loop()

        client = MCPProtocolClient(transport=client_trans)
        client.initialize()

        # Initially 0 tools
        self.assertEqual(len(client.list_tools()), 0)

        # Track callback
        notif_received = {"count": 0}
        client.add_tools_changed_listener(lambda: notif_received.update({"count": notif_received["count"] + 1}))

        # Dynamically register new tool on server
        server.register_tool(
            name="new_feature",
            description="Hot-loaded feature tool",
            input_schema={"type": "object"},
            handler=lambda: "done",
        )

        # Give small tick for message delivery
        time.sleep(0.05)
        client.poll_notifications()

        self.assertEqual(notif_received["count"], 1)
        # New list_tools call fetches hot-reloaded tool list
        tools = client.list_tools(use_cache=False)
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "new_feature")

        server.stop()

    def test_06_mcp_error_code_mapping(self):
        """Standard JSON-RPC error codes are correctly mapped and surfaced."""
        client_trans, server_trans = InMemoryDuplexTransport.create_pair()
        server = MCPServer(name="error-server")
        server.attach_transport(server_trans)
        server.start_loop()

        client = MCPProtocolClient(transport=client_trans)

        # 1. Calling before handshake raises error
        with self.assertRaises(MCPProtocolError) as ctx:
            client.list_tools()
        self.assertEqual(ctx.exception.code, JSONRPCErrorCode.SERVER_NOT_INITIALIZED)

        # 2. Handshake
        client.initialize()

        # 3. Call non-existent tool
        with self.assertRaises(MCPProtocolError) as ctx2:
            client.call_tool("ghost_tool", {})
        self.assertEqual(ctx2.exception.code, JSONRPCErrorCode.METHOD_NOT_FOUND)

        server.stop()

    def test_07_mcp_browser_multimodal_simulation(self):
        """MCPBrowserServer exposes Playwright/Computer Use tools for browser automation."""
        client_trans, server_trans = InMemoryDuplexTransport.create_pair()
        browser_server = MCPBrowserServer()
        browser_server.attach_transport(server_trans)
        browser_server.start_loop()

        client = MCPProtocolClient(transport=client_trans)
        client.initialize()

        # 1. Verify standard browser tools are exposed
        tools = client.list_tools()
        tool_names = {t.name for t in tools}
        expected = {"browser_navigate", "browser_screenshot", "browser_click", "browser_extract_text"}
        self.assertTrue(expected.issubset(tool_names))

        # 2. Navigate
        nav_res = client.call_tool("browser_navigate", {"url": "https://github.com/explore"})
        self.assertIn("Successfully navigated", nav_res.text_content)
        self.assertIn("GitHub", nav_res.text_content)

        # 3. Screenshot (Multimodal content block)
        shot_res = client.call_tool("browser_screenshot", {"full_page": True})
        self.assertEqual(len(shot_res.content), 2)
        self.assertEqual(shot_res.content[0]["type"], "text")
        self.assertEqual(shot_res.content[1]["type"], "image")
        self.assertEqual(shot_res.content[1]["mimeType"], "image/png")

        # 4. Extract Text
        ext_res = client.call_tool("browser_extract_text", {"selector": "h1"})
        self.assertEqual(ext_res.text_content, "Welcome to Agent Sandbox")

        # 5. Click
        click_res = client.call_tool("browser_click", {"selector": "button#submit"})
        self.assertIn("Clicked element 'button#submit'", click_res.text_content)
        self.assertIn("button#submit", browser_server.clicked_elements)

        browser_server.stop()

    def test_08_pico_agent_mcp_bridge_end_to_end(self):
        """PicoAgent transparently invokes remote MCP Server tools via PicoMCPBridge."""
        client_trans, server_trans = InMemoryDuplexTransport.create_pair()
        server = MCPServer(name="db-query-service")

        def query_database(query: str) -> str:
            if "users" in query:
                return "id=101, username=admin, role=root"
            return "empty query result"

        server.register_tool(
            name="query_db",
            description="Executes a safe read query on the database.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
            handler=query_database,
        )
        server.attach_transport(server_trans)
        server.start_loop()

        client = MCPProtocolClient(transport=client_trans)
        client.initialize()

        # Connect Bridge
        bridge = PicoMCPBridge(client=client)
        specs = bridge.get_tool_specs()
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["function"]["name"], "query_db")

        # Agent generator invokes tool via bridge
        def agent_planner(msg: AgentMessage):
            # Model analyzes request and decides to call query_db
            if "check admin user" in msg.content:
                obs = bridge.execute_tool("query_db", {"query": "SELECT * FROM users WHERE role='root'"})
                yield f"[Thought]: Checked DB.\n[Observation]: {obs}\n[Answer]: Admin user found: {obs}"
            else:
                yield "I cannot help with that."

        agent = PicoAgent(
            name="dba_agent",
            role_description="Database administrator agent",
            generator_fn=agent_planner,
        )

        ctx = AgentContext(session_id="mcp_session", turn_index=1)
        incoming = AgentMessage(
            message_id="msg_mcp_1",
            role=Role.USER,
            sender="mark",
            recipient="dba_agent",
            content="Please check admin user status in the system.",
        )

        response = agent.generate(ctx, incoming)
        self.assertIn("[Observation]: id=101, username=admin, role=root", response.content)
        self.assertIn("Admin user found", response.content)

        server.stop()


if __name__ == "__main__":
    unittest.main()
