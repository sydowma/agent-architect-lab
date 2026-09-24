"""Phase 5: MCP 2.0 Industrial Protocol Ecosystem and Dynamic Tool Discovery.

Implements the Model Context Protocol (MCP 2.0) standard with zero external dependencies:
1. JSON-RPC 2.0 Message Specification (Request, Response, Notification, Error Codes).
2. Transport Layer (InMemoryDuplexTransport, SubprocessStdioTransport).
3. MCP Server Core (Handshake, Tools List/Call, Resources List/Read, Dynamic Change Notifications).
4. MCP Protocol Client (Lifecycle Handshake, Dynamic Discovery, Tool Invocation, Cache Management).
5. MCP Browser Server (Playwright / Computer Use multimodal emulation tools).
6. PicoMCPBridge (Bridges MCP Server Tools to PicoAgent / mini-harness runtime).
"""

from __future__ import annotations

import abc
import json
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union


# ============================================================================
# 0. JSON-RPC 2.0 规范与错误码 (JSON-RPC 2.0 Specification & Error Codes)
# ============================================================================

class JSONRPCErrorCode(IntEnum):
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    SERVER_NOT_INITIALIZED = -32002


class MCPError(Exception):
    """Base exception for all MCP protocol and execution errors."""


class MCPProtocolError(MCPError):
    """Raised on invalid JSON-RPC message structure or protocol violation."""
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


class MCPConnectionError(MCPError):
    """Raised when transport connection fails or is closed prematurely."""


class MCPTimeoutError(MCPError):
    """Raised when a request exceeds execution timeout."""


class MCPToolExecutionError(MCPError):
    """Raised when an MCP tool execution fails."""


@dataclass
class JSONRPCRequest:
    method: str
    params: Optional[Dict[str, Any]] = None
    id: Union[int, str] = field(default_factory=lambda: str(uuid.uuid4()))
    jsonrpc: str = "2.0"

    def to_json(self) -> str:
        payload: Dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
            "method": self.method,
        }
        if self.params is not None:
            payload["params"] = self.params
        return json.dumps(payload, ensure_ascii=False)


@dataclass
class JSONRPCResponse:
    id: Union[int, str]
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None
    jsonrpc: str = "2.0"

    def to_json(self) -> str:
        payload: Dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
        }
        if self.error is not None:
            payload["error"] = self.error
        else:
            payload["result"] = self.result
        return json.dumps(payload, ensure_ascii=False)


@dataclass
class JSONRPCNotification:
    method: str
    params: Optional[Dict[str, Any]] = None
    jsonrpc: str = "2.0"

    def to_json(self) -> str:
        payload: Dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
        }
        if self.params is not None:
            payload["params"] = self.params
        return json.dumps(payload, ensure_ascii=False)


def parse_jsonrpc_message(raw_text: str) -> Union[JSONRPCRequest, JSONRPCResponse, JSONRPCNotification]:
    """Parses raw JSON text into typed JSON-RPC 2.0 message."""
    try:
        data = json.loads(raw_text.strip())
    except Exception as e:
        raise MCPProtocolError(JSONRPCErrorCode.PARSE_ERROR, f"Invalid JSON payload: {e}")

    if not isinstance(data, dict) or data.get("jsonrpc") != "2.0":
        raise MCPProtocolError(JSONRPCErrorCode.INVALID_REQUEST, "Message missing 'jsonrpc': '2.0'")

    if "method" in data:
        if "id" in data:
            return JSONRPCRequest(
                id=data["id"],
                method=data["method"],
                params=data.get("params"),
            )
        else:
            return JSONRPCNotification(
                method=data["method"],
                params=data.get("params"),
            )
    elif "id" in data and ("result" in data or "error" in data):
        return JSONRPCResponse(
            id=data["id"],
            result=data.get("result"),
            error=data.get("error"),
        )
    else:
        raise MCPProtocolError(JSONRPCErrorCode.INVALID_REQUEST, "Malformed JSON-RPC message structure.")


# ============================================================================
# 1. 传输层抽象 (Transport Layer)
# ============================================================================

class MCPTransport(abc.ABC):
    """Abstract bidirectional transport for MCP messaging."""

    @abc.abstractmethod
    def send(self, message_str: str) -> None:
        """Sends raw line-delimited message."""
        raise NotImplementedError

    @abc.abstractmethod
    def receive(self, timeout: Optional[float] = None) -> Optional[str]:
        """Receives a line-delimited message or None on timeout."""
        raise NotImplementedError

    @abc.abstractmethod
    def close(self) -> None:
        """Closes transport and releases underlying resources."""
        raise NotImplementedError

    @abc.abstractmethod
    def is_connected(self) -> bool:
        """Returns True if the transport is open and active."""
        raise NotImplementedError


class InMemoryDuplexTransport(MCPTransport):
    """Thread-safe bidirectional in-memory queue transport."""

    def __init__(self, outgoing_q: queue.Queue[str], incoming_q: queue.Queue[str]):
        self._out_q = outgoing_q
        self._in_q = incoming_q
        self._active = True

    @classmethod
    def create_pair(cls) -> Tuple[InMemoryDuplexTransport, InMemoryDuplexTransport]:
        """Creates a paired Client/Server in-memory duplex channel."""
        q_client_to_server: queue.Queue[str] = queue.Queue()
        q_server_to_client: queue.Queue[str] = queue.Queue()
        client_transport = cls(outgoing_q=q_client_to_server, incoming_q=q_server_to_client)
        server_transport = cls(outgoing_q=q_server_to_client, incoming_q=q_client_to_server)
        return client_transport, server_transport

    def send(self, message_str: str) -> None:
        if not self._active:
            raise MCPConnectionError("Cannot send message: transport is closed.")
        self._out_q.put(message_str.strip())

    def receive(self, timeout: Optional[float] = None) -> Optional[str]:
        if not self._active and self._in_q.empty():
            return None
        try:
            return self._in_q.get(block=True, timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._active = False

    def is_connected(self) -> bool:
        return self._active


class SubprocessStdioTransport(MCPTransport):
    """Stdio pipe transport for spawning local MCP Server sub-processes."""

    def __init__(self, command: List[str], cwd: Optional[str] = None):
        self.command = command
        self.cwd = cwd
        self.proc: Optional[subprocess.Popen] = None
        self._in_q: queue.Queue[str] = queue.Queue()
        self._reader_thread: Optional[threading.Thread] = None
        self._active = False
        self._start_process()

    def _start_process(self) -> None:
        try:
            self.proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                text=True,
                bufsize=1,  # Line-buffered
            )
            self._active = True
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()
        except Exception as e:
            self._active = False
            raise MCPConnectionError(f"Failed to start MCP server subprocess: {e}")

    def _read_loop(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        for line in self.proc.stdout:
            line_str = line.strip()
            if line_str:
                self._in_q.put(line_str)
        self._active = False

    def send(self, message_str: str) -> None:
        if not self._active or not self.proc or not self.proc.stdin:
            raise MCPConnectionError("Subprocess stdin is closed or process died.")
        try:
            self.proc.stdin.write(message_str.strip() + "\n")
            self.proc.stdin.flush()
        except Exception as e:
            self._active = False
            raise MCPConnectionError(f"Failed writing to subprocess stdin: {e}")

    def receive(self, timeout: Optional[float] = None) -> Optional[str]:
        try:
            return self._in_q.get(block=True, timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._active = False
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                self.proc.terminate()
                self.proc.wait(timeout=1.0)
            except Exception:
                if self.proc:
                    self.proc.kill()

    def is_connected(self) -> bool:
        return self._active and self.proc is not None and self.proc.poll() is None


# ============================================================================
# 2. MCP 实体定义 (Domain Models)
# ============================================================================

@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class ToolCallResult:
    content: List[Dict[str, Any]]
    is_error: bool = False

    @property
    def text_content(self) -> str:
        texts = [b["text"] for b in self.content if b.get("type") == "text" and "text" in b]
        return "\n".join(texts)


@dataclass
class ResourceDefinition:
    uri: str
    name: str
    description: str = ""
    mime_type: str = "text/plain"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


@dataclass
class ResourceContent:
    uri: str
    text: Optional[str] = None
    blob: Optional[str] = None
    mime_type: str = "text/plain"


# ============================================================================
# 3. MCP 服务端引擎 (MCP Server Core)
# ============================================================================

class MCPServer:
    """Standard MCP 2.0 compliant server implementation."""

    def __init__(self, name: str = "pico-mcp-server", version: str = "1.0.0"):
        self.name = name
        self.version = version
        self.protocol_version = "2024-11-05"
        self.is_initialized = False

        self._tools: Dict[str, Tuple[ToolDefinition, Callable[..., Any]]] = {}
        self._resources: Dict[str, Tuple[ResourceDefinition, Callable[[], str]]] = {}
        self._transport: Optional[MCPTransport] = None
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        handler: Callable[..., Any],
    ) -> None:
        """Registers a tool and notifies client if active."""
        self._tools[name] = (ToolDefinition(name, description, input_schema), handler)
        if self.is_initialized and self._transport and self._transport.is_connected():
            self._notify_tools_changed()

    def register_resource(
        self,
        uri: str,
        name: str,
        description: str,
        reader_fn: Callable[[], str],
        mime_type: str = "text/plain",
    ) -> None:
        """Registers an addressable external resource."""
        res_def = ResourceDefinition(uri=uri, name=name, description=description, mime_type=mime_type)
        self._resources[uri] = (res_def, reader_fn)

    def attach_transport(self, transport: MCPTransport) -> None:
        self._transport = transport

    def start_loop(self) -> None:
        """Starts background listener loop processing requests."""
        if not self._transport:
            raise MCPConnectionError("Cannot start server: transport not attached.")
        self._running = True
        self._worker_thread = threading.Thread(target=self._serve_loop, daemon=True)
        self._worker_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._transport:
            self._transport.close()

    def _serve_loop(self) -> None:
        while self._running and self._transport and self._transport.is_connected():
            raw_msg = self._transport.receive(timeout=0.1)
            if raw_msg is not None:
                resp_str = self.process_message(raw_msg)
                if resp_str and self._transport and self._transport.is_connected():
                    self._transport.send(resp_str)

    def _notify_tools_changed(self) -> None:
        """Broadcasts list_changed notification to connected client."""
        notif = JSONRPCNotification(method="notifications/tools/list_changed")
        if self._transport and self._transport.is_connected():
            self._transport.send(notif.to_json())

    def process_message(self, raw_text: str) -> Optional[str]:
        """Dispatches an incoming JSON-RPC raw message and returns JSON response string."""
        try:
            msg = parse_jsonrpc_message(raw_text)
        except MCPProtocolError as pe:
            return JSONRPCResponse(id="null", error={"code": pe.code, "message": pe.message}).to_json()

        if isinstance(msg, JSONRPCNotification):
            self._handle_notification(msg)
            return None

        if isinstance(msg, JSONRPCRequest):
            return self._handle_request(msg).to_json()

        return None

    def _handle_notification(self, notif: JSONRPCNotification) -> None:
        if notif.method == "notifications/initialized":
            self.is_initialized = True

    def _handle_request(self, req: JSONRPCRequest) -> JSONRPCResponse:
        method = req.method
        params = req.params or {}

        # 1. 握手能力协商 (initialize)
        if method == "initialize":
            self.is_initialized = True
            result = {
                "protocolVersion": self.protocol_version,
                "capabilities": {
                    "tools": {"listChanged": True},
                    "resources": {"subscribe": False, "listChanged": True},
                },
                "serverInfo": {
                    "name": self.name,
                    "version": self.version,
                },
            }
            return JSONRPCResponse(id=req.id, result=result)

        # 2. 检查初始化门禁
        if not self.is_initialized:
            return JSONRPCResponse(
                id=req.id,
                error={
                    "code": JSONRPCErrorCode.SERVER_NOT_INITIALIZED,
                    "message": "Server has not been initialized with 'initialize' handshake.",
                },
            )

        # 3. 工具协议 (tools/list & tools/call)
        if method == "tools/list":
            tools_list = [td.to_dict() for td, _ in self._tools.values()]
            return JSONRPCResponse(id=req.id, result={"tools": tools_list})

        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            if not tool_name or tool_name not in self._tools:
                return JSONRPCResponse(
                    id=req.id,
                    error={
                        "code": JSONRPCErrorCode.METHOD_NOT_FOUND,
                        "message": f"Tool '{tool_name}' not found on server.",
                    },
                )

            tool_def, handler = self._tools[tool_name]
            # Schema argument validation
            required_fields = tool_def.input_schema.get("required", [])
            for rf in required_fields:
                if rf not in arguments:
                    return JSONRPCResponse(
                        id=req.id,
                        error={
                            "code": JSONRPCErrorCode.INVALID_PARAMS,
                            "message": f"Missing required parameter '{rf}' for tool '{tool_name}'.",
                        },
                    )

            try:
                output = handler(**arguments)
                if isinstance(output, str):
                    content = [{"type": "text", "text": output}]
                elif isinstance(output, dict) and "content" in output:
                    content = output["content"]
                else:
                    content = [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]
                return JSONRPCResponse(id=req.id, result={"content": content, "isError": False})
            except Exception as e:
                return JSONRPCResponse(
                    id=req.id,
                    result={
                        "content": [{"type": "text", "text": f"Tool execution failed: {str(e)}"}],
                        "isError": True,
                    },
                )

        # 4. 资源协议 (resources/list & resources/read)
        elif method == "resources/list":
            resources_list = [rd.to_dict() for rd, _ in self._resources.values()]
            return JSONRPCResponse(id=req.id, result={"resources": resources_list})

        elif method == "resources/read":
            uri = params.get("uri")
            if not uri or uri not in self._resources:
                return JSONRPCResponse(
                    id=req.id,
                    error={
                        "code": JSONRPCErrorCode.INVALID_PARAMS,
                        "message": f"Resource with URI '{uri}' not found.",
                    },
                )
            res_def, reader = self._resources[uri]
            try:
                text_content = reader()
                return JSONRPCResponse(
                    id=req.id,
                    result={
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": res_def.mime_type,
                                "text": text_content,
                            }
                        ]
                    },
                )
            except Exception as e:
                return JSONRPCResponse(
                    id=req.id,
                    error={
                        "code": JSONRPCErrorCode.INTERNAL_ERROR,
                        "message": f"Failed reading resource '{uri}': {e}",
                    },
                )

        # 5. 未知方法
        return JSONRPCResponse(
            id=req.id,
            error={
                "code": JSONRPCErrorCode.METHOD_NOT_FOUND,
                "message": f"Unknown MCP method: {method}",
            },
        )


# ============================================================================
# 4. MCP 客户端引擎 (MCP Protocol Client)
# ============================================================================

class MCPProtocolClient:
    """Client for connecting to, discovering tools from, and invoking MCP Servers."""

    def __init__(self, transport: MCPTransport, name: str = "PicoMCPClient", version: str = "1.0.0"):
        self.transport = transport
        self.name = name
        self.version = version
        self.server_info: Dict[str, Any] = {}
        self.server_capabilities: Dict[str, Any] = {}
        self.is_initialized = False

        self._tools_cache: Dict[str, ToolDefinition] = {}
        self._on_tools_changed_callbacks: List[Callable[[], None]] = []

    def initialize(self, timeout: float = 3.0) -> Dict[str, Any]:
        """Performs handshake and capability negotiation."""
        req = JSONRPCRequest(
            method="initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "roots": {"listChanged": False},
                    "sampling": {},
                },
                "clientInfo": {
                    "name": self.name,
                    "version": self.version,
                },
            },
        )
        resp = self._send_request(req, timeout=timeout)
        if resp.error:
            raise MCPProtocolError(resp.error["code"], resp.error["message"])

        result = resp.result or {}
        self.server_info = result.get("serverInfo", {})
        self.server_capabilities = result.get("capabilities", {})
        self.is_initialized = True

        # Send confirmed initialized notification
        notif = JSONRPCNotification(method="notifications/initialized")
        self.transport.send(notif.to_json())
        return result

    def list_tools(self, use_cache: bool = True, timeout: float = 3.0) -> List[ToolDefinition]:
        """Discovers tools available on the server with schema validation."""
        self._check_ready()
        if use_cache and self._tools_cache:
            return list(self._tools_cache.values())

        req = JSONRPCRequest(method="tools/list")
        resp = self._send_request(req, timeout=timeout)
        if resp.error:
            raise MCPProtocolError(resp.error["code"], resp.error["message"])

        tools_data = resp.result.get("tools", [])
        self._tools_cache.clear()
        for td in tools_data:
            tool_def = ToolDefinition(
                name=td["name"],
                description=td.get("description", ""),
                input_schema=td.get("inputSchema", {}),
            )
            self._tools_cache[tool_def.name] = tool_def

        return list(self._tools_cache.values())

    def call_tool(self, name: str, arguments: Dict[str, Any], timeout: float = 5.0) -> ToolCallResult:
        """Invokes remote tool and unpacks the returned content blocks."""
        self._check_ready()
        req = JSONRPCRequest(
            method="tools/call",
            params={"name": name, "arguments": arguments},
        )
        resp = self._send_request(req, timeout=timeout)
        if resp.error:
            raise MCPProtocolError(resp.error["code"], resp.error["message"])

        result = resp.result or {}
        content = result.get("content", [])
        is_error = result.get("isError", False)
        return ToolCallResult(content=content, is_error=is_error)

    def list_resources(self, timeout: float = 3.0) -> List[ResourceDefinition]:
        """Lists accessible static/dynamic resources on server."""
        self._check_ready()
        req = JSONRPCRequest(method="resources/list")
        resp = self._send_request(req, timeout=timeout)
        if resp.error:
            raise MCPProtocolError(resp.error["code"], resp.error["message"])

        res_list = resp.result.get("resources", [])
        return [
            ResourceDefinition(
                uri=item["uri"],
                name=item["name"],
                description=item.get("description", ""),
                mime_type=item.get("mimeType", "text/plain"),
            )
            for item in res_list
        ]

    def read_resource(self, uri: str, timeout: float = 3.0) -> ResourceContent:
        """Reads context resource content by URI."""
        self._check_ready()
        req = JSONRPCRequest(method="resources/read", params={"uri": uri})
        resp = self._send_request(req, timeout=timeout)
        if resp.error:
            raise MCPProtocolError(resp.error["code"], resp.error["message"])

        contents = resp.result.get("contents", [])
        if not contents:
            raise MCPProtocolError(JSONRPCErrorCode.INTERNAL_ERROR, f"Empty content returned for '{uri}'")

        item = contents[0]
        return ResourceContent(
            uri=item["uri"],
            text=item.get("text"),
            blob=item.get("blob"),
            mime_type=item.get("mimeType", "text/plain"),
        )

    def poll_notifications(self) -> None:
        """Checks incoming notifications (e.g. tools/list_changed)."""
        while True:
            raw = self.transport.receive(timeout=0.01)
            if not raw:
                break
            try:
                msg = parse_jsonrpc_message(raw)
                if isinstance(msg, JSONRPCNotification):
                    if msg.method == "notifications/tools/list_changed":
                        self._tools_cache.clear()
                        for cb in self._on_tools_changed_callbacks:
                            cb()
            except Exception:
                pass

    def add_tools_changed_listener(self, callback: Callable[[], None]) -> None:
        self._on_tools_changed_callbacks.append(callback)

    def _check_ready(self) -> None:
        if not self.is_initialized:
            raise MCPProtocolError(JSONRPCErrorCode.SERVER_NOT_INITIALIZED, "Client has not performed initialize handshake.")

    def _send_request(self, req: JSONRPCRequest, timeout: float) -> JSONRPCResponse:
        self.transport.send(req.to_json())
        start_time = time.time()
        while time.time() - start_time < timeout:
            raw = self.transport.receive(timeout=0.1)
            if not raw:
                continue
            msg = parse_jsonrpc_message(raw)
            if isinstance(msg, JSONRPCNotification):
                if msg.method == "notifications/tools/list_changed":
                    self._tools_cache.clear()
                continue
            if isinstance(msg, JSONRPCResponse):
                if str(msg.id) == str(req.id):
                    return msg
        raise MCPTimeoutError(f"Request '{req.method}' (id={req.id}) timed out after {timeout}s")


# ============================================================================
# 5. 多模态浏览器环境服务 (MCP Browser Server - Computer Use)
# ============================================================================

class MCPBrowserServer(MCPServer):
    """Simulated Playwright/Computer Use browser environment exposed as standard MCP tools."""

    def __init__(self, name: str = "mcp-browser-environment"):
        super().__init__(name=name, version="2.0.0")
        self.current_url: Optional[str] = "about:blank"
        self.page_title: str = "Blank Page"
        self.dom_content: Dict[str, str] = {
            "h1": "Welcome to Agent Sandbox",
            ".lead": "Testing MCP Browser Operations",
            "button#submit": "Confirm Action",
        }
        self.clicked_elements: List[str] = []
        self._register_browser_tools()

    def _register_browser_tools(self) -> None:
        # 1. browser_navigate
        self.register_tool(
            name="browser_navigate",
            description="Navigates the browser to a target URL.",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Target website URL"}
                },
                "required": ["url"],
            },
            handler=self._navigate,
        )

        # 2. browser_screenshot
        self.register_tool(
            name="browser_screenshot",
            description="Captures viewport screenshot encoded as base64 representation.",
            input_schema={
                "type": "object",
                "properties": {
                    "full_page": {"type": "boolean", "description": "Capture full page or viewport"}
                },
            },
            handler=self._screenshot,
        )

        # 3. browser_click
        self.register_tool(
            name="browser_click",
            description="Simulates a user click on a selector element.",
            input_schema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector to click"}
                },
                "required": ["selector"],
            },
            handler=self._click,
        )

        # 4. browser_extract_text
        self.register_tool(
            name="browser_extract_text",
            description="Extracts clean text content from the rendered DOM.",
            input_schema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "Optional specific CSS selector"}
                },
            },
            handler=self._extract_text,
        )

    def _navigate(self, url: str) -> str:
        self.current_url = url
        if "google" in url:
            self.page_title = "Google Search"
        elif "github" in url:
            self.page_title = "GitHub - Coding Repository"
        else:
            self.page_title = f"Page for {url}"
        return f"Successfully navigated to {url} (Status: 200, Title: '{self.page_title}')"

    def _screenshot(self, full_page: bool = False) -> Dict[str, Any]:
        simulated_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Captured screenshot of '{self.current_url}' [FullPage={full_page}]",
                },
                {
                    "type": "image",
                    "mimeType": "image/png",
                    "data": simulated_base64,
                },
            ]
        }

    def _click(self, selector: str) -> str:
        if selector in self.dom_content:
            self.clicked_elements.append(selector)
            return f"Clicked element '{selector}' containing text '{self.dom_content[selector]}'."
        raise ValueError(f"DOM element selector '{selector}' not found on page.")

    def _extract_text(self, selector: Optional[str] = None) -> str:
        if selector:
            if selector in self.dom_content:
                return self.dom_content[selector]
            raise ValueError(f"Selector '{selector}' not found.")
        # Return all DOM text
        return "\n".join(f"<{tag}>{text}</{tag}>" for tag, text in self.dom_content.items())


# ============================================================================
# 6. PicoAgent 与 MCP 桥接器 (PicoMCPBridge)
# ============================================================================

class PicoMCPBridge:
    """Bridges remote MCP Server tools into native functions usable by PicoAgents / mini-harness."""

    def __init__(self, client: MCPProtocolClient):
        self.client = client

    def get_tool_specs(self) -> List[Dict[str, Any]]:
        """Returns standard Function Calling / Tool Calling JSON specifications."""
        tools = self.client.list_tools()
        specs = []
        for t in tools:
            specs.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            })
        return specs

    def execute_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """Executes the tool via MCP Client and returns string observation."""
        result = self.client.call_tool(name=name, arguments=arguments)
        if result.is_error:
            return f"[MCP TOOL ERROR]: {result.text_content}"
        return result.text_content
