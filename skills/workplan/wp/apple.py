"""提醒事項與行事曆的橋接層,底層是 EventKit。

實際存取由 bin/wpkit 這個 Swift 工具負責,因為 AppleScript 讀行事曆要三分半,
EventKit 只要幾十毫秒。資料一律走 stdin 與 stdout 的 JSON。

提醒事項的到期日只帶日期不帶時間,由 wpkit 用 EventKit 的
dueDateComponents 只填年月日達成。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import date, datetime

from . import config

WPKIT = config.SKILL_DIR / "bin" / "wpkit"
TIMEOUT = 120


class AppleError(RuntimeError):
    pass


def _count(result) -> int:
    """setreminders 這類命令回的是 {"count": n}。"""
    if isinstance(result, dict):
        return int(result.get("count", 0))
    return int(result or 0)


def _setup_script() -> str:
    """找 setup.sh。plugin 結構下它在 repo 根,也就是 skill 目錄的上兩層。"""
    for candidate in (
        config.SKILL_DIR.parent.parent / "setup.sh",
        config.SKILL_DIR / "setup.sh",
    ):
        if candidate.exists():
            return str(candidate)
    return str(config.SKILL_DIR.parent.parent / "setup.sh")


SETUP_HINT = f"""還沒完成初始化。workplan 需要一支自己編譯的 EventKit 工具才能讀寫
提醒事項與行事曆,plugin 安裝不會自動做這一步。請執行:

    {_setup_script()}

它會編譯工具、要求系統權限、並產生你的設定檔。行事曆權限請選「完整存取」,
只給「僅新增事件」的話讀不到既有行程,排程會把已經有事的時段當成空檔。"""

PERMISSION_HINT = """行事曆或提醒事項的權限不足。到系統設定的隱私權與安全性,
把終端機(或你執行 Claude Code 的那個 app)的行事曆權限改成「完整存取」。"""


def _wpkit(args: list[str], payload=None):
    if not WPKIT.exists():
        raise SystemExit(SETUP_HINT)
    proc = subprocess.run(
        [str(WPKIT), *args],
        input=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    if proc.returncode != 0:
        message = proc.stderr.strip()
        # wpkit 的權限錯誤訊息對第一次使用的人太簡略,補上實際該去哪裡開。
        if "權限" in message or "存取" in message:
            raise SystemExit(f"{message}\n\n{PERMISSION_HINT}")
        raise AppleError(message or "wpkit 執行失敗")
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def q(text: str) -> str:
    """把字串轉成 AppleScript 字面值,只有本機通知還用得到。"""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# ------------------------------------------------------------------ 資料模型


@dataclass
class Reminder:
    list_name: str
    rid: str
    title: str
    due: date | None
    has_time: bool
    body: str | None
    completed: bool
    completion_date: datetime | None

    @property
    def is_workplan(self) -> bool:
        return bool(self.body and config.BODY_MARKER in self.body)

    @property
    def project(self) -> str | None:
        """從標題開頭的 hashtag 取專案,舊格式的方括號前綴也照樣認。"""
        if self.title.startswith("#"):
            return self.title[1:].split(" ", 1)[0] or None
        if self.title.startswith("["):
            end = self.title.find("]")
            return self.title[1:end] if end > 1 else None
        return None

    @property
    def plain_title(self) -> str:
        if self.title.startswith("#"):
            parts = self.title.split(" ", 1)
            return parts[1].strip() if len(parts) > 1 else ""
        if self.title.startswith("[") and "]" in self.title:
            return self.title[self.title.find("]") + 1 :].strip()
        return self.title

    @property
    def defer_count(self) -> int:
        for line in (self.body or "").splitlines():
            if line.startswith("順延:"):
                try:
                    return int(line.split(":", 1)[1].strip())
                except ValueError:
                    return 0
        return 0

    @property
    def source_url(self) -> str | None:
        for line in (self.body or "").splitlines():
            if line.startswith("來源:"):
                return line.split(":", 1)[1].strip()
        return None


@dataclass
class CalEvent:
    calendar: str
    uid: str
    title: str
    start: datetime | None
    end: datetime | None
    allday: bool
    notes: str | None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


# ------------------------------------------------------------------ 讀取


def reminder_list_names() -> list[str]:
    return _wpkit(["lists"]) or []


def calendar_names() -> list[dict]:
    return _wpkit(["calendars"]) or []


def read_reminders(include_completed: bool = False, workplan_only: bool = False) -> list[Reminder]:
    args = ["reminders"]
    if include_completed:
        args.append("all")
    rows = _wpkit(args) or []
    out = []
    for r in rows:
        body = r.get("notes")
        if workplan_only and not (body and config.BODY_MARKER in body):
            continue
        out.append(
            Reminder(
                list_name=r.get("list") or "",
                rid=r["id"],
                title=r.get("title") or "",
                due=_parse_day(r.get("due")),
                has_time=bool(r.get("hasTime")),
                body=body,
                completed=bool(r.get("completed")),
                completion_date=_parse_dt(r.get("completionDate")),
            )
        )
    return out


def read_events(
    start: datetime, end: datetime, calendars: list[str] | None = None
) -> list[CalEvent]:
    """讀事件。預設只讀會佔用工作時段的那幾個行事曆。"""
    names = calendars if calendars is not None else config.BUSY_CALENDARS
    rows = _wpkit(["events", start.isoformat(), end.isoformat(), ",".join(names)]) or []
    return [
        CalEvent(
            calendar=e.get("calendar") or "",
            uid=e.get("uid") or "",
            title=e.get("title") or "",
            start=_parse_dt(e.get("start")),
            end=_parse_dt(e.get("end")),
            allday=bool(e.get("allday")),
            notes=e.get("notes"),
        )
        for e in rows
    ]


# ------------------------------------------------------------------ 寫入


def create_reminders(list_name: str, items: list[dict]) -> list[str]:
    """建立提醒事項,只設日期不設時間。items 每筆要有 title、due、body。"""
    if not items:
        return []
    payload = [
        {
            "list": list_name,
            "title": it["title"],
            "due": it["due"].isoformat() if isinstance(it["due"], date) else it["due"],
            "notes": it.get("body") or "",
        }
        for it in items
    ]
    return _wpkit(["mkreminders"], payload) or []


def update_reminder_dates(updates: list[dict]) -> int:
    """依 id 改日期與備註。updates 每筆要有 rid,可帶 due、body、completed。"""
    if not updates:
        return 0
    payload = []
    for u in updates:
        row = {"id": u["rid"]}
        if u.get("due") is not None:
            due = u["due"]
            row["due"] = due.isoformat() if isinstance(due, date) else due
        if u.get("body") is not None:
            row["notes"] = u["body"]
        if u.get("completed") is not None:
            row["completed"] = bool(u["completed"])
        payload.append(row)
    return _count(_wpkit(["setreminders"], payload))


def delete_reminders(rids: list[str]) -> int:
    if not rids:
        return 0
    return _count(_wpkit(["delreminders"], rids))


def create_events(calendar: str, items: list[dict]) -> dict[str, str]:
    """建立事件,回傳 key 對 uid。items 每筆要有 key、title、start、end、notes。

    每筆可以帶 calendar 覆寫,讓不同專案落到自己那本分色行事曆。
    """
    if not items:
        return {}
    payload = [
        {
            "calendar": it.get("calendar") or calendar,
            "key": it["key"],
            "title": it["title"],
            "start": it["start"].isoformat(),
            "end": it["end"].isoformat(),
            "notes": it.get("notes") or "",
            "alarmMinutes": it.get("alarm_minutes", config.EVENT_ALARM_MINUTES),
        }
        for it in items
    ]
    made = _wpkit(["mkevents"], payload) or []
    return {m["key"]: m["uid"] for m in made}


def set_alarms(uids: list[str], minutes: int | None = None) -> int:
    """替既有事件補上提醒,minutes 是開始前幾分鐘。"""
    if not uids:
        return 0
    offset = config.EVENT_ALARM_MINUTES if minutes is None else minutes
    payload = [{"uid": u, "alarmMinutes": offset} for u in uids]
    return _count(_wpkit(["setalarms"], payload))


def retitle_events(jobs: list[dict]) -> int:
    """改事件標題,用來標記完成。jobs 每筆要有 uid、title。"""
    if not jobs:
        return 0
    payload = [{"uid": j["uid"], "title": j["title"]} for j in jobs]
    return _count(_wpkit(["retitle"], payload))


def set_titles(jobs: list[dict]) -> int:
    """只改標題。jobs 每筆要有 rid 與 title。"""
    if not jobs:
        return 0
    payload = [{"id": j["rid"], "title": j["title"]} for j in jobs]
    return _count(_wpkit(["setreminders"], payload))


def apply_tags(items: list[dict]) -> tuple[int, list[str]]:
    """把專案掛成提醒事項的原生標籤。

    EventKit 沒有標籤 API,ReminderKit 寫入被權限擋,唯一可行的是捷徑。
    捷徑用標題與清單名稱找到那筆提醒,再設定標籤,所以標題必須唯一。
    items 每筆要有 title(完整標題)、list、tag。回傳成功筆數與失敗的標題。
    """
    ok = 0
    failed: list[str] = []
    for it in items:
        payload = json.dumps(
            {
                "title": it["title"],
                "list": it["list"],
                "tags": it["tag"],
                "url": "",
                "parentTitle": "",
            },
            ensure_ascii=False,
        )
        proc = subprocess.run(
            ["/usr/bin/shortcuts", "run", config.TAG_SHORTCUT],
            input=payload,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode == 0:
            ok += 1
        else:
            failed.append(it["title"])
    return ok, failed


def tag_shortcut_installed() -> bool:
    proc = subprocess.run(
        ["/usr/bin/shortcuts", "list"], capture_output=True, text=True, timeout=30
    )
    return config.TAG_SHORTCUT in proc.stdout.splitlines()


def delete_events(uids: list[str]) -> int:
    if not uids:
        return 0
    return _count(_wpkit(["delevents"], uids))
