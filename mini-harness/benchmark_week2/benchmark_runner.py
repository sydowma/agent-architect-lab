"""Week 2 Capstone Benchmark Runner: Direct vs ReAct vs Reflection vs PEV.

Evaluates 4 architectures on the same real-world task:
Refactoring `buggy_cache.py` (which has 4 concurrency/memory/isolation flaws)
to pass 100% of deterministic physical tests in `test_suite.py`.

Optimized for lightweight local models (e.g. google/gemma-4-12b-qat) with:
- Execution environment pre-injected standard library namespaces (threading, copy, time)
- Concise critic and gatekeeper prompts
- Full trace and error collection into benchmark_results.json
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import sys
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple, Type

# Add benchmark directory and mini-harness/src to path
BENCHMARK_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(os.path.dirname(BENCHMARK_DIR), "src")
sys.path.insert(0, BENCHMARK_DIR)
sys.path.insert(0, SRC_DIR)

from test_suite import run_tests_on_cache_class

DEFAULT_ENDPOINT = os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:12340/v1")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "google/gemma-4-12b-qat")


# =====================================================================
# 1. Utility Functions
# =====================================================================

def call_llm(
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 3000,
    tools: Optional[List[Dict[str, Any]]] = None
) -> Tuple[str, Optional[List[Dict[str, Any]]], int]:
    """Calls the local LLM endpoint and returns (content, tool_calls, tokens_used)."""
    url = f"{DEFAULT_ENDPOINT.rstrip('/')}/chat/completions"
    payload: Dict[str, Any] = {
        "model": DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    if tools:
        payload["tools"] = tools

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer lm-studio"},
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=180) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        choice = res["choices"][0]
        msg = choice.get("message", {})
        content = msg.get("content") or ""
        # If content is empty but model emitted reasoning_content
        if not content.strip() and msg.get("reasoning_content"):
            content = msg.get("reasoning_content")
        tool_calls = msg.get("tool_calls")
        tokens = res.get("usage", {}).get("total_tokens", 0)
        return content, tool_calls, tokens


def extract_python_code(raw_text: str) -> str:
    """Extracts Python code from markdown blocks or raw text."""
    matches = re.findall(r"```(?:python)?\s*([\s\S]*?)```", raw_text, flags=re.MULTILINE)
    if matches:
        for block in matches:
            if "class InMemoryCache" in block:
                return block.strip()
        return matches[-1].strip()

    if "class InMemoryCache" in raw_text:
        idx = raw_text.find("class InMemoryCache")
        return raw_text[idx:].strip()

    return raw_text.strip()


def load_cache_class_from_code(code_str: str) -> Type:
    """Dynamically compiles code string with standard engineering namespaces."""
    scope: Dict[str, Any] = {
        "time": time,
        "threading": threading,
        "copy": copy,
        "Any": Any,
        "Optional": Optional,
        "Dict": Dict,
        "List": List,
        "Tuple": Tuple,
        "Type": Type
    }
    exec(code_str, scope)
    if "InMemoryCache" not in scope:
        raise ValueError("InMemoryCache class not found in compiled code")
    return scope["InMemoryCache"]


def evaluate_code(code_str: str) -> Dict[str, Any]:
    """Runs test_suite on code and returns pass/fail metrics and errors."""
    try:
        cls = load_cache_class_from_code(code_str)
        test_results = run_tests_on_cache_class(cls)
        passed_count = sum(1 for v in test_results.values() if v)
        return {
            "valid_syntax": True,
            "tests": test_results,
            "passed": passed_count,
            "total": len(test_results),
            "pass_rate": round(passed_count / len(test_results), 4),
            "error": None
        }
    except Exception as e:
        return {
            "valid_syntax": False,
            "tests": {},
            "passed": 0,
            "total": 5,
            "pass_rate": 0.0,
            "error": str(e)
        }


# =====================================================================
# 2. Architecture Runners
# =====================================================================

class ArchitectureBenchmark:
    def __init__(self, buggy_code: str):
        self.buggy_code = buggy_code

    # -----------------------------------------------------------------
    # Group A: Direct (Single-Shot Generation)
    # -----------------------------------------------------------------
    def run_direct(self) -> Dict[str, Any]:
        print("\n" + "="*50)
        print("▶ Running Architecture A: Direct (Single-Shot Generation)")
        print("="*50)
        start_time = time.time()

        system_prompt = (
            "You are an expert Python systems architect.\n"
            "Refactor a buggy in-memory cache into a thread-safe, leak-free implementation.\n"
            "Output ONLY the complete Python code inside a ```python ``` block."
        )
        user_prompt = (
            f"Here is the buggy implementation:\n\n```python\n{self.buggy_code}\n```\n\n"
            "Requirements for refactoring:\n"
            "1. Concurrency safety: use threading.RLock on all methods (set, get, delete, size, cleanup_expired).\n"
            "2. Active cleanup: provide `cleanup_expired(self) -> int` to actively remove all keys where expire_at is not None and expire_at <= time.time().\n"
            "3. Mutable isolation: apply copy.deepcopy when storing in set() AND when retrieving in get().\n"
            "4. Robust TTL: if ttl is not None and ttl <= 0, do not store or expire immediately.\n"
            "5. Preserve API: __init__, set(key, value, ttl=None), get(key), delete(key), size(), cleanup_expired().\n\n"
            "Provide the complete code for `InMemoryCache` in a ```python ``` block."
        )

        content, _, tokens = call_llm(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1
        )
        elapsed = time.time() - start_time
        code = extract_python_code(content)
        eval_res = evaluate_code(code)

        print(f"Direct Finished in {elapsed:.2f}s | Pass: {eval_res['passed']}/{eval_res['total']} ({eval_res['pass_rate']*100:.0f}%) | Err: {eval_res['error']}")
        return {
            "name": "Direct (Single-Shot)",
            "llm_calls": 1,
            "total_tokens": tokens,
            "elapsed_seconds": round(elapsed, 2),
            "eval": eval_res,
            "code": code
        }

    # -----------------------------------------------------------------
    # Group B: ReAct (Tool-Calling Autonomous Loop)
    # -----------------------------------------------------------------
    def run_react(self, max_steps: int = 4) -> Dict[str, Any]:
        print("\n" + "="*50)
        print("▶ Running Architecture B: ReAct (Autonomous Tool-Using Loop)")
        print("="*50)
        start_time = time.time()

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_current_code",
                    "description": "Reads the current cache code.",
                    "parameters": {"type": "object", "properties": {}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "update_cache_code",
                    "description": "Updates the cache implementation with the provided Python code string.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "description": "The complete Python code for InMemoryCache"}
                        },
                        "required": ["code"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "run_physical_tests",
                    "description": "Runs the physical test suite on the current cache code and returns detailed pass/fail status and errors.",
                    "parameters": {"type": "object", "properties": {}}
                }
            }
        ]

        current_code = self.buggy_code
        system_prompt = (
            "You are an autonomous ReAct engineering agent.\n"
            "Objective: fix InMemoryCache so ALL 5 physical tests pass.\n"
            "Tools available: read_current_code, update_cache_code, run_physical_tests.\n"
            "Crucial fixes needed:\n"
            "1. threading.RLock on all methods\n"
            "2. cleanup_expired() method that deletes expired keys\n"
            "3. copy.deepcopy in BOTH set() and get()"
        )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Please refactor InMemoryCache to pass all 5 physical tests.\n"
                    "First inspect current status, update the code with update_cache_code, and test with run_physical_tests."
                )
            }
        ]

        llm_calls = 0
        total_tokens = 0
        step = 0
        eval_res = evaluate_code(current_code)

        while step < max_steps and eval_res["passed"] < 5:
            step += 1
            print(f"  [ReAct Step {step}] Requesting LLM...")
            content, tool_calls, tokens = call_llm(messages, tools=tools, temperature=0.1)
            llm_calls += 1
            total_tokens += tokens

            if not tool_calls:
                # If model emitted code in text directly
                extracted = extract_python_code(content)
                if "class InMemoryCache" in extracted:
                    current_code = extracted
                    eval_res = evaluate_code(current_code)
                    print(f"  [ReAct Step {step}] Model emitted code directly. Pass: {eval_res['passed']}/5")
                    if eval_res["passed"] == 5:
                        break
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": "Please call update_cache_code or run_physical_tests."})
                continue

            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls
            })

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name")
                args_str = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except Exception:
                    args = {}

                tc_id = tc.get("id", f"call_{step}")
                obs = ""

                if name == "read_current_code":
                    obs = current_code
                    print(f"  [Tool Call] read_current_code")
                elif name == "update_cache_code":
                    new_code = extract_python_code(args.get("code", ""))
                    if new_code:
                        current_code = new_code
                        eval_res = evaluate_code(current_code)
                        obs = f"Code updated. Syntax valid: {eval_res['valid_syntax']}, Tests passed: {eval_res['passed']}/5: {eval_res['tests']}"
                        print(f"  [Tool Call] update_cache_code -> {len(current_code)} bytes | Pass: {eval_res['passed']}/5")
                    else:
                        obs = "Error: empty code provided"
                elif name == "run_physical_tests":
                    eval_res = evaluate_code(current_code)
                    obs = json.dumps({
                        "valid_syntax": eval_res["valid_syntax"],
                        "passed": eval_res["passed"],
                        "total": eval_res["total"],
                        "tests": eval_res["tests"],
                        "error": eval_res["error"]
                    }, indent=2)
                    print(f"  [Tool Call] run_physical_tests -> {eval_res['passed']}/5 passed: {eval_res['tests']}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": name,
                    "content": obs
                })

            if eval_res["passed"] == 5:
                print("  [ReAct] All 5 tests passed! Ending ReAct loop.")
                break

        elapsed = time.time() - start_time
        print(f"ReAct Finished in {elapsed:.2f}s ({llm_calls} calls) | Pass: {eval_res['passed']}/{eval_res['total']} ({eval_res['pass_rate']*100:.0f}%)")
        return {
            "name": "ReAct (Autonomous Loop)",
            "llm_calls": llm_calls,
            "total_tokens": total_tokens,
            "elapsed_seconds": round(elapsed, 2),
            "eval": eval_res,
            "code": current_code
        }

    # -----------------------------------------------------------------
    # Group C: Reflection (Generator -> Critic -> Refiner)
    # -----------------------------------------------------------------
    def run_reflection(self) -> Dict[str, Any]:
        print("\n" + "="*50)
        print("▶ Running Architecture C: Reflection (Generator -> Critic -> Refiner)")
        print("="*50)
        start_time = time.time()
        llm_calls = 0
        total_tokens = 0

        # Phase 1: Generator creates initial draft
        print("  [Phase 1] Generator: Creating initial draft...")
        gen_sys = "You are a Python systems engineer. Refactor InMemoryCache into a thread-safe cache."
        gen_prompt = (
            f"Buggy code:\n```python\n{self.buggy_code}\n```\n"
            "Requirements:\n"
            "- Thread-safe with threading.RLock()\n"
            "- Active cleanup_expired() method\n"
            "- Deepcopy on store (set) and retrieve (get)\n"
            "Provide complete code in ```python ``` block."
        )
        draft_content, _, tokens = call_llm(
            [{"role": "system", "content": gen_sys}, {"role": "user", "content": gen_prompt}],
            temperature=0.2
        )
        llm_calls += 1
        total_tokens += tokens
        draft_code = extract_python_code(draft_content)
        draft_eval = evaluate_code(draft_code)
        print(f"  [Generator Draft] Pass: {draft_eval['passed']}/5: {draft_eval['tests']}")

        # Phase 2: Critic conducts concise code review (max 3 bullets)
        print("  [Phase 2] Critic: Auditing draft against edge-case rubric...")
        critic_sys = (
            "You are a strict code reviewer auditing InMemoryCache.\n"
            "Rubric checklist:\n"
            "1. Concurrency: Does every method acquire self._lock = threading.RLock()?\n"
            "2. Active Scavenger: Does cleanup_expired() actively remove expired keys?\n"
            "3. Mutable Isolation: Is copy.deepcopy used on BOTH set AND get?\n"
            "Be concise. List at most 3 concise bullet points of concrete flaws to fix."
        )
        critic_prompt = f"Review this draft:\n```python\n{draft_code}\n```\nConcise audit bullets:"
        critique, _, tokens = call_llm(
            [{"role": "system", "content": critic_sys}, {"role": "user", "content": critic_prompt}],
            temperature=0.1
        )
        llm_calls += 1
        total_tokens += tokens
        print(f"  [Critic Review Completed] Review length: {len(critique)} chars")

        # Phase 3: Refiner incorporates critique into final production code
        print("  [Phase 3] Refiner: Synthesizing critique into finalized implementation...")
        refiner_sys = (
            "You are a master Python engineer. Rewrite InMemoryCache fixing all issues identified by Critic.\n"
            "Ensure:\n"
            "1. threading.RLock on ALL methods\n"
            "2. cleanup_expired() actively deletes expired items\n"
            "3. copy.deepcopy on BOTH set() and get()\n"
            "Output ONLY the complete Python code in ```python ``` block."
        )
        refiner_prompt = (
            f"Original Draft:\n```python\n{draft_code}\n```\n\n"
            f"Critic Audit:\n{critique}\n\n"
            "Output the perfected code:"
        )
        final_content, _, tokens = call_llm(
            [{"role": "system", "content": refiner_sys}, {"role": "user", "content": refiner_prompt}],
            temperature=0.1
        )
        llm_calls += 1
        total_tokens += tokens
        final_code = extract_python_code(final_content)
        eval_res = evaluate_code(final_code)

        elapsed = time.time() - start_time
        print(f"Reflection Finished in {elapsed:.2f}s ({llm_calls} calls) | Pass: {eval_res['passed']}/{eval_res['total']} ({eval_res['pass_rate']*100:.0f}%)")
        return {
            "name": "Reflection (Dual-Role Loop)",
            "llm_calls": llm_calls,
            "total_tokens": total_tokens,
            "elapsed_seconds": round(elapsed, 2),
            "eval": eval_res,
            "draft_eval": draft_eval,
            "code": final_code
        }

    # -----------------------------------------------------------------
    # Group D: PEV (Plan-Execute-Verify Gatekeeper)
    # -----------------------------------------------------------------
    def run_pev(self) -> Dict[str, Any]:
        print("\n" + "="*50)
        print("▶ Running Architecture D: PEV (Plan -> Execute -> Physical Gate Verify)")
        print("="*50)
        start_time = time.time()
        llm_calls = 0
        total_tokens = 0

        # Step 1: Planner breaks down the engineering plan into deterministic steps
        print("  [Step 1: Planner] Formulating modular refactoring DAG...")
        planner_sys = "You are a software architect. Decompose the refactoring of buggy_cache.py into 3 concise steps."
        planner_prompt = (
            f"Target buggy cache:\n```python\n{self.buggy_code}\n```\n"
            "Decompose into 3 linear steps:\n"
            "Step 1: Thread safety (RLock across all methods)\n"
            "Step 2: Active cleanup scavenger (cleanup_expired method)\n"
            "Step 3: Mutable isolation (copy.deepcopy on set and get)\n"
            "Output the plan briefly."
        )
        plan, _, tokens = call_llm(
            [{"role": "system", "content": planner_sys}, {"role": "user", "content": planner_prompt}],
            temperature=0.1
        )
        llm_calls += 1
        total_tokens += tokens
        print(f"  [Plan Generated] 3 Steps defined.")

        # Step 2: Executor generates code addressing all planned requirements
        print("  [Step 2: Executor] Implementing plan with modular safeguards...")
        exec_sys = (
            "You are a senior systems engineer executing a refactoring plan.\n"
            "Implement InMemoryCache with:\n"
            "1. threading.RLock protecting every operation\n"
            "2. cleanup_expired() actively scanning and deleting keys where expire_at <= time.time()\n"
            "3. copy.deepcopy on both store (set) and retrieve (get)\n"
            "Output the complete Python code in ```python ```."
        )
        exec_prompt = f"Plan:\n{plan}\n\nBuggy Code:\n```python\n{self.buggy_code}\n```\nExecute and provide code."
        code_content, _, tokens = call_llm(
            [{"role": "system", "content": exec_sys}, {"role": "user", "content": exec_prompt}],
            temperature=0.1
        )
        llm_calls += 1
        total_tokens += tokens
        current_code = extract_python_code(code_content)

        # Step 3: Verifier runs deterministic physical gatekeeper
        print("  [Step 3: Physical Verifier Gate] Evaluating against test suite...")
        eval_res = evaluate_code(current_code)
        print(f"  [Verifier Gate 1] Passed {eval_res['passed']}/5: {eval_res['tests']}")

        # If any test failed, trigger Verifier-guided Self-Correction
        if eval_res["passed"] < 5:
            print(f"  [Verifier Gate] Tests failed ({eval_res['passed']}/5). Initiating Gatekeeper Repair...")
            failed_tests = [k for k, v in eval_res["tests"].items() if not v]
            repair_sys = "You are an automated gatekeeper repair engineer. Fix the specific test failures."
            repair_prompt = (
                f"The physical test gate failed on: {failed_tests}\n"
                f"Current Code:\n```python\n{current_code}\n```\n"
                "Requirements to fix:\n"
                "- If test_active_cleanup_scavenger failed: ensure cleanup_expired() is present and removes expired keys.\n"
                "- If test_mutable_object_isolation failed: ensure copy.deepcopy is called in BOTH set() AND get().\n"
                "- If test_concurrent_multithreading_safety failed: wrap all operations in `with self._lock:`.\n"
                "Output the corrected code in ```python ```."
            )
            repair_content, _, tokens = call_llm(
                [{"role": "system", "content": repair_sys}, {"role": "user", "content": repair_prompt}],
                temperature=0.1
            )
            llm_calls += 1
            total_tokens += tokens
            current_code = extract_python_code(repair_content)
            eval_res = evaluate_code(current_code)
            print(f"  [Verifier Gate 2 (Post-Repair)] Passed {eval_res['passed']}/5: {eval_res['tests']}")

        elapsed = time.time() - start_time
        print(f"PEV Finished in {elapsed:.2f}s ({llm_calls} calls) | Pass: {eval_res['passed']}/{eval_res['total']} ({eval_res['pass_rate']*100:.0f}%)")
        return {
            "name": "PEV (Plan-Execute-Verify Gate)",
            "llm_calls": llm_calls,
            "total_tokens": total_tokens,
            "elapsed_seconds": round(elapsed, 2),
            "eval": eval_res,
            "code": current_code
        }


# =====================================================================
# 3. Main Benchmark Orchestrator
# =====================================================================

def main():
    print("="*70)
    print(" 🚀 AGENT ARCHITECTURE COMPREHENSIVE BENCHMARK (WEEK 2 CAPSTONE)")
    print(" Direct vs ReAct vs Reflection vs PEV on Production Buggy Cache")
    print(f" Backend Model: {DEFAULT_MODEL} @ {DEFAULT_ENDPOINT}")
    print("="*70)

    # 1. Verify Original Buggy Cache
    with open(os.path.join(BENCHMARK_DIR, "buggy_cache.py"), "r", encoding="utf-8") as f:
        buggy_code = f.read()

    baseline_eval = evaluate_code(buggy_code)
    print(f"\n[Baseline Verification] Original buggy_cache.py pass rate: {baseline_eval['passed']}/5 ({baseline_eval['pass_rate']*100:.0f}%)")
    for t_name, passed in baseline_eval["tests"].items():
        st = "✅ PASS" if passed else "❌ FAIL (Expected)"
        print(f"  - {t_name}: {st}")

    benchmark = ArchitectureBenchmark(buggy_code)
    results = []

    # Run all 4 architectures
    results.append(benchmark.run_direct())
    results.append(benchmark.run_react())
    results.append(benchmark.run_reflection())
    results.append(benchmark.run_pev())

    # Format comparative summary
    print("\n" + "="*75)
    print(" 🏆 FINAL COMPREHENSIVE BENCHMARK RESULTS")
    print("="*75)
    print(f"{'Architecture':<32} | {'Pass Rate':<10} | {'Latency':<8} | {'Calls':<6} | {'Status':<10}")
    print("-" * 75)

    summary_data = []
    for r in results:
        ev = r["eval"]
        pass_str = f"{ev['passed']}/5 ({ev['pass_rate']*100:.0f}%)"
        lat_str = f"{r['elapsed_seconds']}s"
        status = "🏅 PERFECT" if ev["passed"] == 5 else ("⚠️ PARTIAL" if ev["passed"] >= 3 else "❌ FAILED")
        print(f"{r['name']:<32} | {pass_str:<10} | {lat_str:<8} | {r['llm_calls']:<6} | {status:<10}")

        summary_data.append({
            "architecture": r["name"],
            "passed": ev["passed"],
            "total": ev["total"],
            "pass_rate": ev["pass_rate"],
            "elapsed_seconds": r["elapsed_seconds"],
            "llm_calls": r["llm_calls"],
            "total_tokens": r["total_tokens"],
            "tests_breakdown": ev["tests"],
            "error": ev.get("error"),
            "code_snippet": r["code"][:300] if r.get("code") else ""
        })

    # Save to JSON
    output_json_path = os.path.join(BENCHMARK_DIR, "benchmark_results.json")
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "model": DEFAULT_MODEL,
            "baseline": baseline_eval,
            "results": summary_data
        }, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Detailed benchmark results saved to: {output_json_path}")


if __name__ == "__main__":
    main()
