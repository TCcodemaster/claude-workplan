#!/usr/bin/env python3
"""workplan:把 Notion 任務平鋪成提醒事項待辦,重點才進行事曆。

子命令
    add         口述工作直接建待辦並排時段,最常用的入口
    status      對照行事曆與提醒事項的現況,回報落差但不動行程
    shift       依實際進度重排,預設只動逾期未完成的
    plan        從 Notion 抓任務排一至兩週,只有要對 Notion 時才用
    commit      把草案寫入指定的提醒事項清單
    focus       把待辦寫成行事曆事件,預設排全部還沒時段的
    today       顯示今天的待辦與已卡的時段
    rebalance   逾期未完成的往後順延並重新平衡
    evening     產生下班收尾訊息,可直接推播
    roadmap     顯示 2026 計畫頁上本月的安排
    lists       列出提醒事項清單名稱
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wp import apple, config, notion_src, planner, render  # noqa: E402

PLAN_FILE = "plan-latest.json"
# 選用的外部推播腳本。存在就會被呼叫,傳一個字串參數當訊息內容;
# 不存在就只發 macOS 本機通知。想接 Telegram、Slack 或其他服務就放一支同名腳本。
TG_NOTIFY = Path(os.environ.get("WORKPLAN_NOTIFY_SCRIPT", Path.home() / "bin" / "notify.sh"))


# ------------------------------------------------------------------ 工具


def load_plan(path: Path | None = None) -> dict:
    target = path or config.cache_path(PLAN_FILE)
    if not target.exists():
        raise SystemExit(f"找不到草案檔 {target},請先執行 plan。")
    return json.loads(target.read_text(encoding="utf-8"))


BACKUP_KEEP = 10


def save_plan(plan: dict, path: Path | None = None) -> Path:
    """寫入草案,寫之前先留一份備份。

    這個檔案存的是待辦與行事曆事件的對應關係,弄丟的話已經排好的東西全部跟行事曆
    斷開,勾完成標不回去、也偵測不到你在行事曆上動過什麼。所以每次覆寫前都留一份。
    """
    target = path or config.cache_path(PLAN_FILE)
    if target.exists():
        backups = target.parent / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (backups / f"{target.stem}-{stamp}.json").write_text(
            target.read_text(encoding="utf-8"), encoding="utf-8"
        )
        # 只留最近幾份,備份本身不該無限長大。
        old = sorted(backups.glob(f"{target.stem}-*.json"))[:-BACKUP_KEEP]
        for f in old:
            f.unlink()
    target.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def plan_index(path: Path | None = None) -> dict[str, dict]:
    """草案檔裡已寫入的項目,以提醒事項 id 為索引。

    專案、順延次數這些 metadata 全部存在草案檔,不寫進提醒事項的備註,
    否則備註會直接顯示在待辦下方,畫面很亂。
    """
    target = path or config.cache_path(PLAN_FILE)
    if not target.exists():
        return {}
    plan = json.loads(target.read_text(encoding="utf-8"))
    return {i["rid"]: i for i in plan.get("items", []) if i.get("rid")}


def workplan_reminders(include_completed: bool = False) -> list[tuple[apple.Reminder, dict]]:
    """回傳工具管理的提醒與對應的草案項目。"""
    index = plan_index()
    if not index:
        return []
    return [
        (r, index[r.rid])
        for r in apple.read_reminders(include_completed)
        if r.rid in index
    ]


def assignment_from_pair(r: apple.Reminder, item: dict) -> planner.Assignment:
    return planner.Assignment(
        project=item.get("project") or "未分類",
        title=item.get("title") or r.title,
        day=(r.due if r.due else date.today()),
        minutes=item.get("minutes") or config.DEFAULT_TASK_MINUTES,
        rid=r.rid,
        defer_count=item.get("defer_count") or 0,
    )


def create_tagged(list_name: str, items: list[dict]) -> list[str]:
    """建立待辦並掛上原生標籤。

    掛標籤的捷徑靠標題與清單找那筆提醒,所以建立時先用帶序號的唯一標題,
    掛完標籤再改回正式標題。不同專案的任務名稱可能完全一樣,
    例如兩個專案都有「分析新需求」,不做這一步捷徑會標到錯的那筆。
    """
    if not items:
        return []
    stamped = [
        f"{i['title']} ⟦{i['project']}-{n}⟧" for n, i in enumerate(items)
    ]
    payload = [
        {
            "title": stamp,
            "due": date.fromisoformat(i["day"]) if isinstance(i["day"], str) else i["day"],
            "body": "",
            "url": i.get("url") or "",
        }
        for stamp, i in zip(stamped, items)
    ]
    rids = apple.create_reminders(list_name, payload)

    if apple.tag_shortcut_installed():
        jobs = [
            {"title": stamp, "list": list_name, "tag": i["project"]}
            for stamp, i in zip(stamped, items)
        ]
        ok, failed = apple.apply_tags(jobs)
        print(f"已掛上原生標籤 {ok}/{len(jobs)} 件。")
        for t in failed:
            print(f"  標籤失敗:{t}")
    else:
        print(f"找不到「{config.TAG_SHORTCUT}」捷徑,跳過標籤。")
        print("要補的話安裝 shortcuts/workplan-tags2.shortcut。")

    apple.set_titles([{"rid": rid, "title": i["title"]} for rid, i in zip(rids, items)])
    return rids


def item_dict(a: planner.Assignment, idx: int) -> dict:
    return {
        "idx": idx,
        "project": a.project,
        "title": a.title,
        "day": a.day.isoformat(),
        "minutes": a.minutes,
        "due": a.due.isoformat() if a.due else None,
        "priority": a.priority,
        "difficulty": a.difficulty,
        "status": a.status,
        "url": a.url,
        "page_id": a.page_id,
        "late": a.late,
        "overdue": a.overdue,
        "defer_count": a.defer_count,
        "rid": a.rid,
        "event_uid": None,
    }


# ------------------------------------------------------------------ 子命令


def cmd_projects(args) -> int:
    rows = notion_src.project_summary()
    print(render.projects_report(rows))
    return 0


def cmd_plan(args) -> int:
    today = date.today()
    if not args.projects and not args.all:
        print("要先指定這次排哪些專案,或加 --all 排全部。可選的專案:")
        print()
        print(render.projects_report(notion_src.project_summary()))
        return 1
    projects = None
    if args.projects:
        typed = [p.strip() for p in args.projects.split(",") if p.strip()]
        projects = [config.resolve_project(p) for p in typed]
        known = config.known_prefixes()
        unknown = [t for t, r in zip(typed, projects) if r not in known]
        if unknown:
            print(f"認不出這些專案:{', '.join(unknown)}")
            print(f"可用的前綴:{', '.join(known)}")
            print("要新增說法就改 references/project_map.json 的 aliases。")
            return 1
        renamed = [(t, r) for t, r in zip(typed, projects) if t != r]
        if renamed:
            hint = ", ".join(f"{t} 視為 {r}" for t, r in renamed)
            print(f"專案名稱對應:{hint}")
            print()
        projects = sorted(set(projects))

    span = args.days if args.days else args.weeks * 7
    days = planner.workdays(today, span)
    if not days:
        print("這段期間沒有工作日。")
        return 0

    existing = workplan_reminders()
    occupied = [
        assignment_from_pair(r, item)
        for r, item in existing
        if r.due and today <= r.due <= days[-1]
    ]
    loads = planner.build_loads(days, occupied)
    taken = {(a.project, a.title) for a in occupied}

    tasks = notion_src.open_tasks(projects=projects)
    fresh = [t for t in tasks if (t.project, t.title) not in taken]
    assigned, leftover = planner.distribute(fresh, days, loads, today)

    # 已經寫入提醒事項的項目一定要留著,否則 rid 與 event_uid 的對應會斷,
    # 完成狀態就沒辦法標回行事曆。
    plan_path = Path(args.out) if args.out else config.cache_path(PLAN_FILE)
    kept = []
    previous_list = None
    if plan_path.exists():
        old = json.loads(plan_path.read_text(encoding="utf-8"))
        previous_list = old.get("list")
        kept = [i for i in old.get("items", []) if i.get("rid")]
        for item in kept:
            item["committed"] = True
    next_idx = max((i["idx"] for i in kept), default=0) + 1

    plan = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "weeks": args.weeks,
        "span_days": span,
        "projects": projects or "all",
        "list": previous_list,
        "items": kept + [item_dict(a, next_idx + i) for i, a in enumerate(assigned)],
        "leftover": [
            {"project": t.project, "title": t.title, "due": t.due.isoformat() if t.due else None}
            for t in leftover
        ],
    }
    path = save_plan(plan, Path(args.out) if args.out else None)

    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    print(render.plan_report(plan))
    print()
    print(render.roadmap_report(today.month, notion_src.roadmap_hint(today.month)))
    print()
    print(f"Notion 未完成任務 {len(tasks)} 件,其中 {len(existing)} 件已在提醒事項。")
    print(f"草案存於 {path},尚未寫入任何地方。")
    return 0


def cmd_commit(args) -> int:
    plan = load_plan(Path(args.plan) if args.plan else None)
    pending = [i for i in plan["items"] if not i.get("rid")]
    if not pending:
        print("草案裡沒有待寫入的項目。")
        return 0

    names = apple.reminder_list_names()
    if args.list not in names:
        print(f"提醒事項沒有「{args.list}」這個清單,現有清單:{', '.join(names)}")
        return 1

    rids = create_tagged(args.list, pending)
    for item, rid in zip(pending, rids):
        item["rid"] = rid
    plan["list"] = args.list
    save_plan(plan, Path(args.plan) if args.plan else None)
    print(f"已寫入「{args.list}」清單 {len(rids)} 件待辦,只設日期沒有時間。")
    if len(rids) != len(pending):
        print(f"注意:預期 {len(pending)} 件但只建立 {len(rids)} 件,請檢查提醒事項。")
    return 0


def cmd_add(args) -> int:
    """手動加待辦。使用者的工作粒度常常比 Notion 任務粗,例如整天分析新需求。

    stdin 收 [{project, title, day, minutes?}],寫入提醒事項後併進草案,
    這樣接下來還能用 focus 把它卡進行事曆時段。
    """
    raw = sys.stdin.read().strip()
    if not raw:
        print("請從 stdin 給 JSON 陣列,每筆要有 project、title、day。", file=sys.stderr)
        return 1
    entries = json.loads(raw)

    names = apple.reminder_list_names()
    if args.list not in names:
        print(f"提醒事項沒有「{args.list}」這個清單,現有清單:{', '.join(names)}")
        return 1

    for entry in entries:
        entry["project"] = config.resolve_project(entry["project"])

    rids = create_tagged(args.list, entries)

    plan_path = config.cache_path(PLAN_FILE)
    plan = (
        json.loads(plan_path.read_text(encoding="utf-8"))
        if plan_path.exists()
        else {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "items": [], "leftover": []}
    )
    next_idx = max((i["idx"] for i in plan["items"]), default=0) + 1
    added = []
    for entry, rid in zip(entries, rids):
        item = {
            "idx": next_idx,
            "project": entry["project"],
            "title": entry["title"],
            "day": entry["day"],
            "minutes": entry.get("minutes") or config.DEFAULT_TASK_MINUTES,
            "due": None,
            "priority": entry.get("priority"),
            "difficulty": entry.get("difficulty"),
            "status": None,
            "url": entry.get("url", ""),
            "page_id": "",
            "late": False,
            "overdue": False,
            "defer_count": 0,
            "rid": rid,
            "event_uid": None,
            "manual": True,
        }
        plan["items"].append(item)
        added.append(item)
        next_idx += 1
    plan["list"] = args.list
    save_plan(plan)

    print(f"已在「{args.list}」清單建立 {len(rids)} 件待辦:")
    for item in added:
        day = date.fromisoformat(item["day"])
        print(f"  {item['idx']:>3}. {render.fmt_day(day)}  #{item['project']} {item['title']}")  # 報告用 # 標示專案

    # 口述的工作幾乎都要卡時段,分成兩個指令只是多一次來回,所以預設就排。
    if args.no_schedule:
        print()
        print("要卡進行事曆時段的話跑 focus。")
        return 0
    print()
    return schedule_items(added, plan)


def cmd_focus(args) -> int:
    plan_path = Path(args.plan) if args.plan else None
    plan = load_plan(plan_path)
    if args.pick:
        wanted = {int(x) for x in args.pick.replace(" ", "").split(",") if x}
        picks = [i for i in plan["items"] if i["idx"] in wanted]
        if not picks:
            print("挑選的編號不在草案裡。")
            return 1
    else:
        # 不指定編號就排全部還沒有時段的,平常都是這樣用,抄一長串編號沒有意義。
        picks = [i for i in plan["items"] if not i.get("event_uid") and i.get("rid")]
        if not picks:
            print("沒有待排時段的項目。")
            return 0
    return schedule_items(picks, plan, plan_path, split=args.split, force=bool(args.pick))


def schedule_items(
    picks: list[dict], plan: dict, plan_path=None, split: bool = False, force: bool = False
) -> int:
    """把值得保護時間的待辦卡進行事曆空檔。add 與 focus 共用這段,兩邊行為才會一致。

    不是每一件待辦都該進行事曆。太短的工作留在提醒事項就好,force 為真時
    (使用者用 --pick 明確指定)才不管長度一律排進去。
    """
    already = [i for i in picks if i.get("event_uid")]
    picks = [i for i in picks if not i.get("event_uid")]
    for item in already:
        print(f"編號 {item['idx']} 已經有行事曆事件,跳過。")
    if not picks:
        return 0

    now = datetime.now().replace(second=0, microsecond=0)

    # 同一天同一專案的任務合併成一個時段,除非使用者要求拆開。
    # 一個專案切成好幾個小事件只會讓行事曆變得瑣碎,實際做的時候本來就是連著做。
    groups: dict[tuple[str, str], list[dict]] = {}
    for item in picks:
        key = (item["day"], item["project"] if not split else f"{item['project']}#{item['idx']}")
        groups.setdefault(key, []).append(item)

    # 一組超過上限就拆開,標題塞太多件反而看不出重點。
    by_day: dict[str, list[list[dict]]] = {}
    for (day_text, _), members in groups.items():
        if len(members) > config.MERGE_MAX_TASKS:
            for m in members:
                by_day.setdefault(day_text, []).append([m])
        else:
            by_day.setdefault(day_text, []).append(members)

    # 太短的整組不進行事曆。門檻套在合併後的總時數上,同一天同專案的幾件小事
    # 湊滿一小時仍然值得保護一個時段。
    too_short: list[dict] = []
    if not force:
        for day_text, gs in by_day.items():
            keep = []
            for members in gs:
                total = sum(m.get("minutes") or config.DEFAULT_TASK_MINUTES for m in members)
                if total >= config.MIN_EVENT_MINUTES:
                    keep.append(members)
                else:
                    too_short.extend(members)
            by_day[day_text] = keep

    to_create = []
    unplaced = []
    for day_text in sorted(by_day):
        day = date.fromisoformat(day_text)
        events = apple.read_events(
            datetime.combine(day, datetime.min.time()),
            datetime.combine(day + timedelta(days=1), datetime.min.time()),
        )
        slots = planner.subtract_busy(planner.day_slots(day, now), events)
        for members in sorted(by_day[day_text], key=lambda m: m[0]["idx"]):
            total = sum(m.get("minutes") or config.DEFAULT_TASK_MINUTES for m in members)
            spot = planner.place(slots, total)
            if spot is None:
                # 單一空檔塞不下就切成幾段分散在同一天,總時數不變。
                segments = planner.place_split(slots, total)
            else:
                segments = [spot]
            if not segments:
                unplaced.extend(members)
                continue
            project = members[0]["project"]
            titles = [m["title"] for m in members]
            # 備註只放人看得懂的東西。事件與待辦的對應存在 cache 的 event_uids,
            # 不用寫進備註,那串 uuid 一打開事件就糊在臉上。
            notes = [f"・{t}" for t in titles] if len(members) > 1 else []
            base_title = f"[{project}] " + "、".join(titles)
            for seg_no, (start, end) in enumerate(segments, start=1):
                suffix = f"（{seg_no}／{len(segments)}）" if len(segments) > 1 else ""
                to_create.append(
                    {
                        "key": f"{members[0]['idx']}-{seg_no}",
                        "members": [m["idx"] for m in members],
                        "title": base_title + suffix,
                        "calendar": config.calendar_for(project),
                        "start": start,
                        "end": end,
                        "notes": "\n".join(notes),
                    }
                )

    made = apple.create_events(config.TARGET_CALENDAR, to_create)
    # 一件任務切成幾段時會對到多個事件,全部記下來,勾完成時每一段都要標到。
    uids_by_idx: dict[int, list[str]] = {}
    slots_by_idx: dict[int, list[list[str]]] = {}
    for job in to_create:
        uid = made.get(job["key"])
        if uid:
            for idx in job["members"]:
                uids_by_idx.setdefault(idx, []).append(uid)
                # 記下排定的時間,之後才比對得出你在行事曆上拖動過哪些事件。
                slots_by_idx.setdefault(idx, []).append(
                    [job["start"].isoformat(), job["end"].isoformat()]
                )
    for item in picks:
        uids = uids_by_idx.get(item["idx"])
        if uids:
            item["event_uid"] = uids[0]
            item["event_uids"] = uids
            item["event_slots"] = slots_by_idx.get(item["idx"], [])
    save_plan(plan, plan_path)

    for job in to_create:
        mins = int((job["end"] - job["start"]).total_seconds() // 60)
        print(f"{job['start']:%m/%d %H:%M}-{job['end']:%H:%M}  ({mins}分)  {job['title']}")
    cals = sorted({job["calendar"] for job in to_create if made.get(job["key"])})
    print(f"已在「{'」「'.join(cals) or config.TARGET_CALENDAR}」建立 {len(made)} 個事件。")
    if too_short:
        print(
            f"這 {len(too_short)} 件不到 {config.MIN_EVENT_MINUTES} 分,只留在待辦不佔時段:"
        )
        for item in too_short:
            print(f"  [{item['project']}] {item['title']}（{item.get('minutes') or 0} 分）")
        print("要硬排的話用 focus --pick 指定編號。")
    if unplaced:
        print(f"當天空檔不足,這 {len(unplaced)} 件沒有排入行事曆,待辦仍然保留:")
        for item in unplaced:
            print(f"  [{item['project']}] {item['title']}")
    return 0


def _today_split(target: date):
    pairs = workplan_reminders(include_completed=True)
    same_day = [(r, i) for r, i in pairs if r.due and r.due == target]

    def to_dict(r, item):
        return {
            "project": item.get("project") or "未分類",
            "title": item.get("title") or r.title,
            "defer_count": item.get("defer_count") or 0,
            "rid": r.rid,
        }

    pending = [to_dict(r, i) for r, i in same_day if not r.completed]
    done = [to_dict(r, i) for r, i in same_day if r.completed]
    return pending, done


def cmd_today(args) -> int:
    target = date.today()
    pending, done = _today_split(target)
    events = apple.read_events(
        datetime.combine(target, datetime.min.time()),
        datetime.combine(target + timedelta(days=1), datetime.min.time()),
    )
    ev_dicts = [
        {"title": e.title, "start": e.start.isoformat(), "end": e.end.isoformat()}
        for e in events
        if e.start and e.end and not e.allday
    ]
    ev_dicts.sort(key=lambda e: e["start"])
    print(render.today_report(target, pending, done, ev_dicts))
    return 0


def cmd_evening(args) -> int:
    target = date.today()
    pending, done = _today_split(target)
    message = render.evening_message(target, pending, done)
    print(message)
    if args.notify:
        sent = []
        if TG_NOTIFY.exists():
            subprocess.run([str(TG_NOTIFY), message], check=False)
            sent.append("Telegram")
        subprocess.run(
            [
                "osascript",
                "-e",
                'display notification {} with title "workplan 收尾"'.format(
                    apple.q(f"完成 {len(done)}/{len(pending) + len(done)} 件,未完成 {len(pending)} 件")
                ),
            ],
            check=False,
        )
        sent.append("macOS 通知")
        print()
        print(f"已推播:{', '.join(sent)}")
    return 0


def _fmt_slots(slots: list) -> str:
    """把時段清單寫成人看得懂的一行,例如 08/13 09:00-12:00＋13:00-16:00。"""
    if not slots:
        return "未排時段"
    parts = []
    day_text = ""
    for a, b in slots:
        start, end = datetime.fromisoformat(a), datetime.fromisoformat(b)
        if not day_text:
            day_text = f"{start:%m/%d}"
        parts.append(f"{start:%H:%M}-{end:%H:%M}")
    return f"{day_text} " + "＋".join(parts)


def _sync_reality(plan: dict) -> dict:
    """拿行事曆與提醒事項的現況校正草案,回傳偵測到的落差。

    使用者會在行事曆上直接拖動事件、提早做完就把隔天的拉過來、或是整件沒動。
    草案記的是排程當下的計畫,不校正的話後續每一次調整都是照著過期的資料在算。
    行事曆與提醒事項才是真相,草案永遠讓步。
    """
    items = [i for i in plan.get("items", []) if i.get("rid")]
    if not items:
        return {"moved": [], "deleted": [], "done": [], "missed": [], "early": []}

    days = sorted({i["day"] for i in items if i.get("day")})
    events: dict[str, object] = {}
    if days:
        lo = date.fromisoformat(days[0]) - timedelta(days=7)
        hi = date.fromisoformat(days[-1]) + timedelta(days=14)
        for ev in apple.read_events(
            datetime.combine(lo, datetime.min.time()),
            datetime.combine(hi, datetime.min.time()),
            config.workplan_calendars(),
        ):
            events[ev.uid] = ev

    done_rids = {r.rid for r, _ in workplan_reminders(include_completed=True) if r.completed}
    today = date.today()
    moved, deleted, done, missed, early = [], [], [], [], []

    # 合併時段是好幾件共用一個事件,不能把整段時長回寫成每一件的時數,
    # 那會讓三件各自變成三小時,總量瞬間膨脹三倍。
    shared: dict[str, int] = {}
    for item in items:
        for uid in item.get("event_uids") or ([item["event_uid"]] if item.get("event_uid") else []):
            shared[uid] = shared.get(uid, 0) + 1

    for item in items:
        uids = item.get("event_uids") or ([item["event_uid"]] if item.get("event_uid") else [])
        slots = item.get("event_slots") or []
        live = [(u, events.get(u)) for u in uids]

        # 事件被整個刪掉,代表這件事被取消或改用別的方式處理。
        if uids and all(ev is None for _, ev in live):
            deleted.append(item)
            item["event_uid"] = None
            item["event_uids"] = []
            item["event_slots"] = []
            continue

        # 事件還在但時間不一樣,代表被拖動過。以行事曆為準改寫草案的日期與時數。
        new_slots = []
        for uid, ev in live:
            if ev is None or not ev.start or not ev.end:
                continue
            new_slots.append([ev.start.isoformat(), ev.end.isoformat()])
        # 草案沒記過時段就是第一次對照,拿現況當基準,不能報成被拖動。
        shifted = bool(slots) and slots != new_slots
        if new_slots:
            first = datetime.fromisoformat(new_slots[0][0]).date()
            if shifted:
                moved.append(
                    {
                        "item": item,
                        "from": _fmt_slots(slots),
                        "to": _fmt_slots(new_slots),
                    }
                )
                item["day"] = first.isoformat()
                if all(shared.get(u, 1) <= 1 for u, _ in live):
                    item["minutes"] = sum(
                        int(
                            (
                                datetime.fromisoformat(b) - datetime.fromisoformat(a)
                            ).total_seconds()
                            // 60
                        )
                        for a, b in new_slots
                    )
            item["event_slots"] = new_slots
            item["event_uids"] = [u for u, ev in live if ev is not None]
            item["event_uid"] = item["event_uids"][0] if item["event_uids"] else None

        # 完成狀態:排在未來卻已經勾完成的算超前,排在過去還沒勾的算落後。
        item_day = date.fromisoformat(item["day"]) if item.get("day") else None
        if item["rid"] in done_rids:
            done.append(item)
            if item_day and item_day > today:
                early.append(item)
        elif item_day and item_day < today:
            missed.append(item)

    return {"moved": moved, "deleted": deleted, "done": done, "missed": missed, "early": early}


def cmd_status(args) -> int:
    """把行事曆現況跟計畫對一次,只回報不改行程。"""
    plan_path = Path(args.plan) if args.plan else None
    plan = load_plan(plan_path)
    drift = _sync_reality(plan)
    save_plan(plan, plan_path)

    today = date.today()
    lines = []
    if drift["moved"]:
        lines.append("你在行事曆上動過這幾件,計畫已經跟著校正:")
        for m in drift["moved"]:
            it = m["item"]
            lines.append(f"  [{it['project']}] {it['title']}  {m['from']} → {m['to']}")
    if drift["deleted"]:
        lines.append("這幾件的行事曆事件被刪掉了,待辦還在:")
        for it in drift["deleted"]:
            lines.append(f"  [{it['project']}] {it['title']}")
    if drift["early"]:
        lines.append("這幾件排在後面但已經做完了,後續行程有空間可以往前拉:")
        for it in drift["early"]:
            lines.append(f"  [{it['project']}] {it['title']}（原訂 {it['day']}）")
    if drift["missed"]:
        lines.append("這幾件到期了還沒勾完成:")
        for it in drift["missed"]:
            lines.append(f"  [{it['project']}] {it['title']}（原訂 {it['day']}）")

    todo_today = [
        i
        for i in plan["items"]
        if i.get("day") == today.isoformat() and i["rid"] not in {x["rid"] for x in drift["done"]}
    ]
    if todo_today:
        lines.append(f"今天原訂 {len(todo_today)} 件:")
        for it in todo_today:
            lines.append(f"  [{it['project']}] {it['title']}")

    print(render.section(f"{render.fmt_day(today)} 進度對照"))
    print("\n".join(lines) if lines else "計畫跟行事曆一致,沒有要處理的落差。")
    if drift["early"] or drift["missed"]:
        print()
        print("要調整後續行程的話跑 shift,它只動還沒完成的項目。")

    if args.notify:
        # 早上推播只講要決定的事,細節等使用者回話再談。重排一定要等他點頭,
        # 一早自動把行程改掉的話,他看到的會是一份沒同意過的計畫。
        bits = []
        if drift["missed"]:
            bits.append(f"昨天有 {len(drift['missed'])} 件沒完成")
        if drift["early"]:
            bits.append(f"{len(drift['early'])} 件提早做完")
        if todo_today:
            bits.append(f"今天原訂 {len(todo_today)} 件")
        summary = "，".join(bits) if bits else "今天沒有待辦"
        if TG_NOTIFY.exists():
            subprocess.run([str(TG_NOTIFY), f"{summary}。要調整行程的話回我一聲。"], check=False)
        subprocess.run(
            [
                "osascript",
                "-e",
                'display notification {} with title "workplan 早安"'.format(apple.q(summary)),
            ],
            check=False,
        )
        print()
        print("已推播早安提醒。")
    return 0


def cmd_shift(args) -> int:
    """依實際進度重排後續行程,只動還沒完成的項目。

    做完的、正在做的都不碰,所以不會變成整批重排。提早做完就把後面的往前拉補空檔,
    落後就往後推,兩個方向共用同一套空檔計算。
    """
    plan_path = Path(args.plan) if args.plan else None
    plan = load_plan(plan_path)
    drift = _sync_reality(plan)
    done_rids = {i["rid"] for i in drift["done"]}
    today = date.today()

    # 預設只動非動不可的:逾期沒完成的那幾件。已經排好又還沒到期的一律不碰,
    # 否則每次調整都變成整批重排,行事曆上的安排每天長得不一樣。
    missed_rids = {i["rid"] for i in drift["missed"]}
    movable = [i for i in plan["items"] if i.get("rid") and i["rid"] in missed_rids]

    # --pull 才把後續未完成的項目一起納入,用來在提早做完之後把進度往前遞補。
    if args.pull:
        movable += [
            i
            for i in plan["items"]
            if i.get("rid")
            and i["rid"] not in done_rids
            and i["rid"] not in missed_rids
            and i.get("day")
            and date.fromisoformat(i["day"]) >= today
        ]
    if not movable:
        print("沒有逾期未完成的項目,行程不用動。")
        if drift["early"]:
            print("有項目提早做完,要把後面的往前拉的話加 --pull。")
        return 0

    # 落後的先做,其餘照原順序,這樣往前拉的時候不會把後面的插到前面去。
    movable.sort(key=lambda i: (i["rid"] not in missed_rids, i["day"], i["idx"]))

    old_uids = []
    for item in movable:
        old_uids.extend(item.get("event_uids") or ([item["event_uid"]] if item.get("event_uid") else []))
    old_uids = list(dict.fromkeys(old_uids))

    if not args.apply:
        print(render.section("重排預覽"))
        scope = "逾期未完成＋後續項目一起重排" if args.pull else "只有逾期未完成的,後面不動"
        print(f"會重排 {len(movable)} 件({scope}),已完成與已排定的不動。")
        for it in movable:
            mark = "逾期" if it["rid"] in missed_rids else it["day"]
            print(f"  [{it['project']}] {it['title']}（{mark}，{it.get('minutes') or 0} 分）")
        print()
        print("確認後加 --apply 實際套用。")
        return 0

    # 舊事件先撤掉再重排,不然新排程會把自己的舊時段當成佔用。
    if old_uids:
        apple.delete_events(old_uids)
    for item in movable:
        item["event_uid"] = None
        item["event_uids"] = []
        item["event_slots"] = []

    # 從今天起逐日找空檔,一天塞不下就換下一天,不受原本的日期綁死。
    days = planner.workdays(today, args.weeks * 7)
    now = datetime.now().replace(second=0, microsecond=0)
    queue = list(movable)
    placed_any = False
    for day in days:
        if not queue:
            break
        same_day = [i for i in queue if True]
        # 每天照容量上限收,收滿就換下一天。
        take, seen_projects = [], {}
        for item in same_day:
            proj = item["project"]
            if len(take) >= config.DAILY_MAX_TASKS:
                break
            if proj not in seen_projects and len(seen_projects) >= config.PROJECTS_PER_DAY:
                continue
            if seen_projects.get(proj, 0) >= config.TASKS_PER_PROJECT_PER_DAY:
                continue
            take.append(item)
            seen_projects[proj] = seen_projects.get(proj, 0) + 1
        if not take:
            continue
        for item in take:
            item["day"] = day.isoformat()
        before = [i for i in take if not i.get("event_uid")]
        schedule_items(take, plan, plan_path)
        done_now = [i for i in before if i.get("event_uid")]
        for item in done_now:
            queue.remove(item)
            placed_any = True

    updates = [{"rid": i["rid"], "day": date.fromisoformat(i["day"])} for i in movable if i.get("rid")]
    if updates:
        apple.update_reminder_dates([{"rid": u["rid"], "due": u["day"]} for u in updates])
    save_plan(plan, plan_path)

    if queue:
        print()
        print(f"這 {len(queue)} 件在 {args.weeks} 週內找不到空檔,待辦保留但沒有時段:")
        for it in queue:
            print(f"  [{it['project']}] {it['title']}")
    if not placed_any and not queue:
        print("沒有項目需要移動。")
    return 0


def cmd_rebalance(args) -> int:
    today = date.today()
    days = planner.workdays(today, args.days if args.days else args.weeks * 7)
    pairs = workplan_reminders(include_completed=False)
    stale = [
        assignment_from_pair(r, item)
        for r, item in pairs
        if r.due and r.due < today
    ]
    future = [
        assignment_from_pair(r, item)
        for r, item in pairs
        if r.due and today <= r.due <= (days[-1] if days else today)
    ]
    loads = planner.build_loads(days, future)
    moved, stuck = planner.redistribute(stale, days, loads, today)

    moved_dicts = [
        {
            "project": a.project,
            "title": a.title,
            "day": a.day.isoformat(),
            "defer_count": a.defer_count,
            "rid": a.rid,
        }
        for a in moved
    ]
    stuck_dicts = [{"project": a.project, "title": a.title} for a in stuck]

    if args.apply and moved:
        updates = [{"rid": a.rid, "due": a.day} for a in moved if a.rid]
        count = apple.update_reminder_dates(updates)
        # 順延次數存草案檔,不寫進備註,否則會顯示在待辦下方。
        plan_path = config.cache_path(PLAN_FILE)
        if plan_path.exists():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            bumped = {a.rid: a for a in moved if a.rid}
            for item in plan.get("items", []):
                a = bumped.get(item.get("rid"))
                if a:
                    item["day"] = a.day.isoformat()
                    item["defer_count"] = a.defer_count
            save_plan(plan)
        print(f"已更新 {count} 件待辦的日期。")
        print()

    print(render.rebalance_report(moved_dicts, stuck_dicts, args.apply))
    marked = _mark_done_events()
    if marked:
        print()
        print(f"已把 {marked} 個行事曆事件標上完成記號。")
    return 0


def _mark_done_events() -> int:
    """提醒事項勾了完成,對應的行事曆事件標上勾號。"""
    path = config.cache_path(PLAN_FILE)
    if not path.exists():
        return 0
    plan = json.loads(path.read_text(encoding="utf-8"))
    linked = {i["rid"]: i for i in plan.get("items", []) if i.get("rid") and i.get("event_uid")}
    if not linked:
        return 0
    done_rids = {r.rid for r, _ in workplan_reminders(include_completed=True) if r.completed}
    pending = [linked[rid] for rid in done_rids & set(linked) if not linked[rid].get("event_done")]
    if not pending:
        return 0

    # 事件標題常常是事後改短過的,不能用待辦名稱重新組一個標題蓋回去,
    # 那會把改好的標題洗掉。讀當天的事件,在現有標題前面加勾號。
    titles: dict[str, str] = {}
    for day_text in {i["day"] for i in pending if i.get("day")}:
        day = date.fromisoformat(day_text)
        for event in apple.read_events(
            datetime.combine(day, datetime.min.time()),
            datetime.combine(day + timedelta(days=1), datetime.min.time()),
            config.workplan_calendars(),
        ):
            titles[event.uid] = event.title

    jobs = []
    for item in pending:
        # 一件任務可能切成好幾段,每一段都要標。
        for uid in item.get("event_uids") or [item["event_uid"]]:
            current = titles.get(uid)
            if current is None:
                current = f"[{item['project']}] {item['title']}"
            if current.startswith("✓"):
                continue
            jobs.append(
                {
                    "calendar": config.calendar_for(item["project"]),
                    "uid": uid,
                    "title": f"✓ {current}",
                }
            )
        item["event_done"] = True
    if not jobs:
        return 0
    count = apple.retitle_events(jobs)
    save_plan(plan)
    return count


def cmd_roadmap(args) -> int:
    month = args.month or date.today().month
    print(render.roadmap_report(month, notion_src.roadmap_hint(month, refresh=args.refresh)))
    return 0


def cmd_lists(args) -> int:
    for name in apple.reminder_list_names():
        print(name)
    return 0


# ------------------------------------------------------------------ 入口


def main() -> int:
    ap = argparse.ArgumentParser(description="把 Notion 任務平鋪成待辦,重點才進行事曆")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="規劃未來一至兩週,只產生草案")
    p.add_argument("--projects", help="要排的專案前綴,逗號分隔")
    p.add_argument("--all", action="store_true", help="不指定專案,排全部")
    p.add_argument("--weeks", type=int, default=2)
    p.add_argument("--days", type=int, help="改用天數指定範圍,會覆蓋 --weeks")
    p.add_argument("--out", help="草案輸出路徑")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("projects", help="列出各專案的未完成件數")
    p.set_defaults(func=cmd_projects)

    p = sub.add_parser("commit", help="把草案寫入提醒事項清單")
    p.add_argument("--list", required=True, help="目標清單名稱")
    p.add_argument("--plan", help="草案路徑")
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("add", help="手動加待辦並排時段,stdin 收 JSON 陣列")
    p.add_argument("--list", required=True, help="目標清單名稱")
    p.add_argument("--no-schedule", action="store_true",
                   help="只建待辦不排時段,預設會直接卡進行事曆")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("focus", help="把待辦寫成行事曆事件,預設排全部還沒時段的")
    p.add_argument("--pick", help="只排這幾個編號,逗號分隔;不給就排全部未排的")
    p.add_argument("--split", action="store_true",
                   help="每個任務各自一個事件,預設同一天同專案會合併")
    p.add_argument("--plan", help="草案路徑")
    p.set_defaults(func=cmd_focus)

    p = sub.add_parser("status", help="對照行事曆現況,回報落差但不改行程")
    p.add_argument("--notify", action="store_true", help="推播早安提醒")
    p.add_argument("--plan", help="草案路徑")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("shift", help="依實際進度重排,預設只動逾期未完成的項目")
    # 這個模式同時涵蓋往前拉與往後推,所以主名叫 reflow。--pull 是舊名,留著相容。
    p.add_argument("--reflow", "--pull", dest="pull", action="store_true",
                   help="連後續未完成的項目一起重排,提早做完往前拉、落後則往後順延")
    p.add_argument("--weeks", type=int, default=2)
    p.add_argument("--apply", action="store_true", help="實際套用,不加只看預覽")
    p.add_argument("--plan", help="草案路徑")
    p.set_defaults(func=cmd_shift)

    p = sub.add_parser("today", help="顯示今天的待辦與時段")
    p.set_defaults(func=cmd_today)

    p = sub.add_parser("evening", help="產生下班收尾訊息")
    p.add_argument("--notify", action="store_true", help="同時發 Telegram 與本機通知")
    p.set_defaults(func=cmd_evening)

    p = sub.add_parser("rebalance", help="逾期未完成往後順延")
    p.add_argument("--weeks", type=int, default=2)
    p.add_argument("--days", type=int, help="改用天數指定範圍,會覆蓋 --weeks")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_rebalance)

    p = sub.add_parser("roadmap", help="顯示 2026 計畫頁的月度安排")
    p.add_argument("--month", type=int)
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_roadmap)

    p = sub.add_parser("lists", help="列出提醒事項清單")
    p.set_defaults(func=cmd_lists)

    args = ap.parse_args()
    try:
        return args.func(args)
    except apple.AppleError as exc:
        print(f"提醒事項或行事曆存取失敗:{exc}", file=sys.stderr)
        print("行事曆權限要選完整存取,僅新增事件無法讀取。", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"連不上 Notion:{exc.reason}", file=sys.stderr)
        print("這通常是網路瞬斷,直接重跑一次即可。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
