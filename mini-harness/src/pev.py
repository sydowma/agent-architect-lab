"""
mini-harness Phase 2: PEV (Plan-Execute-Verify) 规划解耦与单步验收架构引擎

核心组件:
1. Planner: 将长链任务分解为具备明确验收条件的步骤清单 (PlanStep DAG)
2. Executor: 基于现有安全工具链 (tools.py) 执行具体单步子任务
3. Verifier: 独立质量门禁，依据 expected_outcome 进行单步客观验证 (PASS/FAIL)
4. Controller: 控制单步就地重试与确定性线性推进，杜绝雪崩效应
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

from tools import default_registry


def load_env_file():
    candidates = [
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
        os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
    ]
    for env_path in candidates:
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
            break


load_env_file()


class PlanStep:
    def __init__(self, step_id: int, title: str, instruction: str, expected_outcome: str):
        self.step_id = step_id
        self.title = title
        self.instruction = instruction
        self.expected_outcome = expected_outcome
        self.status = "PENDING"  # PENDING, IN_PROGRESS, COMPLETED, FAILED
        self.result = ""
        self.retries = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "instruction": self.instruction,
            "expected_outcome": self.expected_outcome,
            "status": self.status,
            "result_preview": self.result[:100],
            "retries": self.retries
        }


class PEVAgent:
    def __init__(self, max_retries_per_step: int = 2, temperature: float = 0.1):
        self.api_key = os.getenv("OPENAI_API_KEY", "lm-studio")
        self.base_url = os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:12340/v1")
        self.model = os.getenv("OPENAI_MODEL", "qwen/qwen3.8-27b")
        self.registry = default_registry
        self.max_retries_per_step = max_retries_per_step
        self.temperature = temperature

    def _call_llm_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """向 LLM 发送请求并提取稳健 JSON"""
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": self.temperature,
            "max_tokens": 2048
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            content = res["choices"][0]["message"].get("content", "")
            if not content.strip() and res["choices"][0]["message"].get("reasoning_content"):
                content = res["choices"][0]["message"]["reasoning_content"]

        cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"```$", "", cleaned.strip(), flags=re.MULTILINE).strip()

        # 提取第一个合法 JSON 块
        match = re.search(r"(\{|\[)[\s\S]*(\}|\])", cleaned)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
        return {}

    def _call_tool_step(self, instruction: str, verified_context: str) -> str:
        """为特定 Step 调度工具执行"""
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        sys_prompt = (
            "You are an expert step-executor. You have access to tools for filesystem, shell, and calculation.\n"
            "Your job is to execute the given step instruction using the appropriate tool.\n"
            "If prior context is provided, use it directly."
        )
        user_msg = (
            f"【前序已验收事实背景】:\n{verified_context}\n\n"
            f"【当前子任务指令】:\n{instruction}\n\n"
            "请调用适当工具执行本任务并直接给出动作。"
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_msg}
            ],
            "tools": self.registry.get_schemas(),
            "tool_choice": "auto",
            "temperature": self.temperature
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            msg = res["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            return msg.get("content", "").strip() or "(Executed with text output)"

        # 派发工具执行
        outputs = []
        for call in tool_calls:
            fn = call["function"]["name"]
            args = call["function"]["arguments"]
            print(f"      🛠️ [Tool Call]: {fn}(args={args[:80]}...)")
            out = self.registry.dispatch(fn, args)
            outputs.append(f"[{fn} Output]: {out}")

        return "\n".join(outputs)

    def plan(self, task: str) -> List[PlanStep]:
        """Planner: 将任务分解为带验收准则的有序步骤清单"""
        sys_prompt = (
            "You are a Principal Software Architect and Master Planner.\n"
            "Decompose the user task into a minimal, sequential plan of 2 to 4 concrete steps.\n"
            "Each step must have an explicit, unambiguous 'expected_outcome' that can be objectively verified.\n"
            "Output strictly a JSON array matching:\n"
            "[\n"
            "  {\n"
            '    "step_id": 1,\n'
            '    "title": "short title",\n'
            '    "instruction": "actionable instruction",\n'
            '    "expected_outcome": "what precise data/file/state must be achieved to pass"\n'
            "  }\n"
            "]"
        )
        res = self._call_llm_json(sys_prompt, task)
        steps = []
        if isinstance(res, list):
            for item in res:
                steps.append(PlanStep(
                    step_id=int(item.get("step_id", len(steps)+1)),
                    title=str(item.get("title", f"Step {len(steps)+1}")),
                    instruction=str(item.get("instruction", "")),
                    expected_outcome=str(item.get("expected_outcome", ""))
                ))
        if not steps:
            # 基础降级计划
            steps = [
                PlanStep(1, "执行核心任务", task, "任务结果完成且非空")
            ]
        return steps

    def verify(self, step: PlanStep, execution_output: str) -> Tuple[bool, str]:
        """Verifier: 质量门禁，对照 expected_outcome 进行客观审查"""
        sys_prompt = (
            "You are an uncompromising Quality Assurance and Verification Judge.\n"
            "Compare the step's 'expected_outcome' against the actual 'execution_output'.\n"
            "Determine strictly whether the step PASSED or FAILED.\n"
            "Output strictly a JSON object matching:\n"
            '{"passed": true/false, "critique": "<reason if failed, or verification note if passed>"}'
        )
        user_msg = (
            f"【子任务指令】: {step.instruction}\n"
            f"【验收预期 (Expected Outcome)】: {step.expected_outcome}\n"
            f"【实际执行输出 (Execution Output)】:\n{execution_output}\n"
        )
        res = self._call_llm_json(sys_prompt, user_msg)
        passed = bool(res.get("passed", False))
        critique = str(res.get("critique", "Verification evaluated."))
        return passed, critique

    def run(self, task: str) -> Dict[str, Any]:
        """PEV 全流程执行调度器"""
        print("\n" + "=" * 68)
        print(" 🧠 mini-harness Phase 2: PEV (Plan-Execute-Verify) 确定性引擎")
        print(f" 🎯 目标任务: {task[:70]}...")
        print("=" * 68)

        # 1. 规划阶段 (Plan)
        print("\n📋 [Phase 1: Planning] 正在分解任务结构与验收标准...")
        plan = self.plan(task)
        print(f"💡 [生成计划清单 ({len(plan)} 步骤)]:")
        for s in plan:
            print(f"   [{s.step_id}] {s.title}")
            print(f"       👉 指令: {s.instruction[:60]}...")
            print(f"       🎯 验收条件: {s.expected_outcome[:60]}...")

        # 2. 执行与单步验证推进阶段 (Execute & Verify)
        verified_context = ""
        overall_success = True

        for step in plan:
            step.status = "IN_PROGRESS"
            step_passed = False
            print(f"\n🚀 [Phase 2: Step {step.step_id}/{len(plan)}] 开始推进: {step.title}")

            while step.retries <= self.max_retries_per_step:
                if step.retries > 0:
                    print(f"   🔄 [Step Retry {step.retries}/{self.max_retries_per_step}] 带批评意见就地重试...")

                # 2.1 执行当前单步
                exec_output = self._call_tool_step(step.instruction, verified_context)
                step.result = exec_output
                preview = exec_output[:120].replace("\n", " ")
                print(f"   📥 [Execution Output]: {preview}{'...' if len(exec_output) > 120 else ''}")

                # 2.2 单步质量门禁验收 (Verify)
                print(f"   ⚖️  [Verifier Gate]: 正在依据预期进行验收...")
                passed, critique = self.verify(step, exec_output)

                if passed:
                    step.status = "COMPLETED"
                    step_passed = True
                    print(f"   ✅ [VERIFY PASS]: {critique}")
                    # 将该步骤的干净产物汇入背景上下文
                    verified_context += f"\n[Step {step.step_id} - {step.title} 成果]:\n{exec_output}\n"
                    break
                else:
                    print(f"   ❌ [VERIFY FAIL]: {critique}")
                    step.retries += 1
                    step.instruction += f"\n[上一轮尝试失败原因]: {critique}，请修正并满足验收条件。"

            if not step_passed:
                step.status = "FAILED"
                overall_success = False
                print(f"\n⛔ [Step Breaker]: 步骤 {step.step_id} 耗尽重试次数未达成验收条件，终止推进以防雪崩！")
                break

        # 3. 结果汇编
        print("\n" + "=" * 68)
        status_label = "🏆 全部步骤验证通过 (PASS)" if overall_success else "❌ 任务在部分步骤受阻中断 (FAILED)"
        print(f"🏁 [PEV 执行结果]: {status_label}")
        print("=" * 68)

        return {
            "task": task,
            "success": overall_success,
            "plan": [s.to_dict() for s in plan],
            "verified_context": verified_context
        }


if __name__ == "__main__":
    agent = PEVAgent()
    sample_task = "检查 mini-harness/data 目录下的文件并提取已有事实，然后计算事实总数并给出总结。"
    res = agent.run(sample_task)
    print("\n最终产物:\n", res["verified_context"])
