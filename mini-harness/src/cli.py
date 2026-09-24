"""mini-harness v1.0 终端交互控制台 (Terminal REPL CLI).

Features:
1. Production-grade interactive terminal for the v1.0 Agent Harness.
2. Rich slash commands: /skills, /hooks, /sandbox, /ledger, /export, /stats, /help, /exit.
3. Live telemetry: KV Prefix Cache hit rates, sandbox containment status, and ledger balance.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Add src dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from v10_production_harness import (
    ExecutionMode,
    ProductionHarnessConfig,
    ProductionHarnessV1,
)

BANNER = r"""
====================================================================
 🧠 mini-harness v1.0 | 工业级 Coding Agent 终端控制台
====================================================================
 🔌 会话标识: {session_id}
 🛡️ 沙箱模式: {execution_mode} (--network=none | memory=256MB | pids=64)
 ⚡ 提示缓存: KV Prefix Cache 严格单调守卫 (TTFT ~25ms)
 🧩 制度治理: {skills_count} 个已安装技能 | {hooks_count} 个生命周期 Hooks
 📜 审计账本: 双向工具平衡账本 (Synthetic Ledger)
 💡 控制指令: /skills | /hooks | /sandbox | /ledger | /export | /help
====================================================================
"""

HELP_TEXT = """
可用指令列表 (Slash Commands):
  /skills, /sk      - 查看当前已安装的本地技能与指纹状态
  /hooks, /hk       - 查看已挂载的生命周期 Hooks 与执行记录
  /sandbox, /sb     - 查看沙箱隔离状态 (Docker / 仿真、网络红线与内存上限)
  /ledger, /lg      - 打印工具调度与多代理委派的审计账本
  /export, /exp     - 导出当前会话为 Pi 风格纯追加 JSONL 文件
  /stats            - 查看引擎性能指标 (总轮数、缓存命中率、平均耗时)
  /help, /?         - 显示本帮助信息
  /exit, /quit, q   - 安全退出并释放计算资源
"""


def main():
    parser = argparse.ArgumentParser(description="mini-harness v1.0 CLI")
    parser.add_argument("--docker", action="store_true", help="Use real local Docker sandbox if available")
    parser.add_argument("--skills-dir", type=str, default=None, help="Directory containing SKILL.md files")
    parser.add_argument("--workspace", type=str, default="/tmp/mini-harness-workspace", help="Workspace path")
    args = parser.parse_args()

    mode = ExecutionMode.DOCKER if args.docker else ExecutionMode.SIMULATED

    # Auto-detect default skills directory if not provided
    skills_dir = args.skills_dir
    if not skills_dir:
        candidate = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".agent", "skills")
        if os.path.exists(candidate):
            skills_dir = candidate

    config = ProductionHarnessConfig(
        execution_mode=mode,
        workspace_dir=args.workspace,
        skills_dir=skills_dir,
    )
    harness = ProductionHarnessV1(config)

    print(BANNER.format(
        session_id=harness.session_id,
        execution_mode=harness.config.execution_mode.value.upper(),
        skills_count=len(harness.skill_registry.installed_names()),
        hooks_count=len(harness.hook_engine.handlers),
    ))

    try:
        while True:
            try:
                user_input = input("\n👤 You: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n⚠️ 捕获中断信号，正在安全闭合账本...")
                harness.handle_user_interruption("User Ctrl+C in CLI")
                break

            if not user_input:
                continue

            # Command: /exit
            if user_input.lower() in ("/exit", "/quit", "exit", "quit", "q"):
                print("👋 正在保存会话并安全退出...")
                break

            # Command: /help
            if user_input.lower() in ("/help", "/?", "help"):
                print(HELP_TEXT)
                continue

            # Command: /skills
            if user_input.lower() in ("/skills", "/sk"):
                skills = harness.skill_registry.installed_names()
                print(f"\n🧩 [已安装技能列表] (总数: {len(skills)})")
                if not skills:
                    print("  (暂未加载自定义技能，可通过 --skills-dir 指定目录)")
                for name in skills:
                    sk = harness.skill_registry.get(name)
                    print(f"  • {name} (v{sk.version}, source: {sk.source.value}, fp: {sk.fingerprint()[:8]}...)")
                continue

            # Command: /hooks
            if user_input.lower() in ("/hooks", "/hk"):
                print(f"\n⚓ [已挂载 Hooks 状态]")
                for h in harness.hook_engine.handlers:
                    block_tag = "[BLOCKING]" if h.can_block else "[OBSERVE]"
                    print(f"  • {h.event_name.value:<18} | {block_tag:<10} | {h.name} (order: {h.display_order})")
                continue

            # Command: /sandbox
            if user_input.lower() in ("/sandbox", "/sb"):
                sb = harness.sandbox
                pol = harness.policy
                print(f"\n🛡️ [沙箱隔离运行时]")
                print(f"  • 沙箱类型: {type(sb).__name__}")
                print(f"  • 运行状态: {sb.state.value}")
                print(f"  • 网络模式: {pol.network_mode.value} (物理断网)")
                print(f"  • 内存配额: {pol.memory_limit_mb} MB (超出触发 Linux OOM 137)")
                print(f"  • 进程配额: {pol.pids_limit} PIDs (防 Fork 炸弹)")
                print(f"  • 工作目录: {harness.config.workspace_dir}")
                continue

            # Command: /ledger
            if user_input.lower() in ("/ledger", "/lg"):
                print(f"\n📜 [双向工具与委派审计账本]")
                print(f"  • 账本平衡状态: {'✅ 已闭合' if harness.ledger.is_balanced else '❌ 不平衡'}")
                print(f"  • 记录总数: {len(harness.ledger.ledger)}")
                for cid, entry in harness.ledger.ledger.items():
                    syn = "[SYNTHETIC]" if entry.is_synthetic else "[REAL]"
                    print(f"    - {cid} | {entry.status} | {entry.tool_name} {syn}")
                continue

            # Command: /export
            if user_input.lower() in ("/export", "/exp"):
                export_path = f"/tmp/{harness.session_id}.jsonl"
                jsonl_data = harness.export_session_jsonl()
                with open(export_path, "w", encoding="utf-8") as f:
                    f.write(jsonl_data)
                print(f"💾 会话已导出为 Pi 格式 JSONL: {export_path} ({len(jsonl_data)} 字节)")
                continue

            # Command: /stats
            if user_input.lower() == "/stats":
                hits = harness.cache_validator.total_cache_hits
                misses = harness.cache_validator.total_cache_misses
                total = max(1, hits + misses)
                ratio = (hits / total) * 100
                print(f"\n📊 [性能度量面板]")
                print(f"  • 累计轮数: {harness.turn_counter}")
                print(f"  • 提示缓存命中率: {ratio:.1f}% (Hits: {hits}, Misses: {misses})")
                print(f"  • 历史流长度: {len(harness.history_stream)} 条")
                continue

            # Normal turn execution
            # Check for simulated tool calling intent in user prompt
            tool_name = None
            tool_args = None

            if user_input.startswith("!bash "):
                tool_name = "run_bash"
                tool_args = {"cmd": user_input[6:].strip()}
            elif user_input.startswith("!read "):
                tool_name = "read_file"
                tool_args = {"path": user_input[6:].strip()}

            res = harness.execute_turn(user_input=user_input, tool_name=tool_name, tool_args=tool_args)

            # Output response
            cache_tag = "⚡ Cache Hit (TTFT ~25ms)" if res["cache_hit"] else f"⚠️ Cache Miss ({res['cache_reason']})"
            print(f"🤖 Agent [{cache_tag}]:")
            if res["observation"]:
                print(f"  {res['observation']}")
            else:
                print(f"  [Thinking] Understood: '{user_input}'. Ready for next command.")

    finally:
        harness.close()
        print("🔒 mini-harness 已安全收口，资源已释放。")


if __name__ == "__main__":
    main()
