"""workplan 的預設設定。

**不要改這個檔案。** 這裡是預設值,而且會被 plugin 更新覆蓋掉。你自己的設定寫在
`~/.claude/workplan/config.local.py`,那份會在最後覆寫這裡的同名變數,更新不會動到它。
沒有那個檔案的話 `setup.sh` 會產生一份範本。

第一次使用請至少確認三件事:TARGET_CALENDAR 這本行事曆存在、BUSY_CALENDARS 涵蓋
你所有會佔用工作時間的行事曆、WORK_BLOCKS 符合你的作息。其餘都有堪用的預設值。
"""

from __future__ import annotations

import json
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REFERENCES_DIR = SKILL_DIR / "references"
NOTION_SKILL_DIR = Path.home() / ".claude" / "skills" / "notion"

# 使用者的狀態一律放家目錄,不能放在 skill 目錄裡。plugin 更新會覆蓋整個目錄,
# cache 被沖掉的話待辦與行事曆事件的對應就斷了,已排的東西全部標不回去。
USER_DIR = Path(Path.home() / ".claude" / "workplan")
CACHE_DIR = USER_DIR / "cache"
LOCAL_CONFIG = USER_DIR / "config.local.py"

# 行事曆與提醒事項
# 沒有對應到專案行事曆的工作會落在這一本,腳本不會自動建立它,請先在行事曆 app 裡建好。
TARGET_CALENDAR = "工作"
# 只是給提示用的清單名稱,實際清單以 `workplan.py lists` 讀到的為準。
KNOWN_REMINDER_LISTS = ["提醒事項"]

# 顏色是行事曆屬性,同一本行事曆的事件在週檢視裡必然同色,一週塞兩三個專案就看不出
# 誰是誰。所以主力專案各自一本行事曆,沒列到的專案落回 TARGET_CALENDAR。
# 藍與橘的區辨最強,對色盲也友善,其餘顏色也刻意選了亮度不同的色相。
# 建行事曆用 `bin/wpkit mkcalendar`,新增專案記得同步加進 BUSY_CALENDARS。
PROJECT_CALENDARS = {
    # "專案代號": "行事曆名稱",
}
PROJECT_CALENDAR_COLORS = {
    # "行事曆名稱": "#RRGGBB",
}

# 建議的分色順序,建新行事曆時照這個順序取用就不會撞色。
SUGGESTED_COLORS = [
    "#2E86C1",  # 藍
    "#E67E22",  # 橘
    "#16A34A",  # 綠
    "#DC2626",  # 紅
    "#0891B2",  # 青
    "#CA8A04",  # 金
    "#7C3AED",  # 紫藍
]


def calendar_for(project: str) -> str:
    return PROJECT_CALENDARS.get(project, TARGET_CALENDAR)


def workplan_calendars() -> list[str]:
    """workplan 自己會寫入的所有行事曆。"""
    return [TARGET_CALENDAR] + sorted(set(PROJECT_CALENDARS.values()))


# 判斷時段是否被佔用時只讀這幾個行事曆。節日、生日、Siri 建議不影響工作時段,
# 而且它們資料量大會讓查詢慢好幾分鐘。專案分色行事曆一定要在這裡,
# 少一本就會把已經排好的工作當成空檔重複排進去。
# 把你自己的私人行事曆(家庭、公司 Google 帳號)也加進來,才不會排到已經有事的時段。
BUSY_CALENDARS = [
    TARGET_CALENDAR,
] + list(PROJECT_CALENDARS.values())

# 每日負載上限
PROJECTS_PER_DAY = 3
TASKS_PER_PROJECT_PER_DAY = 3
DAILY_MAX_TASKS = 8
WORK_WEEKDAYS = {0, 1, 2, 3, 4}  # 週一到週五

# 同一天同一專案的任務合併成一個行事曆時段,超過這個數量就拆成獨立事件,
# 因為一個事件塞四五件事,標題會長到看不出重點。
MERGE_MAX_TASKS = 3

# 行事曆事件的提醒,單位是分鐘,代表開始前幾分鐘通知。
# EventKit 建立事件預設不帶提醒,不設的話到時間不會通知。
EVENT_ALARM_MINUTES = 10

# 行事曆事件的可排時段,只用於你挑出來的重點任務
WORK_BLOCKS = [("09:00", "12:00"), ("13:00", "18:00")]
SLOT_GRANULARITY = 15

# 固定不排工作的時段,key 是 weekday(週一為 0)。例如把週三下午留給例行會議與雜事
# 就寫成 {2: [("13:00", "18:00")]}。預設不封鎖任何時段。
BLOCKED_BLOCKS = {}

# 這些既有事件不算佔用時段,工作可以跟它們重疊。標題含關鍵字即比對成功。
# 適合放那種掛著但不需要專心的行程,例如純簽到的例行會議。
OVERLAPPABLE_EVENT_TITLES = []

# 一件任務的時數塞不進任何單一空檔時,允許切成幾段分散在同一天,
# 每段不得短於這個分鐘數,免得切出一堆碎片。
SPLIT_MIN_SEGMENT = 60

# 短過這個長度的工作不進行事曆,只留在提醒事項。行事曆要保護的是整段不被打斷的
# 時間,半小時的雜事塞進去只會把版面切碎,真正的重點反而看不出來。
# 門檻套在合併後的整組上,所以同一天同專案的三件小事湊滿一小時還是會排進去。
MIN_EVENT_MINUTES = 60

# 難度換算成分鐘,只用於行事曆事件的長度
# 估時一律從 30 分起跳,不要一開始就抓很久。寧可低估再追加時段,
# 抓太久會把一天的空檔吃光,而且看起來壓力很大。
DIFFICULTY_MINUTES = {"小": 30, "中": 60, "大": 90}
DEFAULT_TASK_MINUTES = 30
MIN_TASK_MINUTES = 30

# 任務排序用的權重,全部來自任務本身的欄位
PRIORITY_ORDER = {"高": 0, "中": 1, "低": 2}
PRIORITY_DEFAULT = 1
DIFFICULTY_ORDER = {"大": 0, "中": 1, "小": 2}
DIFFICULTY_DEFAULT = 1

# 視為未完成的判斷:狀態含以下關鍵字即視為已結束
DONE_STATUS_KEYWORDS = ["已完成", "完成", "Done", "取消", "已取消", "Archived"]

# Notion 來源(選用)。沒有 Notion 也能用,那時只走口述建待辦的流程。
# TASK_DB_PREFIX 是任務資料庫的共同名稱前綴,例如「任務追蹤-專案A」就填「任務追蹤」。
# ROADMAP_PAGE_ID 是年度計畫頁,只在報告尾端當提示顯示,留空就不顯示。
TASK_DB_PREFIX = "任務追蹤"
ROADMAP_PAGE_ID = ""

# 提醒事項的識別標記。專案名稱同時寫進標題開頭與原生標籤,
# 標題讓搜尋找得到,標籤讓側邊欄能篩選。行事曆事件不加標籤。
TITLE_PREFIX_FMT = "#{project} "
BODY_MARKER = "⟦workplan⟧"

# 掛原生標籤要靠捷徑,EventKit 沒有標籤 API。捷徑檔在 shortcuts/ 目錄。
TAG_SHORTCUT = "workplan-tags2"

# 順延次數超過此值就提出砍掉的建議
DEFER_WARN_COUNT = 2


def load_project_map() -> dict:
    """讀專案名稱對應表,回傳 {"prefixes": {...}, "aliases": {...}}。"""
    path = REFERENCES_DIR / "project_map.json"
    if not path.exists():
        return {"prefixes": {}, "aliases": {}}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "prefixes" in raw or "aliases" in raw:
        return {
            "prefixes": raw.get("prefixes", {}),
            "aliases": raw.get("aliases", {}),
        }
    # 舊格式是平的 database 名稱對前綴,照樣支援。
    return {"prefixes": {k: v for k, v in raw.items() if not k.startswith("_")}, "aliases": {}}


def resolve_project(name: str) -> str:
    """把使用者輸入的專案名稱正規化成待辦前綴。

    同一個專案往往有好幾種口語說法,全部都要認得,所以走別名表,
    找不到就原樣回傳,由呼叫方決定要不要提醒使用者。
    """
    mapping = load_project_map()
    text = name.strip()
    lowered = text.lower()

    for alias, prefix in mapping["aliases"].items():
        if alias.lower() == lowered:
            return prefix
    for prefix in mapping["prefixes"].values():
        if prefix.lower() == lowered:
            return prefix
    for db_name, prefix in mapping["prefixes"].items():
        if db_name.lower() == lowered:
            return prefix
    return text


def known_prefixes() -> list[str]:
    mapping = load_project_map()
    return sorted(set(mapping["prefixes"].values()))


def cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / name


# ---------------------------------------------------------------- 使用者覆寫

def _load_local_config() -> set[str]:
    """套用 ~/.claude/workplan/config.local.py 的設定,回傳它定義了哪些變數。

    用 exec 而不是 import,這樣使用者的檔案可以只寫要改的那幾行,不必照抄整份。
    載入失敗一律直接中止,設定錯了卻默默用預設值排出來的行程更危險。
    """
    if not LOCAL_CONFIG.exists():
        return set()
    namespace: dict = {}
    try:
        code = compile(LOCAL_CONFIG.read_text(encoding="utf-8"), str(LOCAL_CONFIG), "exec")
        exec(code, namespace)  # noqa: S102
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"讀取 {LOCAL_CONFIG} 失敗:{exc}")
    overrides = {k: v for k, v in namespace.items() if not k.startswith("_")}
    globals().update(overrides)
    return set(overrides)


_OVERRIDDEN = _load_local_config()

# 使用者只設了 PROJECT_CALENDARS 卻沒動 BUSY_CALENDARS 的話要自動補上,
# 少一本行事曆就會把已經排好的工作當成空檔重複排進去。
if "BUSY_CALENDARS" not in _OVERRIDDEN:
    BUSY_CALENDARS = [TARGET_CALENDAR] + sorted(set(PROJECT_CALENDARS.values()))
