"""mini-harness Day 11: Hierarchical Memory Architecture.

Implements native, zero-dependency dual-track persistent memory:
1. GraphMemory: In-memory directed knowledge graph with BFS N-hop relation traversal.
2. EpisodicMemory: Temporal interaction log with token-overlap semantic similarity recall.
3. HierarchicalMemoryAgent: Orchestrates retrieval -> grounded answering -> triple extraction -> persistent update.
"""

from __future__ import annotations

import collections
import json
import math
import re
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# LLM Invoker & JSON Extraction
# ---------------------------------------------------------------------------
DEFAULT_ENDPOINT = "http://127.0.0.1:12340/v1"
DEFAULT_MODEL = "qwen/qwen3.8-27b"


def call_llm(
    messages: List[Dict[str, str]],
    model: str = DEFAULT_MODEL,
    endpoint: str = DEFAULT_ENDPOINT,
    temperature: float = 0.2,
    max_tokens: int = 1500,
    timeout: float = 180.0,
) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{endpoint}/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        msg = res["choices"][0]["message"]
        content = msg.get("content") or ""
        return content.strip()


def parse_json_from_response(raw: str) -> Dict[str, Any]:
    raw = raw.strip()
    # 1. Try direct parse
    try:
        return json.loads(raw)
    except Exception:
        pass

    # 2. Try markdown fence extraction
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except Exception:
            pass

    # 3. Balanced brace parsing
    start_idx = raw.find("{")
    while start_idx != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start_idx, len(raw)):
            ch = raw[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = raw[start_idx : i + 1]
                        try:
                            return json.loads(candidate)
                        except Exception:
                            break
        start_idx = raw.find("{", start_idx + 1)

    raise ValueError(f"Failed to parse valid JSON from output:\n{raw}")


# ---------------------------------------------------------------------------
# 1. Graph Memory (Semantic World Model with N-hop Traversal)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Triple:
    subject: str
    predicate: str
    object: str

    def to_readable(self) -> str:
        return f"({self.subject}) --[{self.predicate}]--> ({self.object})"


def normalize_token(text: str) -> str:
    """Normalize entity or predicate names to prevent drift."""
    cleaned = re.sub(r"[^\w\s-]", "", text.strip().lower())
    return re.sub(r"[\s-]+", "_", cleaned)


class GraphMemory:
    """Native Python Directed Knowledge Graph with N-Hop Traversal."""

    def __init__(self):
        # subject -> list of (predicate, object)
        self.outgoing: Dict[str, List[Tuple[str, str]]] = collections.defaultdict(list)
        # object -> list of (predicate, subject)
        self.incoming: Dict[str, List[Tuple[str, str]]] = collections.defaultdict(list)
        # Set of raw canonical entity names
        self.nodes: Set[str] = set()

    def add_triple(self, subject: str, predicate: str, object_: str) -> bool:
        s = normalize_token(subject)
        p = normalize_token(predicate)
        o = normalize_token(object_)

        if not s or not p or not o:
            return False

        # Avoid duplicate edges
        if (p, o) in self.outgoing[s]:
            return False

        self.outgoing[s].append((p, o))
        self.incoming[o].append((p, s))
        self.nodes.add(s)
        self.nodes.add(o)
        return True

    def get_all_triples(self) -> List[Triple]:
        triples = []
        for s, edges in self.outgoing.items():
            for p, o in edges:
                triples.append(Triple(subject=s, predicate=p, object=o))
        return triples

    def find_matching_entities(self, query: str) -> List[str]:
        """Finds known graph nodes mentioned in the query."""
        query_norm = normalize_token(query)
        matches = []
        for node in self.nodes:
            # Check if node name appears as substring or token in query
            if node in query_norm or any(part in query_norm.split("_") for part in node.split("_")):
                matches.append(node)
        return sorted(matches, key=lambda n: len(n), reverse=True)

    def traverse_n_hop(self, start_nodes: List[str], max_depth: int = 3) -> List[Triple]:
        """Performs Breadth-First Search (BFS) to gather N-hop connected subgraph."""
        visited_nodes: Set[str] = set()
        visited_edges: Set[Tuple[str, str, str]] = set()
        queue: collections.deque[Tuple[str, int]] = collections.deque(
            (node, 0) for node in start_nodes if node in self.nodes
        )
        result_triples: List[Triple] = []

        while queue:
            curr_node, depth = queue.popleft()
            visited_nodes.add(curr_node)

            if depth >= max_depth:
                continue

            # Forward traversal: curr_node --[p]--> next_node
            for p, next_node in self.outgoing.get(curr_node, []):
                edge_id = (curr_node, p, next_node)
                if edge_id not in visited_edges:
                    visited_edges.add(edge_id)
                    result_triples.append(Triple(subject=curr_node, predicate=p, object=next_node))
                if next_node not in visited_nodes:
                    queue.append((next_node, depth + 1))

            # Backward traversal: prev_node --[p]--> curr_node
            for p, prev_node in self.incoming.get(curr_node, []):
                edge_id = (prev_node, p, curr_node)
                if edge_id not in visited_edges:
                    visited_edges.add(edge_id)
                    result_triples.append(Triple(subject=prev_node, predicate=p, object=curr_node))
                if prev_node not in visited_nodes:
                    queue.append((prev_node, depth + 1))

        return result_triples

    def to_dict(self) -> Dict[str, Any]:
        return {
            "triples": [
                {"subject": t.subject, "predicate": t.predicate, "object": t.object}
                for t in self.get_all_triples()
            ]
        }

    def from_dict(self, data: Dict[str, Any]) -> None:
        for t in data.get("triples", []):
            self.add_triple(t["subject"], t["predicate"], t["object"])


# ---------------------------------------------------------------------------
# 2. Episodic Memory (Narrative Event Log with Similarity Recall)
# ---------------------------------------------------------------------------
@dataclass
class EpisodicRecord:
    episode_id: int
    timestamp: float
    user_query: str
    agent_response: str
    tokens: Set[str] = field(default_factory=set)


class EpisodicMemory:
    """Native Python Episodic Memory Buffer with Token-Overlap Recall."""

    def __init__(self):
        self.episodes: List[EpisodicRecord] = []
        self._next_id = 1

    @staticmethod
    def _tokenize(text: str) -> Set[str]:
        words = re.findall(r"\w+", text.lower())
        # Filter basic trivial stop words
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to", "for", "of", "and", "or"}
        return {w for w in words if w not in stop_words and len(w) > 1}

    def add_episode(self, user_query: str, agent_response: str) -> EpisodicRecord:
        combined_text = f"{user_query} {agent_response}"
        tokens = self._tokenize(combined_text)
        rec = EpisodicRecord(
            episode_id=self._next_id,
            timestamp=time.time(),
            user_query=user_query,
            agent_response=agent_response,
            tokens=tokens,
        )
        self.episodes.append(rec)
        self._next_id += 1
        return rec

    def search_similar(self, query: str, top_k: int = 2) -> List[Tuple[EpisodicRecord, float]]:
        """Returns top_k past episodes by Jaccard / Token-overlap similarity score."""
        q_tokens = self._tokenize(query)
        if not q_tokens:
            return []

        scored = []
        for ep in self.episodes:
            overlap = len(q_tokens.intersection(ep.tokens))
            union = len(q_tokens.union(ep.tokens))
            score = (overlap / union) if union > 0 else 0.0
            if score > 0.05:  # Minimum relevance threshold
                scored.append((ep, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episodes": [
                {
                    "id": ep.episode_id,
                    "timestamp": ep.timestamp,
                    "query": ep.user_query,
                    "response": ep.agent_response,
                }
                for ep in self.episodes
            ]
        }

    def from_dict(self, data: Dict[str, Any]) -> None:
        for item in data.get("episodes", []):
            self.add_episode(item["query"], item["response"])


# ---------------------------------------------------------------------------
# 3. Hierarchical Dual-Memory Agent Orchestrator
# ---------------------------------------------------------------------------
class HierarchicalMemoryAgent:
    """Agent orchestrator combining Episodic logs and Semantic Graph knowledge."""

    def __init__(
        self,
        graph_memory: Optional[GraphMemory] = None,
        episodic_memory: Optional[EpisodicMemory] = None,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        llm_fn: Optional[Callable[..., str]] = None,
    ):
        self.graph = graph_memory or GraphMemory()
        self.episodic = episodic_memory or EpisodicMemory()
        self.model = model
        self.endpoint = endpoint
        self.llm_fn = llm_fn or call_llm

    def retrieve(self, query: str, max_depth: int = 3) -> Dict[str, Any]:
        """Dual-track retrieval: Semantic graph traversal + Episodic similarity."""
        # 1. Graph retrieval via entity match + N-hop BFS (default 3-hop for transitive relations)
        matched_entities = self.graph.find_matching_entities(query)
        graph_triples = self.graph.traverse_n_hop(matched_entities, max_depth=max_depth)

        # 2. Episodic retrieval via token similarity
        similar_episodes = self.episodic.search_similar(query, top_k=2)

        return {
            "matched_entities": matched_entities,
            "graph_triples": graph_triples,
            "episodic_records": [ep for ep, score in similar_episodes],
        }

    def answer(self, query: str, context: Dict[str, Any]) -> str:
        """Generates grounded answer conditioned strictly on retrieved memories."""
        # Format Graph Facts
        if context["graph_triples"]:
            graph_str = "\n".join(f"- {t.to_readable()}" for t in context["graph_triples"])
        else:
            graph_str = "(No directly connected graph facts found)"

        # Format Episodic Past Conversations
        if context["episodic_records"]:
            episodic_str = "\n\n".join(
                f"[Past Episode #{ep.episode_id}]:\nUser: {ep.user_query}\nAssistant: {ep.agent_response}"
                for ep in context["episodic_records"]
            )
        else:
            episodic_str = "(No relevant past conversation episodes found)"

        grounded_prompt = (
            f"You are an assistant with persistent hierarchical memory.\n\n"
            f"## Retrieved Knowledge Graph Facts (Semantic Memory)\n{graph_str}\n\n"
            f"## Retrieved Past Conversation Logs (Episodic Memory)\n{episodic_str}\n\n"
            f"## User Message\n{query}\n\n"
            f"## Answering Instructions\n"
            f"1. If the user is sharing or introducing new information/facts, acknowledge and summarize it concisely.\n"
            f"2. If the user is asking a question, answer based STRICTLY on the retrieved memory and perform necessary multi-hop graph deductions.\n"
            f"3. If a question is asked and the required facts are completely absent from memory, state clearly: 'I do not have memory of this.'\n"
            f"4. Be clear, concise, and professional."
        )

        return self.llm_fn(
            messages=[
                {"role": "system", "content": "You are an AI assistant strictly guided by persistent memory."},
                {"role": "user", "content": grounded_prompt},
            ],
            model=self.model,
            endpoint=self.endpoint,
            max_tokens=1500,
        )

    def extract_and_ingest_triples(self, user_query: str, agent_response: str) -> List[Triple]:
        """Extracts atomic (subject, predicate, object) triples from the exchange and updates graph."""
        extract_prompt = (
            f"Extract concrete, atomic factual triples (subject, predicate, object) from the conversation below.\n\n"
            f"User: {user_query}\n"
            f"Assistant: {agent_response}\n\n"
            f"## Extraction Rules\n"
            f"- Subject and Object must be specific named entities or concrete terms (lowercase with underscores).\n"
            f"- Predicate must be a concise relation verb (e.g. works_at, develops, manages, reviewed_by, depends_on).\n"
            f"- Skip generic filler (e.g. do not extract 'user says hello').\n"
            f"- Output strictly a JSON object formatted as:\n"
            f"{{\n"
            f'  "triples": [\n'
            f'    {{"subject": "entity1", "predicate": "relation", "object": "entity2"}}\n'
            f"  ]\n"
            f"}}"
        )

        raw_output = self.llm_fn(
            messages=[
                {"role": "system", "content": "You are an expert knowledge graph engineer. Output strict JSON only."},
                {"role": "user", "content": extract_prompt},
            ],
            model=self.model,
            endpoint=self.endpoint,
            max_tokens=1000,
        )

        new_triples: List[Triple] = []
        try:
            parsed = parse_json_from_response(raw_output)
            for item in parsed.get("triples", []):
                s = item.get("subject", "")
                p = item.get("predicate", "")
                o = item.get("object", "")
                if s and p and o:
                    added = self.graph.add_triple(s, p, o)
                    if added:
                        new_triples.append(Triple(subject=normalize_token(s), predicate=normalize_token(p), object=normalize_token(o)))
        except Exception as e:
            print(f"[Warning] Failed to parse triples from raw output: {e}\nRaw output preview: {raw_output[:200]}")

        return new_triples

    def chat(self, user_query: str) -> Dict[str, Any]:
        """Executes full interaction cycle: retrieve -> answer -> extract -> persist."""
        # 1. Retrieve
        context = self.retrieve(user_query)

        # 2. Answer
        answer_text = self.answer(user_query, context)

        # 3. Extract & Ingest into Semantic Graph
        new_triples = self.extract_and_ingest_triples(user_query, answer_text)

        # 4. Ingest into Episodic Memory
        episode = self.episodic.add_episode(user_query, answer_text)

        return {
            "query": user_query,
            "answer": answer_text,
            "retrieved_entities": context["matched_entities"],
            "retrieved_triples": context["graph_triples"],
            "retrieved_episodes": [e.episode_id for e in context["episodic_records"]],
            "new_triples": new_triples,
            "episode_id": episode.episode_id,
        }
