// The Live Activity shown on the lock screen and in the Dynamic Island while a hot or cold flash runs: a countdown
// that ticks on the phone by itself (Text(timerInterval:)), with "+5 min" and "Stop" buttons (App Intents).
// Started and ended by the app (EmberNativePlugin.showFlash / clearFlash), exactly where Android shows its ongoing
// flash notification. Target membership: app + widget extension.

#if canImport(ActivityKit)
import ActivityKit
import Foundation

struct FlashAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var end: Date            // on this phone's clock (the app corrects for server clock differences)
        var kind: String         // "hot" | "cold"
    }
    var name: String             // whose side
}

@available(iOS 16.2, *)
enum FlashActivity {
    static func show(kind: String, end: Date, name: String) {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { return }
        let state = FlashAttributes.ContentState(end: end, kind: kind)
        let content = ActivityContent(state: state, staleDate: end.addingTimeInterval(300))
        if let a = Activity<FlashAttributes>.activities.first {
            Task { await a.update(content) }
        } else {
            _ = try? Activity.request(attributes: FlashAttributes(name: name), content: content, pushType: nil)
        }
    }

    static func clear() {
        for a in Activity<FlashAttributes>.activities {
            Task { await a.end(nil, dismissalPolicy: .immediate) }
        }
    }
}
#endif
