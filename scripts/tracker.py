#!/usr/bin/env python3
"""
Agent Architect Lab - Study Tracker & Progress Reporter
Usage:
  python3 scripts/tracker.py log --hours 2.5 --task "完成 09_react-agent 剖析并重构" --phase 1 --notes "理清了 Thought-Action 循环终止边界"
  python3 scripts/tracker.py status
  python3 scripts/tracker.py report
"""

import argparse
import datetime
import json
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACKER_FILE = os.path.join(REPO_ROOT, "progress", "tracker.json")
DAILY_DIR = os.path.join(REPO_ROOT, "progress", "daily_logs")
README_FILE = os.path.join(REPO_ROOT, "README.md")

TARGET_HOURS = 90.0

PHASES = {
    1: {
        "title": "Phase 1: 核心机制手搓 (ai-agents-from-scratch)",
        "target_hours": 22.0,
        "deliverable": "mini-harness v0.1 (原生 CLI ReAct 智能体)"
    },
    2: {
        "title": "Phase 2: 架构纵横对比 (all-agentic-architectures)",
        "target_hours": 24.0,
        "deliverable": "Agent 架构横向基准评测报告 (ReAct vs PEV vs Reflection)"
    },
    3: {
        "title": "Phase 3: 工业级工程防御 (harness-books Book 1)",
        "target_hours": 24.0,
        "deliverable": "mini-harness v0.2 (集成 Context Compaction + 权限中断 + 崩溃自愈)"
    },
    4: {
        "title": "Phase 4: 工业体系与方法论 (harness-books Book 2)",
        "target_hours": 20.0,
        "deliverable": "mini-harness v1.0 完备版 + 《现代 AI Agent Harness 架构精要》深度长文"
    }
}


def load_data():
    if os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "target_hours": TARGET_HOURS,
        "start_date": datetime.date.today().isoformat(),
        "entries": [],
        "completed_milestones": []
    }


def save_data(data):
    with open(TRACKER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def make_progress_bar(current, total, length=24):
    fraction = min(max(current / total, 0.0), 1.0)
    filled = int(round(length * fraction))
    bar = "█" * filled + "░" * (length - filled)
    percent = fraction * 100
    return f"[{bar}] {percent:5.1f}% ({current:.1f}h / {total:.1f}h)"


def append_daily_log(date_str, hours, task, phase, notes):
    os.makedirs(DAILY_DIR, exist_ok=True)
    daily_file = os.path.join(DAILY_DIR, f"{date_str}.md")
    timestamp = datetime.datetime.now().strftime("%H:%M")

    header_needed = not os.path.exists(daily_file)
    with open(daily_file, "a", encoding="utf-8") as f:
        if header_needed:
            f.write(f"# 学习日志: {date_str}\n\n")
            f.write("| 记录时间 | 阶段 | 投入学时 | 核心任务 | 关键心得 / 产出 |\n")
            f.write("| :--- | :---: | :---: | :--- | :--- |\n")
        f.write(f"| {timestamp} | P{phase} | {hours:.1f}h | {task} | {notes or '-'} |\n")


def update_readme_dashboard(data):
    if not os.path.exists(README_FILE):
        return

    total_hours = sum(e["hours"] for e in data["entries"])
    phase_hours = {p: 0.0 for p in PHASES}
    for e in data["entries"]:
        p = e.get("phase", 1)
        phase_hours[p] = phase_hours.get(p, 0.0) + e["hours"]

    bar_str = make_progress_bar(total_hours, TARGET_HOURS)

    table_rows = []
    for p, info in PHASES.items():
        cur = phase_hours.get(p, 0.0)
        tgt = info["target_hours"]
        pct = (cur / tgt * 100) if tgt > 0 else 0
        status_icon = "🟢 完成" if pct >= 100 else ("🟡 推进中" if cur > 0 else "⚪ 未开始")
        table_rows.append(
            f"| Phase {p} | {info['title']} | {cur:.1f}h / {tgt:.1f}h | {pct:.1f}% | {status_icon} | {info['deliverable']} |"
        )

    dashboard_md = f"""<!-- DASHBOARD:START -->
### 📊 学习进度与工时看板 (实时同步)

* **总投入学时**：`{total_hours:.1f}` / `{TARGET_HOURS:.1f}` 小时
* **总完成进度**：`{bar_str}`
* **最近更新**：`{datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}`

| 阶段 | 阶段主题 | 学时投入 (当前/目标) | 进度 | 状态 | 核心交付成果 |
| :--- | :--- | :---: | :---: | :---: | :--- |
""" + "\n".join(table_rows) + "\n<!-- DASHBOARD:END -->"

    with open(README_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    new_content = re.sub(
        r"<!-- DASHBOARD:START -->.*?<!-- DASHBOARD:END -->",
        dashboard_md,
        content,
        flags=re.DOTALL
    )

    if new_content != content:
        with open(README_FILE, "w", encoding="utf-8") as f:
            f.write(new_content)


def log_entry(args):
    data = load_data()
    date_str = args.date or datetime.date.today().isoformat()

    entry = {
        "id": len(data["entries"]) + 1,
        "date": date_str,
        "hours": args.hours,
        "phase": args.phase,
        "task": args.task,
        "notes": args.notes or "",
        "logged_at": datetime.datetime.now().isoformat()
    }

    data["entries"].append(entry)
    save_data(data)

    append_daily_log(date_str, args.hours, args.task, args.phase, args.notes)
    update_readme_dashboard(data)

    total_hours = sum(e["hours"] for e in data["entries"])
    print(f"\n✅ 打卡成功！")
    print(f"  - 日期: {date_str}")
    print(f"  - 本次投入: {args.hours:.1f} 小时 (Phase {args.phase})")
    print(f"  - 任务内容: {args.task}")
    if args.notes:
        print(f"  - 学习心得: {args.notes}")
    print(f"\n📈 当前进度: {make_progress_bar(total_hours, TARGET_HOURS)}")
    print(f"   日志已追加至 progress/daily_logs/{date_str}.md 并同步至 README.md\n")


def show_status(args):
    data = load_data()
    total_hours = sum(e["hours"] for e in data["entries"])
    phase_hours = {p: 0.0 for p in PHASES}
    for e in data["entries"]:
        p = e.get("phase", 1)
        phase_hours[p] = phase_hours.get(p, 0.0) + e["hours"]

    print("\n" + "=" * 62)
    print(" 🚀 Agent Architect Lab - 学习进度与工时仪表盘")
    print("=" * 62)
    print(f" 总体进度: {make_progress_bar(total_hours, TARGET_HOURS)}")
    print("-" * 62)

    for p, info in PHASES.items():
        cur = phase_hours.get(p, 0.0)
        tgt = info["target_hours"]
        pct = (cur / tgt * 100) if tgt > 0 else 0
        tag = "🟢 完成" if pct >= 100 else ("🟡 推进中" if cur > 0 else "⚪ 未开始")
        print(f" [{tag}] Phase {p} ({pct:5.1f}% | {cur:.1f}h / {tgt:.1f}h)")
        print(f"    主题: {info['title']}")
        print(f"    交付: {info['deliverable']}")
        print()

    # 近7日打卡
    recent_days = {}
    today = datetime.date.today()
    for i in range(6, -1, -1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        recent_days[d] = 0.0

    for e in data["entries"]:
        d = e["date"]
        if d in recent_days:
            recent_days[d] += e["hours"]

    print(" 📅 最近 7 天打卡投入:")
    for d, h in recent_days.items():
        mark = "🔥" if h >= 2.0 else ("⚡" if h > 0 else "💤")
        bar = "▓" * int(h * 3)
        print(f"   {d} ({mark}) : {h:4.1f}h  {bar}")

    print("=" * 62 + "\n")


def generate_report(args):
    data = load_data()
    total_hours = sum(e["hours"] for e in data["entries"])
    phase_hours = {p: 0.0 for p in PHASES}
    for e in data["entries"]:
        p = e.get("phase", 1)
        phase_hours[p] = phase_hours.get(p, 0.0) + e["hours"]

    report_date = datetime.date.today().isoformat()
    report_file = os.path.join(REPO_ROOT, "progress", "weekly_reports", f"report_{report_date}.md")
    os.makedirs(os.path.dirname(report_file), exist_ok=True)

    lines = [
        f"# 阶段复盘报告 ({report_date})",
        "",
        f"- **累计总学时**：`{total_hours:.1f}` / `{TARGET_HOURS:.1f}` 小时",
        f"- **总进度**：`{total_hours / TARGET_HOURS * 100:.1f}%`",
        "",
        "## 各阶段进展",
        ""
    ]

    for p, info in PHASES.items():
        cur = phase_hours.get(p, 0.0)
        tgt = info["target_hours"]
        lines.append(f"### Phase {p}: {info['title']}")
        lines.append(f"- 投入学时：`{cur:.1f}h / {tgt:.1f}h` ({cur / tgt * 100:.1f}%)")
        lines.append(f"- 核心交付物：{info['deliverable']}")
        lines.append("")

    lines.append("## 最近完成的任务清单")
    lines.append("")
    for e in reversed(data["entries"][-10:]):
        notes_str = f"（{e['notes']}）" if e.get("notes") else ""
        lines.append(f"- **{e['date']}** [P{e.get('phase', 1)}] {e['hours']:.1f}h : {e['task']} {notes_str}")

    content = "\n".join(lines)
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\n📄 阶段复盘报告已生成至: progress/weekly_reports/report_{report_date}.md\n")
    print(content)


def main():
    parser = argparse.ArgumentParser(description="Agent 架构深入学习与打卡系统")
    subparsers = parser.add_subparsers(dest="subcommand", help="子命令")

    log_parser = subparsers.add_parser("log", help="记录学习时间与任务")
    log_parser.add_argument("--hours", type=float, required=True, help="投入时长(小时，如 2.5)")
    log_parser.add_argument("--task", type=str, required=True, help="完成的学习/开发任务")
    log_parser.add_argument("--phase", type=int, choices=[1, 2, 3, 4], default=1, help="所属阶段(1-4)")
    log_parser.add_argument("--notes", type=str, default="", help="收获、心得或问题点")
    log_parser.add_argument("--date", type=str, default=None, help="指定日期 (YYYY-MM-DD)，默认今天")

    subparsers.add_parser("status", help="查看当前学习进度仪表盘")
    subparsers.add_parser("report", help="生成阶段复盘总结报告")

    args = parser.parse_args()

    if args.subcommand == "log":
        log_entry(args)
    elif args.subcommand == "status":
        show_status(args)
    elif args.subcommand == "report":
        generate_report(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
