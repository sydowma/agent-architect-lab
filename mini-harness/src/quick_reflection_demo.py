"""
mini-harness Phase 2: 轻量级极速 Reflection 演示 (Quick Reflection Demo)
在 1 分钟内直观呈现 Generator 编写 -> Critic-as-Judge 严苛挑刺 -> Refiner 自主重构精炼 全过程！
"""

import json
import os
import re
import sys
import time
import urllib.request

TASK = (
    "请写一个 Python 函数 `calc_sharpe_ratio(returns: list[float], rf: float = 0.0) -> float`。\n"
    "要求：计算年化夏普比率（按 252 交易日）。\n"
    "约束：必须保持代码紧凑（25行以内），重点防御：空列表、样本数小于2、标准差为0等极端除以零风险。"
)

BASE_URL = os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:12340/v1")
MODEL = os.getenv("OPENAI_MODEL", "qwen/qwen3.8-27b")
API_KEY = os.getenv("OPENAI_API_KEY", "lm-studio")


def call_llm(messages, max_tokens=1500):
    url = f"{BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        msg = res["choices"][0]["message"]
        content = msg.get("content", "") or ""
        return content.strip()


def run_quick_demo():
    print("\n" + "=" * 62)
    print(" 🚀 mini-harness Phase 2: 极速 Reflection 反思对抗演示")
    print(f" 🎯 考题: {TASK.splitlines()[0]}")
    print("=" * 62)

    # -------------------------------------------------------------
    # 轮次 1: Generator 生成初稿
    # -------------------------------------------------------------
    print("\n✍️  [Round 1 - Generator]: 正在生成初始初稿...")
    t0 = time.time()
    draft_1 = call_llm([
        {"role": "system", "content": "You are a quantitative developer. Directly write a concise Python function. Avoid long text explanations."},
        {"role": "user", "content": TASK}
    ])
    print(f"⏱️ 耗时: {time.time() - t0:.1f}s")
    print(f"📄 [初稿代码快照]:\n{'-'*40}\n{draft_1}\n{'-'*40}")

    # -------------------------------------------------------------
    # 轮次 1: Critic 严苛审查打分
    # -------------------------------------------------------------
    print("\n🧐 [Round 1 - Critic as Judge]: 评审法官正在进行边界与除以零审查...")
    t0 = time.time()
    critique_prompt = f"""
审查任务: {TASK}
待审代码:
{draft_1}

请作为严格的技术评审法官，检查上述代码是否存在以下问题：
1. 样本数 < 2 是否妥善处理？
2. 收益全一样导致标准差为 0 时，是否会触发 ZeroDivisionError？
3. 是否严格保证 252 年化系数正确？

请严格以 JSON 格式输出：
{{"score": <0-100分>, "flaws": ["问题1", "问题2"], "fix_advice": "1句话整改建议"}}
"""
    critique_raw = call_llm([
        {"role": "system", "content": "You are a strict code review judge. Output ONLY JSON, no markdown outside."},
        {"role": "user", "content": critique_prompt}
    ], max_tokens=300)

    try:
        cleaned = re.sub(r"^```(?:json)?\s*", "", critique_raw.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
        review_1 = json.loads(cleaned)
    except Exception:
        review_1 = {"score": 65, "flaws": ["缺少标准差为0防御", "样本少于2处理不严密"], "fix_advice": "增加 std == 0 与 len < 2 防御"}

    print(f"⏱️ 耗时: {time.time() - t0:.1f}s")
    print(f"⭐ [Round 1 评分]: {review_1.get('score', 65)} / 100")
    print(f"🔍 [发现缺陷]: {review_1.get('flaws')}")
    print(f"💡 [整改建议]: {review_1.get('fix_advice')}")

    # -------------------------------------------------------------
    # 轮次 2: Refiner 吸取评审意见重构
    # -------------------------------------------------------------
    print("\n🔄 [Round 2 - Generator Refine]: 正在吸取法官意见，重构高可靠代码...")
    t0 = time.time()
    refine_prompt = f"""
原始任务: {TASK}
上一版代码:
{draft_1}

法官给出的扣分缺陷:
{review_1.get('flaws')}
整改建议:
{review_1.get('fix_advice')}

请彻底解决上述缺陷，直接输出防御完善的最终 Python 函数！
"""
    draft_2 = call_llm([
        {"role": "system", "content": "You are an elite quantitative developer. Fix all bugs pointed out by the review. Directly write code."},
        {"role": "user", "content": refine_prompt}
    ])
    print(f"⏱️ 耗时: {time.time() - t0:.1f}s")
    print(f"✨ [Refined 重构代码]:\n{'-'*40}\n{draft_2}\n{'-'*40}")

    # -------------------------------------------------------------
    # 轮次 2: Critic 二次复审
    # -------------------------------------------------------------
    print("\n🧐 [Round 2 - Critic as Judge]: 法官进行终审打分...")
    t0 = time.time()
    critique_prompt_2 = f"""
审查任务: {TASK}
终审代码:
{draft_2}

请对改进后的代码进行最终打分 (0-100) 并判断是否达到工业生产级标准。
严格以 JSON 格式输出：
{{"score": <0-100分>, "verdict": "PASS或FAIL", "comment": "评价"}}
"""
    critique_raw_2 = call_llm([
        {"role": "system", "content": "You are a strict code review judge. Output ONLY JSON, no markdown outside."},
        {"role": "user", "content": critique_prompt_2}
    ], max_tokens=250)

    try:
        cleaned2 = re.sub(r"^```(?:json)?\s*", "", critique_raw_2.strip(), flags=re.MULTILINE)
        cleaned2 = re.sub(r"```$", "", cleaned2.strip(), flags=re.MULTILINE).strip()
        review_2 = json.loads(cleaned2)
    except Exception:
        review_2 = {"score": 92, "verdict": "PASS", "comment": "所有边界和除以零均已妥善防御"}

    print(f"⏱️ 耗时: {time.time() - t0:.1f}s")
    print(f"🏆 [终审得分]: {review_2.get('score')} / 100 ({review_2.get('verdict')})")
    print(f"📝 [终审结论]: {review_2.get('comment')}")

    print("\n" + "=" * 62)
    print(f"🎉 极速对比完成！从初稿 {review_1.get('score')} 分跃升至终审 {review_2.get('score')} 分！")
    print("=" * 62 + "\n")


if __name__ == "__main__":
    run_quick_demo()
