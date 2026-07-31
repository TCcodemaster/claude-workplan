"""planner 的空檔計算測試。

這裡測的是純函式,沒有 EventKit、沒有 Notion、不碰你的行事曆,可以放心跑。

    python3 tests/test_planner.py
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wp import config, planner  # noqa: E402
from wp.apple import CalEvent  # noqa: E402

MORNING = datetime(2026, 3, 2, 8, 0)  # 週一早上,還沒開始上班


def ev(start: str, end: str, title: str = "既有行程", allday: bool = False) -> CalEvent:
    day = date(2026, 3, 2)
    parse = lambda t: datetime.combine(day, datetime.strptime(t, "%H:%M").time())  # noqa: E731
    return CalEvent(
        calendar="工作",
        uid=f"{title}-{start}",
        title=title,
        start=None if allday else parse(start),
        end=None if allday else parse(end),
        allday=allday,
        notes=None,
    )


def spans(slots) -> list[tuple[str, str]]:
    return [(s.start.strftime("%H:%M"), s.end.strftime("%H:%M")) for s in slots]


class DaySlots(unittest.TestCase):
    def test_預設兩個區塊(self):
        got = planner.day_slots(date(2026, 3, 2), MORNING)
        self.assertEqual(spans(got), [("09:00", "12:00"), ("13:00", "18:00")])

    def test_已經過去的區塊不會排(self):
        afternoon = datetime(2026, 3, 2, 14, 30)
        got = planner.day_slots(date(2026, 3, 2), afternoon)
        self.assertEqual(spans(got), [("14:30", "18:00")])

    def test_封鎖時段會被扣掉(self):
        original = config.BLOCKED_BLOCKS
        config.BLOCKED_BLOCKS = {0: [("13:00", "18:00")]}  # 週一下午封鎖
        try:
            got = planner.day_slots(date(2026, 3, 2), MORNING)
            self.assertEqual(spans(got), [("09:00", "12:00")])
        finally:
            config.BLOCKED_BLOCKS = original

    def test_封鎖只影響指定的星期(self):
        original = config.BLOCKED_BLOCKS
        config.BLOCKED_BLOCKS = {2: [("13:00", "18:00")]}  # 只封週三
        try:
            got = planner.day_slots(date(2026, 3, 2), MORNING)  # 這天是週一
            self.assertEqual(spans(got), [("09:00", "12:00"), ("13:00", "18:00")])
        finally:
            config.BLOCKED_BLOCKS = original


class SubtractBusy(unittest.TestCase):
    def setUp(self):
        self.slots = planner.day_slots(date(2026, 3, 2), MORNING)

    def test_中間挖一個洞(self):
        got = planner.subtract_busy(self.slots, [ev("14:00", "15:00")])
        self.assertEqual(spans(got), [("09:00", "12:00"), ("13:00", "14:00"), ("15:00", "18:00")])

    def test_整段被吃掉(self):
        got = planner.subtract_busy(self.slots, [ev("09:00", "12:00")])
        self.assertEqual(spans(got), [("13:00", "18:00")])

    def test_全天事件不算佔用(self):
        got = planner.subtract_busy(self.slots, [ev("", "", "請假", allday=True)])
        self.assertEqual(spans(got), [("09:00", "12:00"), ("13:00", "18:00")])

    def test_可重疊的行程不算佔用(self):
        original = config.OVERLAPPABLE_EVENT_TITLES
        config.OVERLAPPABLE_EVENT_TITLES = ["簽到"]
        try:
            got = planner.subtract_busy(self.slots, [ev("09:15", "10:15", "每日簽到會議")])
            self.assertEqual(spans(got), [("09:00", "12:00"), ("13:00", "18:00")])
        finally:
            config.OVERLAPPABLE_EVENT_TITLES = original

    def test_碎片小於粒度就丟掉(self):
        # 09:00-09:10 只剩十分鐘,短於 SLOT_GRANULARITY,不該留下來
        got = planner.subtract_busy(self.slots, [ev("09:10", "12:00")])
        self.assertEqual(spans(got), [("13:00", "18:00")])


class Place(unittest.TestCase):
    def test_放進第一個塞得下的空檔(self):
        slots = planner.day_slots(date(2026, 3, 2), MORNING)
        got = planner.place(slots, 120)
        self.assertIsNotNone(got)
        self.assertEqual(got[0].strftime("%H:%M"), "09:00")
        self.assertEqual(got[1].strftime("%H:%M"), "11:00")
        # 用掉的部分要從空檔清單扣掉
        self.assertEqual(spans(slots), [("11:00", "12:00"), ("13:00", "18:00")])

    def test_塞不下就跳到下一個空檔(self):
        slots = planner.day_slots(date(2026, 3, 2), MORNING)
        got = planner.place(slots, 240)  # 四小時,上午的三小時放不下
        self.assertEqual(got[0].strftime("%H:%M"), "13:00")

    def test_全部都塞不下就回空值(self):
        slots = planner.day_slots(date(2026, 3, 2), MORNING)
        self.assertIsNone(planner.place(slots, 600))


class PlaceSplit(unittest.TestCase):
    def test_切成同一天的兩段(self):
        slots = planner.day_slots(date(2026, 3, 2), MORNING)
        got = planner.place_split(slots, 360)  # 六小時,單一空檔都放不下
        self.assertEqual(
            [(a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b in got],
            [("09:00", "12:00"), ("13:00", "16:00")],
        )

    def test_總時數不夠就回空清單(self):
        slots = planner.day_slots(date(2026, 3, 2), MORNING)
        self.assertEqual(planner.place_split(slots, 600), [])

    def test_每段不短於下限(self):
        slots = planner.day_slots(date(2026, 3, 2), MORNING)
        got = planner.place_split(slots, 200)
        for a, b in got:
            self.assertGreaterEqual((b - a).total_seconds() / 60, config.SPLIT_MIN_SEGMENT)

    def test_切段後總時數不變(self):
        slots = planner.day_slots(date(2026, 3, 2), MORNING)
        got = planner.place_split(slots, 300)
        total = sum((b - a).total_seconds() / 60 for a, b in got)
        self.assertEqual(total, 300)


class Workdays(unittest.TestCase):
    def test_跳過週末(self):
        got = planner.workdays(date(2026, 3, 6), 4)  # 週五起算四天
        self.assertEqual([d.isoformat() for d in got], ["2026-03-06", "2026-03-09"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
