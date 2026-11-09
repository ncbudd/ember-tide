// App Intents: one definition each, used by Siri ("Hey Siri, hot flash in Ember & Tide"), Shortcuts, the home and
// lock-screen widget buttons, the Live Activity buttons, the Control Center control (iOS 18) and the Apple Watch
// widget. Every request carries source=… so the night story says where it came from (same as Android: widget,
// tile, watch, notification).
//
// Target membership: app + widget extension (+ watch app/widget, with `source` set to "watch").

import AppIntents
import Foundation
#if canImport(WidgetKit)
import WidgetKit
#endif

/// Where an intent ran. The watch targets compile with WATCH defined (Build Settings → Swift Compiler → Active
/// Compilation Conditions) so their requests are labelled "watch".
enum IntentSource {
    #if WATCH
    static let value = "watch"
    #else
    static let value = "widget"
    #endif
}

enum EmberAction {
    static func run(_ method: String, _ path: String, _ json: [String: Any]? = nil) async throws {
        guard Ember.ready else { throw Ember.HTTPError(status: 0) }
        try await Ember.call(method, "/api/people/\(Ember.person)\(path)", json: json)
        #if canImport(WidgetKit)
        WidgetCenter.shared.reloadAllTimelines()
        #endif
    }
}

struct HotFlashIntent: AppIntent {
    static var title: LocalizedStringResource = "Hot flash"
    static var description = IntentDescription("Cools your side right down for a while, then eases back.")
    static var openAppWhenRun = false
    func perform() async throws -> some IntentResult & ProvidesDialog {
        try await EmberAction.run("POST", "/flash", ["kind": "hot", "source": IntentSource.value])
        return .result(dialog: "Hot flash started")
    }
}

struct ColdFlashIntent: AppIntent {
    static var title: LocalizedStringResource = "Cold flash"
    static var description = IntentDescription("Warms your side up for a while, then eases back.")
    static var openAppWhenRun = false
    func perform() async throws -> some IntentResult & ProvidesDialog {
        try await EmberAction.run("POST", "/flash", ["kind": "cold", "source": IntentSource.value])
        return .result(dialog: "Cold flash started")
    }
}

struct ExtendFlashIntent: AppIntent {
    static var title: LocalizedStringResource = "5 more minutes"
    static var openAppWhenRun = false
    func perform() async throws -> some IntentResult {
        try await EmberAction.run("POST", "/flash/extend?source=\(IntentSource.value)")
        return .result()
    }
}

struct StopFlashIntent: AppIntent {
    static var title: LocalizedStringResource = "Stop the flash"
    static var openAppWhenRun = false
    func perform() async throws -> some IntentResult {
        try await EmberAction.run("POST", "/flash/stop?source=\(IntentSource.value)")
        return .result()
    }
}

struct WarmerIntent: AppIntent {
    static var title: LocalizedStringResource = "Make my side warmer"
    static var openAppWhenRun = false
    func perform() async throws -> some IntentResult & ProvidesDialog {
        try await EmberAction.run("POST", "/adjust", ["delta_c": 0.56, "until": "next_phase", "source": IntentSource.value])
        return .result(dialog: "A degree warmer")
    }
}

struct CoolerIntent: AppIntent {
    static var title: LocalizedStringResource = "Make my side cooler"
    static var openAppWhenRun = false
    func perform() async throws -> some IntentResult & ProvidesDialog {
        try await EmberAction.run("POST", "/adjust", ["delta_c": -0.56, "until": "next_phase", "source": IntentSource.value])
        return .result(dialog: "A degree cooler")
    }
}

/// Same logic as the Android widget and Wear tile power button.
struct TogglePowerIntent: AppIntent {
    static var title: LocalizedStringResource = "Turn my side on or off"
    static var openAppWhenRun = false
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let data = try await Ember.call("GET", "/api/snapshot")
        guard let snap = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let me = (snap["people"] as? [[String: Any]])?.first(where: { $0["id"] as? String == Ember.person }),
              let n = me["night"] as? [String: Any] else { return .result(dialog: "Couldn't find your side") }
        let st = n["state"] as? String ?? ""
        let src = IntentSource.value
        if (n["override"] as? [String: Any])?["manual"] as? Bool == true {
            try await EmberAction.run("POST", "/adjust/clear?source=\(src)")
            return .result(dialog: "Turned off")
        }
        if n["target_c"] as? Double != nil && st != "skipped" && st != "paused" {
            try await EmberAction.run("POST", "/tonight", ["action": "skip", "source": src])
            return .result(dialog: "Off for tonight")
        }
        if st == "skipped" || st == "paused" {
            try await EmberAction.run("POST", "/tonight", ["action": "resume", "source": src])
            return .result(dialog: "Back on")
        }
        // daytime / idle: on by hand at the rhythm's first temperature, like the Android widget
        let first = ((me["phases"] as? [[String: Any]])?.first?["temp_c"] as? Double) ?? 20.0
        try await EmberAction.run("POST", "/adjust", ["temp_c": first, "until": "next_phase", "minutes": 60, "source": src])
        return .result(dialog: "On for an hour")
    }
}

/// "Hey Siri, hot flash in Ember & Tide" works without any setup once the app is installed.
struct EmberShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: HotFlashIntent(), phrases: ["Hot flash in \(.applicationName)", "I'm hot in \(.applicationName)"],
                    shortTitle: "Hot flash", systemImageName: "snowflake")
        AppShortcut(intent: ColdFlashIntent(), phrases: ["Cold flash in \(.applicationName)", "I'm cold in \(.applicationName)"],
                    shortTitle: "Cold flash", systemImageName: "flame")
        AppShortcut(intent: CoolerIntent(), phrases: ["Make \(.applicationName) cooler"], shortTitle: "Cooler", systemImageName: "minus")
        AppShortcut(intent: WarmerIntent(), phrases: ["Make \(.applicationName) warmer"], shortTitle: "Warmer", systemImageName: "plus")
    }
}
