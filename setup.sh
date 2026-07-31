#!/bin/zsh
# workplan 初始化。編譯 wpkit、檢查權限、選擇性安裝早晚推播的排程。
set -euo pipefail

# --launchd 直接裝定時推播,--no-launchd 直接跳過。都沒給而且是互動終端才會問。
# Claude Code 或 CI 這種非互動環境跑到 read 會卡住,所以一定要能用旗標決定。
want_launchd=""
for arg in "$@"; do
  case "$arg" in
    --launchd) want_launchd="yes" ;;
    --no-launchd) want_launchd="no" ;;
    -h|--help)
      print "用法:setup.sh [--launchd|--no-launchd]"
      print "  --launchd     安裝平日 08:30 與 18:00 的定時推播"
      print "  --no-launchd  跳過定時推播"
      print "  兩者都不給時,互動終端會詢問,非互動環境預設跳過"
      exit 0
      ;;
  esac
done

repo=${0:A:h}
here=$repo/skills/workplan          # 腳本實際所在,plugin 的標準結構是 skills/<name>/
skill_target="$HOME/.claude/skills/workplan"

print "== 1. 檢查環境 =="
if ! command -v swiftc >/dev/null; then
  print "找不到 swiftc。請先安裝 Xcode Command Line Tools:xcode-select --install"
  exit 1
fi
print "swiftc 有了"
/usr/bin/python3 --version

print "\n== 2. 編譯 wpkit =="
zsh "$here/src/build.sh"

print "\n== 3. 確認行事曆與提醒事項權限 =="
# 第一次執行會跳出系統授權對話框。行事曆一定要給「完整存取」,
# 只給「僅新增事件」的話讀不到既有行程,排程會把已經有事的時段當成空檔。
if "$here/bin/wpkit" calendars >/dev/null 2>&1; then
  print "行事曆讀取正常,現有行事曆:"
  "$here/bin/wpkit" calendars
else
  print "行事曆讀取失敗。到系統設定的隱私權與安全性,把終端機的行事曆權限改成「完整存取」。"
  exit 1
fi
print "\n提醒事項清單:"
"$here/bin/wpkit" lists

print "\n== 4. 產生你的設定檔 =="
user_dir="$HOME/.claude/workplan"
local_config="$user_dir/config.local.py"
mkdir -p "$user_dir/cache"
if [[ -f "$local_config" ]]; then
  print "$local_config 已存在,保留不動。"
else
  cat > "$local_config" <<'CONF'
"""你自己的 workplan 設定。

這個檔案不會被更新覆蓋,所有個人化都寫在這裡。只需要寫你要改的那幾行,
沒寫到的沿用 wp/config.py 的預設值。改完直接生效,不用重啟任何東西。
"""

# 沒有分色的工作事件放哪一本行事曆。這本要先在行事曆 app 裡手動建好。
TARGET_CALENDAR = "工作"

# 專案分色。一個專案一本行事曆,因為顏色是行事曆屬性而不是事件屬性。
# 建行事曆:echo '[{"title":"工作-官網","color":"#2E86C1"}]' | bin/wpkit mkcalendar
PROJECT_CALENDARS = {
    # "官網改版": "工作-官網",
}

# 所有會佔用你工作時間的行事曆,漏掉一本就會排到已經有事的時段。
# 沒設這一項的話會自動用 TARGET_CALENDAR 加上 PROJECT_CALENDARS 的全部。
# 記得把私人行事曆(家庭、公司 Google 帳號)也加進來。
# BUSY_CALENDARS = ["工作", "工作-官網", "家庭"]

# 你的可工作時段。
WORK_BLOCKS = [("09:00", "12:00"), ("13:00", "18:00")]

# 固定不排工作的時段,key 是星期幾,週一為 0。
# 例如週三下午留給例行會議:{2: [("13:00", "18:00")]}
BLOCKED_BLOCKS = {}

# 這些既有行程不算佔用時段,工作可以蓋過去。適合掛著但不需要專心的行程。
OVERLAPPABLE_EVENT_TITLES = []

# 短於這個長度的工作只留在待辦,不佔行事曆時段。
MIN_EVENT_MINUTES = 60
CONF
  print "已產生 $local_config"
fi

print "\n== 5. 原生標籤(選用) =="
if shortcuts list 2>/dev/null | grep -q "workplan-tags2"; then
  print "捷徑已安裝"
else
  print "還沒安裝捷徑,待辦不會有專案標籤,其餘功能不受影響。"
  print "要裝的話開啟 shortcuts/workplan-tags2.shortcut 並加入捷徑 app。"
fi

print "\n== 6. 早晚推播(選用) =="
if [[ -z "$want_launchd" ]]; then
  if [[ -t 0 ]]; then
    print "要安裝平日 08:30 與 18:00 的自動推播嗎?[y/N]"
    read -r answer
    [[ "$answer" == "y" || "$answer" == "Y" ]] && want_launchd="yes" || want_launchd="no"
  else
    # 非互動環境不要卡在等輸入,跳過並告知怎麼補裝。
    want_launchd="no"
    print "非互動環境,跳過。要裝的話之後跑:setup.sh --launchd"
  fi
fi
if [[ "$want_launchd" == "yes" ]]; then
  if [[ "$here" != "$skill_target" && ! -L "$skill_target" ]]; then
    print "推播腳本走 \$HOME/.claude/skills/workplan 這個路徑,但那裡不是這份 skill。"
    print "建立連結後再跑一次:ln -s \"$here\" \"$skill_target\""
  else
    for kind in morning evening; do
      cp "$here/launchd/com.workplan-$kind.plist" "$HOME/Library/LaunchAgents/"
      launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.workplan-$kind.plist" 2>/dev/null || true
    done
    print "已安裝。記錄寫在 ~/Library/Logs/workplan.log"
  fi
fi

print "\n== 7. 連結到 skills 目錄 =="
if [[ "$here" == "$skill_target" || -L "$skill_target" ]]; then
  print "已經在位置上了。"
elif [[ -e "$skill_target" ]]; then
  print "$skill_target 已存在而且不是連結,請自行處理後再建立連結。"
else
  ln -s "$here" "$skill_target"
  print "已建立連結 $skill_target -> $here"
fi

print "\n== 8. 接下來 =="
print "編輯 $local_config,至少確認這三項:"
print "  TARGET_CALENDAR   放沒有分色的工作事件,這本行事曆要先手動建好"
print "  BUSY_CALENDARS    涵蓋所有會佔用你工作時間的行事曆,漏掉會排到已經有事的時段"
print "  WORK_BLOCKS       你的可工作時段"
print ""
print "不要改 wp/config.py,那是預設值,更新會覆蓋掉。"
print ""
print "要用 Notion 當任務來源的話,另外設定 NOTION_TOKEN 並填 references/project_map.json。"
print "不用 Notion 也能運作,那時走口述建待辦的流程。"
