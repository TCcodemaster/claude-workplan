"""把任務平鋪到工作日,以及替重點任務在行事曆找空檔。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from . import config
from .apple import CalEvent


@dataclass
class Assignment:
    project: str
    title: str
    day: date
    minutes: int
    due: date | None = None
    priority: str | None = None
    difficulty: str | None = None
    status: str | None = None
    url: str = ""
    page_id: str = ""
    late: bool = False
    overdue: bool = False
    rid: str | None = None
    defer_count: int = 0

    @property
    def display(self) -> str:
        return config.TITLE_PREFIX_FMT.format(project=self.project) + self.title


@dataclass
class DayLoad:
    day: date
    count: int = 0
    projects: dict[str, int] = field(default_factory=dict)

    def can_take(self, project: str) -> bool:
        if self.count >= config.DAILY_MAX_TASKS:
            return False
        if project in self.projects:
            return self.projects[project] < config.TASKS_PER_PROJECT_PER_DAY
        return len(self.projects) < config.PROJECTS_PER_DAY

    def take(self, project: str) -> None:
        self.count += 1
        self.projects[project] = self.projects.get(project, 0) + 1


def workdays(start: date, span_days: int) -> list[date]:
    """回傳從 start 起算 span_days 個日曆日之內的工作日。"""
    days: list[date] = []
    cursor = start
    horizon = start + timedelta(days=span_days)
    while cursor < horizon:
        if cursor.weekday() in config.WORK_WEEKDAYS:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def build_loads(days: list[date], occupied: list[Assignment]) -> dict[date, DayLoad]:
    loads = {d: DayLoad(d) for d in days}
    for item in occupied:
        load = loads.get(item.day)
        if load:
            load.take(item.project)
    return loads


def distribute(
    tasks: list,
    days: list[date],
    loads: dict[date, DayLoad],
    today: date,
) -> tuple[list[Assignment], list]:
    """依序把任務放進最早有容量的工作日。

    回傳已指派清單與放不下的任務。排序完全依任務本身的欄位,
    這裡只負責遵守每日容量與到期日約束。
    """
    assigned: list[Assignment] = []
    leftover = []

    for task in tasks:
        target = None
        late = False
        if task.due:
            for day in days:
                if day > task.due:
                    break
                if loads[day].can_take(task.project):
                    target = day
                    break
        if target is None:
            for day in days:
                if loads[day].can_take(task.project):
                    target = day
                    late = bool(task.due)
                    break
        if target is None:
            leftover.append(task)
            continue
        loads[target].take(task.project)
        assigned.append(
            Assignment(
                project=task.project,
                title=task.title,
                day=target,
                minutes=task.minutes,
                due=task.due,
                priority=task.priority,
                difficulty=task.difficulty,
                status=task.status,
                url=getattr(task, "url", ""),
                page_id=getattr(task, "page_id", ""),
                late=late,
                overdue=bool(task.due and task.due < today),
            )
        )
    assigned.sort(key=lambda a: (a.day, a.project, a.title))
    return assigned, leftover


def redistribute(
    stale: list[Assignment],
    days: list[date],
    loads: dict[date, DayLoad],
    today: date,
) -> tuple[list[Assignment], list[Assignment]]:
    """把逾期未完成的項目往後挪到最早有容量的工作日。"""
    moved: list[Assignment] = []
    stuck: list[Assignment] = []
    stale = sorted(stale, key=lambda a: (a.due or a.day, a.project, a.title))
    for item in stale:
        target = None
        for day in days:
            if day < today:
                continue
            if loads[day].can_take(item.project):
                target = day
                break
        if target is None:
            stuck.append(item)
            continue
        loads[target].take(item.project)
        item.day = target
        item.defer_count += 1
        moved.append(item)
    moved.sort(key=lambda a: (a.day, a.project, a.title))
    return moved, stuck


# ------------------------------------------------------------------ 行事曆空檔


@dataclass
class Slot:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


def _parse_hhmm(text: str) -> time:
    hour, minute = text.split(":")
    return time(int(hour), int(minute))


def day_slots(day: date, now: datetime) -> list[Slot]:
    slots = []
    for start_txt, end_txt in config.WORK_BLOCKS:
        start = datetime.combine(day, _parse_hhmm(start_txt))
        end = datetime.combine(day, _parse_hhmm(end_txt))
        if end <= now:
            continue
        if start < now:
            step = config.SLOT_GRANULARITY
            bumped = now.replace(second=0, microsecond=0)
            bumped += timedelta(minutes=(step - bumped.minute % step) % step)
            start = max(start, bumped)
            if start >= end:
                continue
        slots.append(Slot(start, end))
    return _subtract_blocked(slots, day)


def _subtract_blocked(slots: list[Slot], day: date) -> list[Slot]:
    """扣掉固定封鎖時段,例如週三下午。"""
    blocked = config.BLOCKED_BLOCKS.get(day.weekday(), [])
    result = slots
    for start_txt, end_txt in blocked:
        b_start = datetime.combine(day, _parse_hhmm(start_txt))
        b_end = datetime.combine(day, _parse_hhmm(end_txt))
        nxt: list[Slot] = []
        for slot in result:
            if b_end <= slot.start or b_start >= slot.end:
                nxt.append(slot)
                continue
            if b_start > slot.start:
                nxt.append(Slot(slot.start, min(b_start, slot.end)))
            if b_end < slot.end:
                nxt.append(Slot(max(b_end, slot.start), slot.end))
        result = [s for s in nxt if s.minutes >= config.SLOT_GRANULARITY]
    result.sort(key=lambda s: s.start)
    return result


def _is_overlappable(event: CalEvent) -> bool:
    """判斷這個既有事件能不能被工作蓋過去。"""
    title = event.title or ""
    return any(kw in title for kw in config.OVERLAPPABLE_EVENT_TITLES)


def subtract_busy(slots: list[Slot], events: list[CalEvent]) -> list[Slot]:
    result = slots
    for event in events:
        if event.allday or not event.start or not event.end:
            continue
        if _is_overlappable(event):
            continue
        nxt: list[Slot] = []
        for slot in result:
            if event.end <= slot.start or event.start >= slot.end:
                nxt.append(slot)
                continue
            if event.start > slot.start:
                nxt.append(Slot(slot.start, min(event.start, slot.end)))
            if event.end < slot.end:
                nxt.append(Slot(max(event.end, slot.start), slot.end))
        result = [s for s in nxt if s.minutes >= config.SLOT_GRANULARITY]
    result.sort(key=lambda s: s.start)
    return result


def place(slots: list[Slot], minutes: int) -> tuple[datetime, datetime] | None:
    """在空檔清單裡找位置,找到就把該空檔切掉。"""
    for idx, slot in enumerate(slots):
        end = slot.start + timedelta(minutes=minutes)
        if end > slot.end:
            continue
        start = slot.start
        remainder = Slot(end, slot.end)
        slots[idx : idx + 1] = [remainder] if remainder.minutes >= config.SLOT_GRANULARITY else []
        return start, end
    return None


def place_split(slots: list[Slot], minutes: int) -> list[tuple[datetime, datetime]]:
    """單一空檔塞不下時,把時數切成幾段分散在同一天。

    先照時間順序填滿較早的空檔,填不完就往後找。每段不短於 SPLIT_MIN_SEGMENT,
    切太碎的話行事曆上看起來是一堆零星小事件,反而看不出這天在做什麼。
    全部空檔加起來還是不夠就回傳空清單,由呼叫方當成排不進去處理。
    """
    usable = [s for s in slots if s.minutes >= config.SPLIT_MIN_SEGMENT]
    if sum(s.minutes for s in usable) < minutes:
        return []
    segments: list[tuple[datetime, datetime]] = []
    left = minutes
    for slot in sorted(usable, key=lambda s: s.start):
        if left <= 0:
            break
        take = min(left, slot.minutes)
        if take < config.SPLIT_MIN_SEGMENT:
            continue
        # 剩下的尾數如果會短於下限,就跟這一段一起吃掉,免得切出碎片。
        if 0 < left - take < config.SPLIT_MIN_SEGMENT and slot.minutes >= left:
            take = left
        end = slot.start + timedelta(minutes=take)
        segments.append((slot.start, end))
        left -= take
        # 用 identity 找位置。Slot 是 dataclass,切出來的餘段有可能跟別的空檔數值相同,
        # 用 index() 比對值會改到錯的那一個。
        idx = next(i for i, s in enumerate(slots) if s is slot)
        remainder = Slot(end, slot.end)
        slots[idx : idx + 1] = [remainder] if remainder.minutes >= config.SLOT_GRANULARITY else []
    if left > 0:
        return []
    return segments
