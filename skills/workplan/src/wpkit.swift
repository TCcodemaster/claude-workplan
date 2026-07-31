// wpkit:workplan 的行事曆與提醒事項存取工具,走 EventKit。
//
// 之前走 AppleScript 讀行事曆要三分半,EventKit 只要幾十毫秒,差距太大所以換掉。
// 提醒事項的到期日刻意只填 year/month/day,EventKit 會存成全天到期,
// 讓待辦只有日期沒有時間。
//
// 用法見 usage(),資料一律走 stdin 與 stdout 的 JSON。

import EventKit
import Foundation

let store = EKEventStore()

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

func requestAccess(events: Bool, reminders: Bool) {
    var needEvents = events
    var needReminders = reminders
    if needEvents {
        var result: Bool? = nil
        store.requestFullAccessToEvents { granted, _ in result = granted }
        let deadline = Date().addingTimeInterval(60)
        while result == nil && Date() < deadline {
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
        }
        if result != true { fail("沒有行事曆的完整存取權限,請到系統設定的隱私權與安全性開啟。") }
        needEvents = false
    }
    if needReminders {
        var result: Bool? = nil
        store.requestFullAccessToReminders { granted, _ in result = granted }
        let deadline = Date().addingTimeInterval(60)
        while result == nil && Date() < deadline {
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
        }
        if result != true { fail("沒有提醒事項的存取權限,請到系統設定的隱私權與安全性開啟。") }
        needReminders = false
    }
}

let isoFormatter: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    f.timeZone = TimeZone.current
    return f
}()

let localFormatter: DateFormatter = {
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
    f.timeZone = TimeZone.current
    return f
}()

func parseDate(_ text: String) -> Date? {
    isoFormatter.date(from: text) ?? localFormatter.date(from: text)
}

func out(_ value: Any) {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}

func readStdinJSON() -> Any {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard !data.isEmpty, let parsed = try? JSONSerialization.jsonObject(with: data) else {
        fail("stdin 不是合法的 JSON。")
    }
    return parsed
}

func dayString(_ comps: DateComponents) -> String? {
    guard let y = comps.year, let m = comps.month, let d = comps.day else { return nil }
    return String(format: "%04d-%02d-%02d", y, m, d)
}

// ---------------------------------------------------------------- 讀取

func cmdLists() {
    requestAccess(events: false, reminders: true)
    out(store.calendars(for: .reminder).map { $0.title })
}

func cmdCalendars() {
    requestAccess(events: true, reminders: false)
    out(store.calendars(for: .event).map { ["title": $0.title, "writable": $0.allowsContentModifications] })
}

func cmdEvents(_ args: [String]) {
    requestAccess(events: true, reminders: false)
    guard args.count >= 2, let start = parseDate(args[0]), let end = parseDate(args[1]) else {
        fail("events 需要開始與結束時間,格式為 ISO8601 或 yyyy-MM-ddTHH:mm:ss。")
    }
    var calendars: [EKCalendar]? = nil
    if args.count >= 3, !args[2].isEmpty {
        let wanted = Set(args[2].split(separator: ",").map(String.init))
        calendars = store.calendars(for: .event).filter { wanted.contains($0.title) }
    }
    let predicate = store.predicateForEvents(withStart: start, end: end, calendars: calendars)
    // 逐欄組字典,一次寫成大字面值會讓 Swift 的型別推導超時。
    let rows: [[String: Any]] = store.events(matching: predicate).map { ev -> [String: Any] in
        var row: [String: Any] = [:]
        row["calendar"] = ev.calendar.title
        row["uid"] = ev.eventIdentifier ?? ""
        row["title"] = ev.title ?? ""
        row["start"] = ev.startDate.map { localFormatter.string(from: $0) } ?? NSNull()
        row["end"] = ev.endDate.map { localFormatter.string(from: $0) } ?? NSNull()
        row["allday"] = ev.isAllDay
        let alarms: [Int] = (ev.alarms ?? []).map { Int(-$0.relativeOffset / 60.0) }
        row["alarms"] = alarms
        row["notes"] = ev.notes ?? NSNull()
        return row
    }
    out(rows)
}

func cmdReminders(_ args: [String]) {
    requestAccess(events: false, reminders: true)
    let includeDone = args.contains("all")
    let calendars = store.calendars(for: .reminder)
    let predicate = store.predicateForReminders(in: calendars)

    var collected: [EKReminder]? = nil
    store.fetchReminders(matching: predicate) { found in collected = found ?? [] }
    let deadline = Date().addingTimeInterval(60)
    while collected == nil && Date() < deadline {
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    guard let reminders = collected else { fail("讀取提醒事項逾時。") }

    let rows: [[String: Any]] = reminders.compactMap { r in
        if r.isCompleted && !includeDone { return nil }
        var due: Any = NSNull()
        var hasTime = false
        if let comps = r.dueDateComponents {
            due = dayString(comps) ?? NSNull()
            hasTime = comps.hour != nil
        }
        return [
            "list": r.calendar?.title ?? "",
            "id": r.calendarItemIdentifier,
            "title": r.title ?? "",
            "due": due,
            "hasTime": hasTime,
            "notes": r.notes ?? NSNull(),
            "url": r.url?.absoluteString ?? NSNull(),
            "completed": r.isCompleted,
            "completionDate": r.completionDate.map { localFormatter.string(from: $0) } ?? NSNull(),
        ]
    }
    out(rows)
}

// ---------------------------------------------------------------- 寫入

func reminderCalendar(_ title: String) -> EKCalendar {
    guard let cal = store.calendars(for: .reminder).first(where: { $0.title == title }) else {
        fail("找不到提醒事項清單「\(title)」。")
    }
    return cal
}

func parseDay(_ text: String) -> DateComponents {
    let parts = text.split(separator: "-").compactMap { Int($0) }
    guard parts.count == 3 else { fail("日期格式必須是 YYYY-MM-DD,收到 \(text)。") }
    return DateComponents(year: parts[0], month: parts[1], day: parts[2])
}

func cmdMakeReminders() {
    requestAccess(events: false, reminders: true)
    guard let items = readStdinJSON() as? [[String: Any]] else { fail("mkreminders 需要 JSON 陣列。") }
    var ids: [String] = []
    for item in items {
        guard let list = item["list"] as? String, let title = item["title"] as? String else {
            fail("每筆需要 list 與 title。")
        }
        let reminder = EKReminder(eventStore: store)
        reminder.calendar = reminderCalendar(list)
        reminder.title = title
        reminder.notes = item["notes"] as? String
        if let urlText = item["url"] as? String, !urlText.isEmpty {
            reminder.url = URL(string: urlText)
        }
        if let due = item["due"] as? String {
            reminder.dueDateComponents = parseDay(due)
        }
        do {
            try store.save(reminder, commit: false)
        } catch {
            fail("建立提醒事項失敗:\(error.localizedDescription)")
        }
        ids.append(reminder.calendarItemIdentifier)
    }
    do { try store.commit() } catch { fail("提交提醒事項失敗:\(error.localizedDescription)") }
    out(ids)
}

func cmdSetReminders() {
    requestAccess(events: false, reminders: true)
    guard let items = readStdinJSON() as? [[String: Any]] else { fail("setreminders 需要 JSON 陣列。") }
    var changed = 0
    for item in items {
        guard let id = item["id"] as? String,
              let reminder = store.calendarItem(withIdentifier: id) as? EKReminder else { continue }
        if let due = item["due"] as? String { reminder.dueDateComponents = parseDay(due) }
        if let notes = item["notes"] as? String { reminder.notes = notes }
        if let urlText = item["url"] as? String {
            reminder.url = urlText.isEmpty ? nil : URL(string: urlText)
        }
        if let done = item["completed"] as? Bool { reminder.isCompleted = done }
        if let title = item["title"] as? String { reminder.title = title }
        do {
            try store.save(reminder, commit: false)
            changed += 1
        } catch {
            fail("更新提醒事項失敗:\(error.localizedDescription)")
        }
    }
    do { try store.commit() } catch { fail("提交提醒事項失敗:\(error.localizedDescription)") }
    out(["count": changed])
}

func cmdDeleteReminders() {
    requestAccess(events: false, reminders: true)
    guard let ids = readStdinJSON() as? [String] else { fail("delreminders 需要字串陣列。") }
    var removed = 0
    for id in ids {
        guard let reminder = store.calendarItem(withIdentifier: id) as? EKReminder else { continue }
        do {
            try store.remove(reminder, commit: false)
            removed += 1
        } catch {
            fail("刪除提醒事項失敗:\(error.localizedDescription)")
        }
    }
    do { try store.commit() } catch { fail("提交刪除失敗:\(error.localizedDescription)") }
    out(["count": removed])
}

func cmdMakeEvents() {
    requestAccess(events: true, reminders: false)
    guard let items = readStdinJSON() as? [[String: Any]] else { fail("mkevents 需要 JSON 陣列。") }
    var made: [[String: String]] = []
    for item in items {
        guard let calTitle = item["calendar"] as? String,
              let title = item["title"] as? String,
              let startText = item["start"] as? String,
              let endText = item["end"] as? String,
              let start = parseDate(startText),
              let end = parseDate(endText) else {
            fail("每筆需要 calendar、title、start、end。")
        }
        guard let cal = store.calendars(for: .event).first(where: { $0.title == calTitle }) else {
            fail("找不到行事曆「\(calTitle)」。")
        }
        let event = EKEvent(eventStore: store)
        event.calendar = cal
        event.title = title
        event.startDate = start
        event.endDate = end
        event.notes = item["notes"] as? String
        // 事件預設要有提醒,否則到了時間不會通知。負值代表開始前幾分鐘。
        if let offset = item["alarmMinutes"] as? Int {
            event.addAlarm(EKAlarm(relativeOffset: TimeInterval(-offset * 60)))
        }
        do {
            try store.save(event, span: .thisEvent, commit: false)
        } catch {
            fail("建立事件失敗:\(error.localizedDescription)")
        }
        made.append(["key": (item["key"] as? String) ?? title, "uid": event.eventIdentifier ?? ""])
    }
    do { try store.commit() } catch { fail("提交事件失敗:\(error.localizedDescription)") }
    out(made)
}

func cmdSetAlarms() {
    requestAccess(events: true, reminders: false)
    guard let items = readStdinJSON() as? [[String: Any]] else { fail("setalarms 需要 JSON 陣列。") }
    var changed = 0
    for item in items {
        guard let uid = item["uid"] as? String,
              let minutes = item["alarmMinutes"] as? Int,
              let event = store.event(withIdentifier: uid) else { continue }
        for alarm in event.alarms ?? [] { event.removeAlarm(alarm) }
        event.addAlarm(EKAlarm(relativeOffset: TimeInterval(-minutes * 60)))
        do {
            try store.save(event, span: .thisEvent, commit: false)
            changed += 1
        } catch {
            fail("設定提醒失敗:\(error.localizedDescription)")
        }
    }
    do { try store.commit() } catch { fail("提交提醒設定失敗:\(error.localizedDescription)") }
    out(["count": changed])
}

func cmdRetitleEvents() {
    requestAccess(events: true, reminders: false)
    guard let items = readStdinJSON() as? [[String: Any]] else { fail("retitle 需要 JSON 陣列。") }
    var changed = 0
    for item in items {
        guard let uid = item["uid"] as? String,
              let title = item["title"] as? String,
              let event = store.event(withIdentifier: uid) else { continue }
        event.title = title
        do {
            try store.save(event, span: .thisEvent, commit: false)
            changed += 1
        } catch {
            fail("改事件標題失敗:\(error.localizedDescription)")
        }
    }
    do { try store.commit() } catch { fail("提交標題變更失敗:\(error.localizedDescription)") }
    out(["count": changed])
}

func cmdDeleteEvents() {
    requestAccess(events: true, reminders: false)
    guard let uids = readStdinJSON() as? [String] else { fail("delevents 需要字串陣列。") }
    var removed = 0
    for uid in uids {
        guard let event = store.event(withIdentifier: uid) else { continue }
        do {
            try store.remove(event, span: .thisEvent, commit: false)
            removed += 1
        } catch {
            fail("刪除事件失敗:\(error.localizedDescription)")
        }
    }
    do { try store.commit() } catch { fail("提交刪除失敗:\(error.localizedDescription)") }
    out(["count": removed])
}

/// 改既有事件的欄位。改時間不用刪掉重建,uid 保住了,
/// cache 裡 rid 與 event_uid 的對應才不會斷。
func cmdSetEvents() {
    requestAccess(events: true, reminders: false)
    guard let items = readStdinJSON() as? [[String: Any]] else { fail("setevents 需要 JSON 陣列。") }
    var changed = 0
    for item in items {
        guard let uid = item["uid"] as? String else { fail("每筆需要 uid。") }
        guard let event = store.event(withIdentifier: uid) else { continue }
        if let title = item["title"] as? String { event.title = title }
        if let notes = item["notes"] as? String { event.notes = notes.isEmpty ? nil : notes }
        if let text = item["start"] as? String, let value = parseDate(text) { event.startDate = value }
        if let text = item["end"] as? String, let value = parseDate(text) { event.endDate = value }
        do {
            try store.save(event, span: .thisEvent, commit: false)
            changed += 1
        } catch {
            fail("更新事件失敗:\(error.localizedDescription)")
        }
    }
    do { try store.commit() } catch { fail("提交更新失敗:\(error.localizedDescription)") }
    out(["count": changed])
}

/// 建立行事曆並指定顏色。EventKit 的顏色是行事曆屬性,不是事件屬性,
/// 所以要讓不同專案在週檢視裡分色,只能一個專案一本行事曆。
func cmdMakeCalendar() {
    requestAccess(events: true, reminders: false)
    guard let items = readStdinJSON() as? [[String: Any]] else { fail("mkcalendar 需要 JSON 陣列。") }
    var made: [[String: String]] = []
    for item in items {
        guard let title = item["title"] as? String else { fail("每筆需要 title。") }
        if let existing = store.calendars(for: .event).first(where: { $0.title == title }) {
            if let hex = item["color"] as? String, let color = colorFromHex(hex) {
                existing.cgColor = color
                try? store.saveCalendar(existing, commit: false)
            }
            made.append(["title": title, "id": existing.calendarIdentifier, "created": "0"])
            continue
        }
        // 一定要指定 source,不然 EventKit 不知道要建在哪個帳號底下。
        // 優先用 iCloud,退而求其次用本機,免得建到唯讀的訂閱帳號。
        let sources = store.sources
        let source = sources.first(where: { $0.sourceType == .calDAV && $0.title == "iCloud" })
            ?? sources.first(where: { $0.sourceType == .local })
            ?? sources.first(where: { $0.sourceType == .calDAV })
        guard let src = source else { fail("找不到可以建立行事曆的帳號。") }
        let cal = EKCalendar(for: .event, eventStore: store)
        cal.title = title
        cal.source = src
        if let hex = item["color"] as? String, let color = colorFromHex(hex) {
            cal.cgColor = color
        }
        do {
            try store.saveCalendar(cal, commit: false)
        } catch {
            fail("建立行事曆失敗:\(error.localizedDescription)")
        }
        made.append(["title": title, "id": cal.calendarIdentifier, "created": "1"])
    }
    do { try store.commit() } catch { fail("提交行事曆失敗:\(error.localizedDescription)") }
    out(made)
}

/// 把既有事件搬到另一本行事曆。改 calendar 再存就好,不用刪掉重建,
/// 這樣 uid 不變,cache 裡記的對應關係也不會斷。
func cmdMoveEvents() {
    requestAccess(events: true, reminders: false)
    guard let items = readStdinJSON() as? [[String: Any]] else { fail("movecal 需要 JSON 陣列。") }
    var moved = 0
    for item in items {
        guard let uid = item["uid"] as? String, let calTitle = item["calendar"] as? String else {
            fail("每筆需要 uid 與 calendar。")
        }
        guard let event = store.event(withIdentifier: uid) else { continue }
        guard let cal = store.calendars(for: .event).first(where: { $0.title == calTitle }) else {
            fail("找不到行事曆「\(calTitle)」。")
        }
        event.calendar = cal
        do {
            try store.save(event, span: .thisEvent, commit: false)
            moved += 1
        } catch {
            fail("搬移事件失敗:\(error.localizedDescription)")
        }
    }
    do { try store.commit() } catch { fail("提交搬移失敗:\(error.localizedDescription)") }
    out(["count": moved])
}

/// 把 #RRGGBB 轉成 CGColor。EventKit 只吃 CGColor,不吃字串。
func colorFromHex(_ hex: String) -> CGColor? {
    let text = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
    guard text.count == 6, let value = Int(text, radix: 16) else { return nil }
    let r = CGFloat((value >> 16) & 0xFF) / 255.0
    let g = CGFloat((value >> 8) & 0xFF) / 255.0
    let b = CGFloat(value & 0xFF) / 255.0
    return CGColor(red: r, green: g, blue: b, alpha: 1.0)
}

func usage() -> Never {
    let text = """
    wpkit 子命令
      lists                              列出提醒事項清單名稱
      calendars                          列出行事曆名稱與是否可寫
      mkcalendar                         stdin: [{title,color}] -> [{title,id,created}]
      movecal                            stdin: [{uid,calendar}] -> 筆數
      events <start> <end> [cal1,cal2]   讀事件,時間用 ISO8601
      reminders [all]                    讀提醒事項,加 all 連已完成一起讀
      mkreminders                        stdin: [{list,title,due,notes}] -> ids
      setreminders                       stdin: [{id,due?,notes?,title?,completed?}] -> 筆數
      delreminders                       stdin: [id,...] -> 筆數
      mkevents                           stdin: [{calendar,key,title,start,end,notes}] -> [{key,uid}]
      retitle                            stdin: [{uid,title}] -> 筆數
      setevents                          stdin: [{uid,title?,notes?,start?,end?}] -> 筆數
      setalarms                          stdin: [{uid,alarmMinutes}] -> 筆數
      delevents                          stdin: [uid,...] -> 筆數
    """
    FileHandle.standardError.write((text + "\n").data(using: .utf8)!)
    exit(2)
}

let argv = Array(CommandLine.arguments.dropFirst())
guard let command = argv.first else { usage() }
let rest = Array(argv.dropFirst())

switch command {
case "lists": cmdLists()
case "calendars": cmdCalendars()
case "mkcalendar": cmdMakeCalendar()
case "movecal": cmdMoveEvents()
case "events": cmdEvents(rest)
case "reminders": cmdReminders(rest)
case "mkreminders": cmdMakeReminders()
case "setreminders": cmdSetReminders()
case "delreminders": cmdDeleteReminders()
case "mkevents": cmdMakeEvents()
case "retitle": cmdRetitleEvents()
case "setevents": cmdSetEvents()
case "setalarms": cmdSetAlarms()
case "delevents": cmdDeleteEvents()
default: usage()
}
