"""報告輸出。終端機寬度以 80 字元為準。"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from datetime import date

WEEK = "一二三四五六日"


def width(text: str) -> int:
    """終端機顯示寬度,中日韓字元算兩格。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text: str, target: int) -> str:
    """靠左補空白到指定顯示寬度,超過就截斷並加省略號。"""
    if width(text) <= target:
        return text + " " * (target - width(text))
    out = ""
    for char in text:
        if width(out) + width(char) > target - 1:
            break
        out += char
    return out + "…" + " " * max(0, target - width(out) - 1)


def rpad(text: str, target: int) -> str:
    """靠右補空白到指定顯示寬度。"""
    return " " * max(0, target - width(text)) + text


def fmt_day(day: date) -> str:
    return f"{day:%m/%d}(週{WEEK[day.weekday()]})"


def section(title: str) -> str:
    """區塊標題,底下加一條跟標題等寬的線。"""
    return f"{title}\n{'─' * min(width(title), 80)}"


def _tail(item: dict) -> str:
    bits = []
    if item.get("priority"):
        bits.append(str(item["priority"]))
    if item.get("difficulty"):
        bits.append(str(item["difficulty"]))
    if item.get("due"):
        due = item["due"]
        this_year = str(date.today().year)
        bits.append(f"到期 {due[5:]}" if due[:4] == this_year else f"到期 {due}")
    if item.get("overdue"):
        bits.append("已逾期")
    elif item.get("late"):
        bits.append("排在到期後")
    if item.get("defer_count"):
        bits.append(f"順延{item['defer_count']}次")
    return "  ".join(bits)


def plan_report(plan: dict, show_index: bool = True) -> str:
    items = plan.get("items", [])
    if not items:
        return "沒有可排的任務。"

    by_day: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        by_day[item["day"]].append(item)

    lines = []
    for day_text in sorted(by_day):
        day = date.fromisoformat(day_text)
        group = by_day[day_text]
        projects = sorted({g["project"] for g in group})
        lines.append(f"{fmt_day(day)}  {len(projects)} 專案 / {len(group)} 件")
        for item in group:
            if not show_index:
                num = "     "
            elif item.get("committed") or item.get("rid"):
                num = f"{item['idx']:>3}✓ "
            else:
                num = f"{item['idx']:>3}. "
            title = f"#{item['project']} {item['title']}"
            tail = _tail(item)
            lines.append(f"  {num}{pad(title, 48)} {tail}".rstrip())
        lines.append("")

    leftover = plan.get("leftover", [])
    if leftover:
        span = plan.get("span_days")
        scope = f"{span} 天" if span and span < 14 else f"{plan.get('weeks', 2)} 週"
        lines.append(f"這 {scope} 排不進去,共 {len(leftover)} 件")
        for item in leftover[:12]:
            lines.append(f"     #{item['project']} {item['title']}")
        if len(leftover) > 12:
            lines.append(f"     其餘 {len(leftover) - 12} 件略過不列")
        lines.append("")

    return "\n".join(lines).rstrip()


def projects_report(rows: list[dict]) -> str:
    if not rows:
        return "沒有讀到任何任務追蹤 database。"
    lines = [pad("專案", 16) + rpad("未完成", 7) + rpad("已逾期", 9) + "   最近到期"]
    for row in rows:
        due = row.get("nearest_due") or "無"
        lines.append(
            pad(row["project"], 16)
            + rpad(str(row["undone"]), 7)
            + rpad(str(row["overdue"]), 9)
            + f"   {due}"
        )
    return "\n".join(lines)


def roadmap_report(month: int, hint: dict) -> str:
    if not hint:
        return f"{month} 月的 2026 計畫頁沒有讀到安排。"
    lines = [f"{month} 月 2026 計畫頁上的安排"]
    for project, text in hint.items():
        lines.append(f"  {pad(project, 22)} {pad(text, 52).rstrip()}")
    return "\n".join(lines)


def today_report(day: date, pending: list[dict], done: list[dict], events: list[dict]) -> str:
    lines = [f"{fmt_day(day)} 今日工作"]
    if events:
        lines.append("")
        lines.append("行事曆已卡的時段")
        for ev in events:
            lines.append(f"  {ev['start'][11:16]}-{ev['end'][11:16]}  {ev['title']}")
    lines.append("")
    if pending:
        lines.append(f"待辦未完成 {len(pending)} 件")
        for item in pending:
            lines.append(f"  □ #{item['project']} {item['title']}  {_tail(item)}".rstrip())
    else:
        lines.append("今天的待辦都勾完了。")
    if done:
        lines.append("")
        lines.append(f"已完成 {len(done)} 件")
        for item in done:
            lines.append(f"  ✓ #{item['project']} {item['title']}")
    return "\n".join(lines)


def evening_message(day: date, pending: list[dict], done: list[dict]) -> str:
    total = len(pending) + len(done)
    head = f"{fmt_day(day)} 收尾:完成 {len(done)}/{total} 件"
    if not pending:
        return head + "\n今天的待辦全部勾完。"
    lines = [head, "", "還沒勾掉的:"]
    for item in pending:
        mark = f"(順延{item['defer_count']}次)" if item.get("defer_count") else ""
        lines.append(f"□ #{item['project']} {item['title']} {mark}".rstrip())
    lines.append("")
    lines.append("勾完後跑 rebalance,未完成的會順延並重新平衡後面幾天。")
    return "\n".join(lines)


def rebalance_report(moved: list[dict], stuck: list[dict], applied: bool) -> str:
    if not moved and not stuck:
        return "沒有逾期未完成的待辦,不需要重排。"
    lines = []
    if moved:
        verb = "已順延" if applied else "建議順延"
        lines.append(f"{verb} {len(moved)} 件")
        for item in moved:
            warn = "  ※順延多次,建議砍掉或改範圍" if item.get("defer_count", 0) >= 3 else ""
            lines.append(
                f"  #{item['project']} {item['title']} -> {fmt_day(date.fromisoformat(item['day']))}{warn}"
            )
    if stuck:
        lines.append("")
        lines.append(f"後面幾天已經滿了,這 {len(stuck)} 件塞不進去")
        for item in stuck:
            lines.append(f"  #{item['project']} {item['title']}")
        lines.append("")
        lines.append("要延長排程週數,還是砍掉部分範圍?")
    return "\n".join(lines)
