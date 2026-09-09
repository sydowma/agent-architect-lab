"""
mini-harness Phase 2: Reflection (自我反思与生成-评审对抗引擎)

特性:
1. Generator (作者) vs Critic-as-Judge (法官) 双角色认知对抗
2. 结构化评估细则 (Rubric): 覆盖正确性、边界防御、并发安全与性能
3. 严格 JSON 格式化评分与缺陷提取 (Score 0-100)
4. 多稿自主迭代精炼循环 (Draft -> Critique -> Refine)
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple


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


DEFAULT_RUBRIC = """
【评估维度与评分标准 (总分 100 分)】:
1. 正确性与数学/逻辑完备性 (Correctness, 30 分):
   - 核心算法逻辑是否绝对正确，有无隐藏的逻辑死角或未定义行为 (UB)。
2. 边界条件防御与鲁棒性 (Edge-Case Defense, 30 分):
   - 是否严格防御除以零、空输入、极大极小值、溢出 (Overflow) 或非法参数。
3. 性能与系统级安全 (Performance & Safety, 25 分):
   - 内存管理、缓存行对齐 (alignas)、锁/原子变量使用、避免伪共享与不必要开销。
4. 代码风格与可维护性 (Readability, 15 分):
   - 命名清晰度、关键并发逻辑与假设注释、接口易用性。

评分要求:
- 优秀 (>= 85 分): 具备工业级生产标准，无明显漏洞。
- 及格 (60-84 分): 基本逻辑可用，但缺乏关键边界防御或性能隐患。
- 不合格 (< 60 分): 存在严重逻辑错误或易崩溃陷阱。
"""


class CritiqueResult:
    def __init__(self, score: int, flaws: List[str], suggestions: List[str], summary: str):
        self.score = score
        self.flaws = flaws
        self.suggestions = suggestions
        self.summary = summary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "flaws": self.flaws,
            "suggestions": self.suggestions,
            "summary": self.summary
        }


class ReflectionAgent:
    """
    原生 Reflection 智能体
    通过 Generator 与 Critic 的结构化博弈，将草稿不断精炼至工业级质量
    """
    def __init__(
        self,
        target_score: int = 85,
        max_iterations: int = 3,
        temperature: float = 0.2
    ):
        self.api_key = os.getenv("OPENAI_API_KEY", "lm-studio")
        self.base_url = os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:12340/v1")
        self.model = os.getenv("OPENAI_MODEL", "qwen/qwen3.8-27b")
        self.target_score = target_score
        self.max_iterations = max_iterations
        self.temperature = temperature

    def _call_llm(self, system_prompt: str, user_prompt: str, temperature: Optional[float] = None) -> str:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": temperature or self.temperature,
            "max_tokens": 4096
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        for attempt in range(1, 4):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=180) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    msg = res["choices"][0]["message"]
                    content = msg.get("content", "") or ""
                    # 容错：若 content 为空但 reasoning_content 中包含代码块，自动兜底提取
                    if not content.strip() and msg.get("reasoning_content"):
                        reasoning = msg["reasoning_content"]
                        code_matches = re.findall(r"```(?:python)?\s*([\s\S]*?)```", reasoning)
                        if code_matches:
                            content = code_matches[-1]
                    return content.strip()
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < 3:
                    time.sleep(2 * attempt)
                else:
                    raise e

    def generate_draft(
        self,
        task: str,
        iteration: int,
        previous_draft: str = "",
        critique: Optional[CritiqueResult] = None
    ) -> str:
        """Generator 角色：负责初稿创作或吸取批评意见重构"""
        if iteration == 1:
            system_prompt = (
                "You are an elite software architect and quantitative engineer (Generator).\n"
                "Your objective is to provide a complete, robust, production-quality solution to the user's task.\n"
                "Write clean, idiomatic code with thorough documentation of your assumptions."
            )
            user_prompt = f"任务目标:\n{task}\n\n请给出完整实现方案与代码。"
        else:
            system_prompt = (
                "You are an elite software architect and quantitative engineer (Refiner).\n"
                "You are tasked with rewriting and heavily improving a previous draft based on strict review feedback.\n"
                "Carefully address EVERY flaw and suggestion pointed out by the Critic Judge.\n"
                "Do NOT settle for superficial fixes; redesign fragile structures to make the solution bulletproof."
            )
            user_prompt = (
                f"【原始任务】:\n{task}\n\n"
                f"【上一版本草稿】:\n{previous_draft}\n\n"
                f"【评审法官打分与缺陷诊断】:\n"
                f"得分: {critique.score} / 100\n"
                f"发现缺陷:\n" + "\n".join(f"- {f}" for f in critique.flaws) + "\n\n"
                f"整改建议:\n" + "\n".join(f"- {s}" for s in critique.suggestions) + "\n\n"
                f"请彻底吸取以上意见，输出重构后的全新高可靠版本！"
            )

        return self._call_llm(system_prompt, user_prompt, temperature=0.3)

    def critique_draft(self, task: str, draft: str, rubric: str) -> CritiqueResult:
        """Critic 角色：依据 Rubric 严苛审阅代码并输出结构化 JSON 评分"""
        system_prompt = (
            "You are a notoriously strict Lead Architect and Code Review Judge (Critic).\n"
            "Your job is to relentlessly find edge-case vulnerabilities, mathematical inaccuracies, "
            "concurrency bugs, memory safety issues, and performance bottlenecks.\n\n"
            "CRITICAL: You must output ONLY a valid JSON object matching this exact schema:\n"
            "{\n"
            '  "score": <integer from 0 to 100>,\n'
            '  "summary": "<1-2 sentence assessment>",\n'
            '  "flaws": ["<specific flaw 1>", "<specific flaw 2>", ...],\n'
            '  "suggestions": ["<actionable fix 1>", "<actionable fix 2>", ...]\n'
            "}\n"
            "Do NOT include markdown formatting or backticks around the JSON. Be harsh and objective."
        )

        user_prompt = (
            f"【审查任务要求】:\n{task}\n\n"
            f"【被审查代码/方案】:\n{draft}\n\n"
            f"【评审准则 (Rubric)】:\n{rubric}\n\n"
            f"请严格按 JSON 格式输出评估结果。"
        )

        raw_output = self._call_llm(system_prompt, user_prompt, temperature=0.1)

        # 稳健提取 JSON
        try:
            # 去除可能的 ```json 包裹
            cleaned = re.sub(r"^```(?:json)?\s*", "", raw_output.strip(), flags=re.MULTILINE)
            cleaned = re.sub(r"```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
            parsed = json.loads(cleaned)
            return CritiqueResult(
                score=int(parsed.get("score", 70)),
                flaws=list(parsed.get("flaws", [])),
                suggestions=list(parsed.get("suggestions", [])),
                summary=str(parsed.get("summary", "Review completed."))
            )
        except Exception:
            # 降级正则提取 score
            score_match = re.search(r'"score":\s*(\d+)', raw_output)
            score = int(score_match.group(1)) if score_match else 70
            return CritiqueResult(
                score=score,
                flaws=["JSON parsing imperfect, but review provided in summary."],
                suggestions=["Refine according to raw text critique."],
                summary=raw_output[:200]
            )

    def run(self, task: str, rubric: Optional[str] = None) -> Dict[str, Any]:
        """执行完整 Reflection 反思精炼循环"""
        effective_rubric = rubric or DEFAULT_RUBRIC
        trace: List[Dict[str, Any]] = []

        print(f"\n🎯 [Reflection Task]: {task[:80]}...")
        print(f"⚖️ [Reflection Budget]: Max Iterations = {self.max_iterations}, Target Score = {self.target_score}")
        print("=" * 68)

        current_draft = ""
        last_critique = None

        for iteration in range(1, self.max_iterations + 1):
            t_start = time.time()
            print(f"\n🔄 [Iteration {iteration}/{self.max_iterations}]")

            # 1. Generator 生成/重构
            role_action = "Drafting initial solution..." if iteration == 1 else "Refining solution based on critique..."
            print(f"✍️ [Generator]: {role_action}")
            current_draft = self.generate_draft(task, iteration, current_draft, last_critique)
            preview_draft = current_draft[:160].replace("\n", " ")
            print(f"📄 [Draft Snapshot]: {preview_draft}...")

            # 2. Critic 严苛打分
            print(f"🧐 [Critic as Judge]: Evaluating draft against rubric...")
            critique = self.critique_draft(task, current_draft, effective_rubric)
            last_critique = critique

            iter_duration = time.time() - t_start
            print(f"⭐ [Score]: {critique.score} / 100 (Target: {self.target_score})")
            print(f"🔍 [Assessment]: {critique.summary}")
            if critique.flaws:
                print(f"⚠️ [Flaws Found ({len(critique.flaws)})]:")
                for f in critique.flaws[:3]:
                    print(f"    - {f}")

            trace.append({
                "iteration": iteration,
                "score": critique.score,
                "summary": critique.summary,
                "flaws": critique.flaws,
                "suggestions": critique.suggestions,
                "draft_length": len(current_draft),
                "duration_sec": round(iter_duration, 2)
            })

            # 3. 达标判定
            if critique.score >= self.target_score:
                print(f"\n🎉 [Target Score Met in Iteration {iteration}]: 代码已达工业级生产标准！")
                break
            elif iteration < self.max_iterations:
                print(f"🔄 未达到 {self.target_score} 分标准，启动下一轮重构精炼...")

        print("=" * 68)
        return {
            "task": task,
            "final_draft": current_draft,
            "final_score": last_critique.score if last_critique else 0,
            "total_iterations": len(trace),
            "trace": trace
        }


if __name__ == "__main__":
    agent = ReflectionAgent(target_score=85, max_iterations=2)
    sample_task = "请用 C++ 实现一个单生产者单消费者 (SPSC) 无锁队列，要求严格内存序并考虑缓存行伪共享。"
    res = agent.run(sample_task)
    print("\n--- FINAL OUTPUT ---\n")
    print(res["final_draft"][:500])
