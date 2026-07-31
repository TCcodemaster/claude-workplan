# workplan

[![Claude Code](https://img.shields.io/badge/Claude%20Code-skill-D97757)](https://code.claude.com/docs)
[![macOS](https://img.shields.io/badge/macOS-13%2B-000000?logo=apple&logoColor=white)](https://www.apple.com/macos/)
[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Swift](https://img.shields.io/badge/Swift-EventKit-F05138?logo=swift&logoColor=white)](https://developer.apple.com/documentation/eventkit)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

以自然語言介面操作 Apple 提醒事項與行事曆的排程工具，實作為 Claude Code skill。

用 Swift 與 EventKit 存取系統資料，Python 負責排程演算法與狀態管理，兩者以 JSON 溝通。

## 功能

- 從自然語言描述建立待辦，掛上專案標籤，並將符合時長門檻者排入行事曆空檔
- 排程時避開既有行程、套用封鎖時段、超出單一空檔則切段、同日同專案合併時段
- 比對行事曆與提醒事項的現況，偵測事件位移、刪除、提前完成與逾期四種偏離
- 依偏離結果調整後續行程，預設僅重排逾期項目，其餘維持原位
- 專案分色，一個專案對應一本行事曆
- launchd 定時推播每日進度

## 設計約束

**時長門檻。** 短於 `MIN_EVENT_MINUTES`（預設 60 分）的工作不佔行事曆時段，僅保留於提醒事項。門檻套用於合併後的整組。

**狀態基準。** 排程時將時段寫入 `cache/plan-latest.json` 作為基準，後續才能比對出行事曆的手動變更。

**最小重排。** `shift` 預設僅處理逾期項目，將其插入既有空隙而不推移後續安排。整批重排需顯式指定 `--reflow`。

## 需求

macOS、Xcode Command Line Tools、Python 3.9+（只用標準庫）。Notion 與捷徑 app 選用。

## 安裝

**裝完還要跑初始化。** 它需要一支自己編譯的 EventKit 工具，以及行事曆的完整存取權限。

走 plugin，這兩行要你自己在 Claude Code 裡打，是 CLI 內建指令，Claude 代跑不了：

```
/plugin marketplace add TCcodemaster/claude-workplan
/plugin install workplan@workplan
```

或走 clone，這條路 Claude 可以全包，把這段丟給它：

> 幫我裝 https://github.com/TCcodemaster/claude-workplan 這個 skill。clone 下來跑初始化，讀出我現有的行事曆與提醒事項清單問我要用哪些，填好設定。有兩件事你代勞不了，記得提醒我。

手動的話：

```bash
git clone https://github.com/TCcodemaster/claude-workplan.git ~/claude-workplan
~/claude-workplan/setup.sh                 # 互動，會問要不要裝定時推播
~/claude-workplan/setup.sh --launchd       # 非互動，直接裝
~/claude-workplan/setup.sh --no-launchd    # 非互動，跳過
```

非互動環境（Claude Code、CI）不加旗標的話會跳過定時推播，不會卡在等輸入。

### 這兩件事只能你自己做

**授予行事曆「完整存取」。** 第一次執行會跳出系統對話框，Claude 點不到。選成「僅新增事件」的話讀不到既有行程，排程會把有事的時段當成空檔，而且不會報錯，只會安靜地排錯。權限授予的是執行它的那個 app，終端機與 Claude Code 可能要各授權一次。`bin/wpkit` 是 ad-hoc 簽章，重新編譯後可能再問一次。

**加入捷徑**（選用）：`skills/workplan/shortcuts/workplan-tags2.shortcut`。不裝的話待辦沒有專案標籤，其餘正常。

### 設定

編輯 `~/.claude/workplan/config.local.py`，`setup.sh` 會產生範本。至少確認 `TARGET_CALENDAR`（哪本行事曆，要先手動建好）、`BUSY_CALENDARS`（所有會佔用工作時間的行事曆，漏掉會排到已經有事的時段）、`WORK_BLOCKS`（可工作時段）。

設定與狀態都在家目錄，plugin 更新不會覆蓋。**不要改 `wp/config.py`**，那是預設值。

## 用法

在 Claude Code 裡直接講就行，以下是對應的 CLI。

```bash
# 排工作：建待辦、掛標籤、卡時段，一步到位
cat <<'JSON' | python3 workplan.py add --list 提醒事項
[{"project":"官網改版","title":"分析新需求","day":"2026-03-02","minutes":120}]
JSON

python3 workplan.py today                    # 今天要做什麼
python3 workplan.py status                   # 計畫跟現實差在哪
python3 workplan.py shift --apply            # 只挪逾期的，後面不動
python3 workplan.py shift --reflow --apply   # 後續一起重排
python3 workplan.py plan --projects 官網改版  # 從 Notion 拉任務（選用）
```

`status` 分五類回報：**動過**（自動校正）、**刪掉**、**提早**、**逾期**、**今天**。

`shift` 預設只補逾期的，逾期項去找空著的縫隙塞，縫隙不夠會直說找不到，讓你決定延長還是砍範圍。`--reflow` 才把後續一起重排，提早做完時往前拉、落後時往後順延。

## 對話介面

自然語言輸入對應到的實際操作：

| 輸入 | 執行 |
|---|---|
| 排下兩週的開發，每週 20 到 24 小時 | 依相依順序拆解、建待辦、排時段，回報各週時數與未排入項目 |
| 這件要花那麼久嗎 | 說明估時依據並提出修正，確認後同步更新待辦時數與行事曆事件 |
| 禮拜三下午都不要安排 | 寫入 `config.local.py` 的 `BLOCKED_BLOCKS`，後續排程持續套用 |
| （推播）昨天 2 件未完成 | 僅回報，待回覆後才執行 `shift` |

排程偏好以設定檔累積，不需重複指示。

## 實作細節

**分色以行事曆為單位。** EventKit 的顏色屬於 `EKCalendar` 而非 `EKEvent`，因此一個專案對應一本行事曆。

**同日同專案合併時段。** 上限為 `MERGE_MAX_TASKS`，超過則拆為獨立事件。

**待辦不帶時間。** `dueDateComponents` 僅填 year/month/day，時間資訊只存在於行事曆事件。

**metadata 不寫入備註。** 專案、順延次數、事件對應存於 `~/.claude/workplan/cache/plan-latest.json`，以提醒事項 id 為索引。

## 架構

```
setup.sh                    初始化
skills/workplan/
  SKILL.md                  給 Claude 讀的操作說明
  workplan.py               CLI 入口
  wp/planner.py             空檔計算，純函式
  wp/apple.py               bin/wpkit 的封裝
  src/wpkit.swift           EventKit 存取工具
  tests/test_planner.py     排程邏輯測試，不碰真實行事曆
~/.claude/workplan/         你的設定與狀態，更新不會動到
```

```bash
python3 skills/workplan/tests/test_planner.py
```

存取 Apple 資料一律走 `bin/wpkit`。**不要改回 AppleScript**，讀行事曆要三分半，EventKit 幾十毫秒，實測 `today` 從 212 秒降到 0.7 秒。

編譯有兩個坑，都在 `src/build.sh` 處理掉了：`Info.plist` 必須用 `-sectcreate` 嵌進執行檔，否則 EventKit 被 TCC 直接拒絕而且不會有彈窗；以及 ad-hoc 簽章。

原生標籤只能靠捷徑的 `is.workflow.actions.setters.reminders` 配 `WFContentItemPropertyName = Tags`。EventKit 沒有標籤 API，ReminderKit 私有框架會被權限擋。捷徑靠標題定位，所以建立時先用唯一標題、掛完標籤再改回正式名稱，少一步就會標到同名的錯誤那筆。

## 授權

MIT
