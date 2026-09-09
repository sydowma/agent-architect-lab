"""
mini-harness v0.1 终端交互控制台 (Terminal REPL CLI)

特性:
1. 极客风终端界面与实时执行追踪
2. 丰富的控制面 Slash Commands (/memory, /stats, /undo, /clear, /help)
3. 零三方依赖，纯原生 Python 实现
"""

import os
import sys
from core import HarnessEngine
from memory import memory_manager


BANNER = r"""
====================================================================
 🧠 mini-harness v0.1 | 工业级 ReAct 自主智能体控制终端
====================================================================
 🔌 模型服务: {base_url} ({model})
 🛡️ 防御机制: 动态工具掩码 | 异常语义自愈 | 指数退避重试 | 快照回滚
 🛠️ 可用工具: read_file, write_file, list_dir, calculate, run_command, save_fact, query_facts
 💡 控制指令: /memory (查看知识) | /stats (性能统计) | /undo (回滚) | /clear (清空)
====================================================================
"""

HELP_TEXT = """
可用指令列表:
  /memory, /mem     - 查看 Agent 已持久化沉淀的长期事实与用户偏好
  /stats            - 查看引擎度量统计 (总任务数、执行步数、自愈次数、掩码熔断数、LLM 耗时)
  /undo             - 回滚上一轮对话 (抹除中毒上下文，还原上一快照)
  /clear            - 清空当前工作记忆会话 (保留长期事实)
  /help, /?         - 显示帮助信息
  /exit, /quit, q   - 保存会话并退出程序
"""


def main():
    session_cache = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
        "v01_session.json"
    )
    engine = HarnessEngine(session_file=session_cache, max_steps=8)

    print(BANNER.format(base_url=engine.base_url, model=engine.model))

    # 开机展示长期记忆
    known = memory_manager.query_facts()
    if known and "No persistent" not in known:
        print(f"📂 [已挂载长期事实]:\n{known}\n" + "-" * 68)

    while True:
        try:
            user_input = input("\n👤 You: ").strip()
            if not user_input:
                continue

            # 处理退出
            if user_input.lower() in ("/exit", "/quit", "exit", "quit", "q"):
                print("\n👋 mini-harness 已安全退出，状态已保存。祝您编码愉快！\n")
                break

            # 处理帮助
            if user_input.lower() in ("/help", "/?", "help"):
                print(HELP_TEXT)
                continue

            # 处理查看长期记忆
            if user_input.lower() in ("/memory", "/mem"):
                facts = memory_manager.query_facts()
                print("\n" + "=" * 50)
                print("🧠 [长期知识库存储 (data/agent_memory.json)]")
                print("=" * 50)
                print(facts if facts else "(暂无长期记忆)")
                print("=" * 50)
                continue

            # 处理度量统计
            if user_input.lower() == "/stats":
                s = engine.stats
                print("\n" + "=" * 50)
                print("📊 [mini-harness v0.1 运行时度量统计]")
                print("=" * 50)
                print(f" • 累计总任务数 (Goals):      {s['total_goals']}")
                print(f" • 累计执行总步数 (Steps):    {s['total_steps']}")
                print(f" • 工具调用总次数 (Actions):  {s['total_tool_calls']}")
                print(f" • 自省自愈总次数 (Healed):   {s['total_self_corrections']}")
                print(f" • 动态工具掩码事件 (Masked): {s['total_masking_events']}")
                print(f" • 累积 LLM 耗时:             {s['total_llm_time_sec']:.2f} 秒")
                print("=" * 50)
                continue

            # 处理上下文回滚
            if user_input.lower() == "/undo":
                success = engine.rollback()
                if success:
                    print("\n⏪ [Checkpoint Rollback]: 上一轮对话已成功撤销，中毒上下文已抹除！")
                else:
                    print("\n⚠️ 无法回滚：当前没有可用的快照。")
                continue

            # 处理清空当前会话
            if user_input.lower() == "/clear":
                engine.clear_session()
                print("\n🧹 [Session Cleared]: 当前工作会话已清空，长期记忆依然完整保留。")
                continue

            # 正常执行 ReAct 任务目标
            result = engine.run(user_input)
            print(f"\n🤖 Agent: {result['final_answer']}")

        except KeyboardInterrupt:
            print("\n\n👋 会话已中断。")
            break
        except Exception as e:
            print(f"\n❌ 控制台运行时异常: {str(e)}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        single_goal = " ".join(sys.argv[1:])
        eng = HarnessEngine(max_steps=8)
        res = eng.run(single_goal)
        print(f"\n🤖 Agent: {res['final_answer']}\n")
    else:
        main()
