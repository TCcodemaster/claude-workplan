"""從 Notion 讀取任務,以及讀取年度計畫頁當作提示。

Notion 是選用的來源,主要流程(口述建待辦、排時段、動態調整)完全不需要它。
任務排序完全依任務本身的欄位,不引入專案層級的主觀權重。
年度計畫頁只用來在報告裡提示本月安排,不參與計算。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime

from . import config

# Notion client 來自另一個 skill,沒裝就讓相關指令在被呼叫時才報錯。
# 不能在 import 階段就炸掉,否則沒用 Notion 的人連 add 都不能跑。
sys.path.insert(0, str(config.NOTION_SKILL_DIR))
try:
    from notion import Notion, db_title, read_props  # noqa: E402

    NOTION_AVAILABLE = True
except ImportError:
    NOTION_AVAILABLE = False

    def _unavailable(*_args, **_kwargs):
        raise SystemExit(
            "這個指令需要 Notion client,但找不到它。\n"
            f"請把 notion.py 放到 {config.NOTION_SKILL_DIR},並設定 NOTION_TOKEN。\n"
            "不用 Notion 的話改用 add 直接建待辦,那條路不需要這個模組。"
        )

    Notion = _unavailable  # type: ignore[assignment]
    db_title = _unavailable  # type: ignore[assignment]
    read_props = _unavailable  # type: ignore[assignment]

FIELD_TITLE = ["任務名稱", "Name", "名稱", "重點摘要"]
FIELD_DUE = ["到期日", "Due", "日期"]
FIELD_PRIORITY = ["優先順序", "優先級", "Priority"]
FIELD_DIFFICULTY = ["難度", "Difficulty"]
FIELD_STATUS = ["狀態", "Status", "開發狀態"]
FIELD_STAGE = ["階段", "Stage"]


@dataclass
class NotionTask:
    project: str
    page_id: str
    url: str
    title: str
    due: date | None
    priority: str | None
    difficulty: str | None
    status: str | None
    stage: str | None

    @property
    def minutes(self) -> int:
        return config.DIFFICULTY_MINUTES.get(self.difficulty or "", config.DEFAULT_TASK_MINUTES)

    @property
    def sort_key(self):
        far = date(9999, 12, 31)
        return (
            self.due or far,
            config.PRIORITY_ORDER.get(self.priority or "", config.PRIORITY_DEFAULT),
            config.DIFFICULTY_ORDER.get(self.difficulty or "", config.DIFFICULTY_DEFAULT),
            self.title,
        )


def _pick(props: dict, candidates: list[str]):
    for name in candidates:
        if name in props and props[name] not in (None, "", []):
            return props[name]
    return None


def _as_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, dict):
        value = value.get("start")
    if not value:
        return None
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _is_done(status: str | None) -> bool:
    if not status:
        return False
    return any(k.lower() in status.lower() for k in config.DONE_STATUS_KEYWORDS)


def project_prefix(database_title: str, overrides: dict) -> str:
    prefixes = overrides.get("prefixes", overrides)
    if database_title in prefixes:
        return prefixes[database_title]
    name = database_title.strip()
    if name in prefixes:
        return prefixes[name]
    if name.startswith(config.TASK_DB_PREFIX):
        name = name[len(config.TASK_DB_PREFIX) :].lstrip("-－— ").strip()
    return name or database_title.strip()


def task_databases(client: Notion) -> list[tuple[str, str]]:
    """回傳 (database id, 顯示名稱),只取名稱以任務追蹤開頭的。"""
    found = []
    for db in client.search(config.TASK_DB_PREFIX, "database"):
        title = db_title(db).strip()
        if title.startswith(config.TASK_DB_PREFIX):
            found.append((db["id"], title))
    seen = set()
    unique = []
    for did, title in found:
        if did in seen:
            continue
        seen.add(did)
        unique.append((did, title))
    return sorted(unique, key=lambda x: x[1])


def open_tasks(
    client: Notion | None = None, projects: list[str] | None = None
) -> list[NotionTask]:
    """讀未完成任務。projects 指定要讀哪些專案前綴,不指定就讀全部。

    只讀指定專案能省下多數 API 呼叫,而且使用者本來就會先講這次要處理哪幾個。
    """
    client = client or Notion()
    overrides = config.load_project_map()
    wanted = {config.resolve_project(p) for p in projects} if projects else None
    tasks: list[NotionTask] = []
    for did, title in task_databases(client):
        prefix = project_prefix(title, overrides)
        if wanted is not None and prefix not in wanted:
            continue
        for row in client.query_database(did):
            props = read_props(row)
            status = _pick(props, FIELD_STATUS)
            if isinstance(status, list):
                status = ", ".join(str(s) for s in status)
            if _is_done(status):
                continue
            name = _pick(props, FIELD_TITLE)
            if not name:
                continue
            tasks.append(
                NotionTask(
                    project=prefix,
                    page_id=row["id"],
                    url=row.get("url", ""),
                    title=str(name).strip().replace("\n", " "),
                    due=_as_date(_pick(props, FIELD_DUE)),
                    priority=_pick(props, FIELD_PRIORITY),
                    difficulty=_pick(props, FIELD_DIFFICULTY),
                    status=status,
                    stage=_pick(props, FIELD_STAGE),
                )
            )
    tasks.sort(key=lambda t: t.sort_key)
    return tasks


def project_summary(client: Notion | None = None) -> list[dict]:
    """列出每個專案的未完成件數,給使用者挑這次要排哪些。"""
    client = client or Notion()
    overrides = config.load_project_map()
    rows = []
    for did, title in task_databases(client):
        prefix = project_prefix(title, overrides)
        undone = 0
        overdue = 0
        nearest: date | None = None
        today = date.today()
        for row in client.query_database(did):
            props = read_props(row)
            status = _pick(props, FIELD_STATUS)
            if isinstance(status, list):
                status = ", ".join(str(s) for s in status)
            if _is_done(status):
                continue
            undone += 1
            due = _as_date(_pick(props, FIELD_DUE))
            if due:
                if due < today:
                    overdue += 1
                if nearest is None or due < nearest:
                    nearest = due
        rows.append(
            {
                "project": prefix,
                "database": title,
                "undone": undone,
                "overdue": overdue,
                "nearest_due": nearest.isoformat() if nearest else None,
            }
        )
    rows.sort(key=lambda r: (-r["undone"], r["project"]))
    return rows


# ------------------------------------------------------------------ 路線圖提示


def _rich(items) -> str:
    return "".join(t.get("plain_text", "") for t in items)


def _find_tables(client: Notion, block_id: str, heading: str = "", depth: int = 0):
    if depth > 2:
        return
    res = client._request("GET", f"/blocks/{block_id}/children?page_size=100")
    for block in res.get("results", []):
        btype = block["type"]
        if btype.startswith("heading"):
            heading = _rich(block[btype]["rich_text"])
        if btype == "table":
            yield heading, block["id"]
        elif block.get("has_children") and btype != "child_database":
            yield from _find_tables(client, block["id"], heading, depth + 1)


def fetch_roadmap(client: Notion | None = None) -> dict:
    """抓年度計畫頁的季度表格,整理成 {月份: {專案: 安排}}。

    這是選用功能,沒設定 ROADMAP_PAGE_ID 就直接回空的,不影響其他流程。
    """
    if not config.ROADMAP_PAGE_ID:
        return {}
    client = client or Notion()
    months: dict[str, dict[str, str]] = {}
    for heading, table_id in _find_tables(client, config.ROADMAP_PAGE_ID):
        rows = client._request("GET", f"/blocks/{table_id}/children?page_size=100").get("results", [])
        if not rows:
            continue
        grid = [[_rich(c) for c in r["table_row"]["cells"]] for r in rows]
        header = grid[0]
        if not header or "月份" not in header[0]:
            continue
        for row in grid[1:]:
            if not row or not row[0].strip():
                continue
            month = row[0].strip()
            bucket = months.setdefault(month, {})
            for idx, cell in enumerate(row[1:], start=1):
                if idx >= len(header):
                    break
                col = header[idx].strip()
                text = cell.strip()
                if text and col:
                    bucket[col] = text.replace("\n", " / ")
    payload = {"fetched": datetime.now().strftime("%Y-%m-%d %H:%M"), "months": months}
    config.cache_path("roadmap.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def roadmap_hint(month: int, refresh: bool = False) -> dict[str, str]:
    """取某個月的路線圖安排,沒有快取時自動抓一次。"""
    path = config.cache_path("roadmap.json")
    if refresh or not path.exists():
        payload = fetch_roadmap()
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    key = f"{month} 月"
    return payload.get("months", {}).get(key, {})
