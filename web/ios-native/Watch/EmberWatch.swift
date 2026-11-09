// Apple Watch: a tiny watch app (big temperature, Hot flash, +5 / Stop, power) and a watch widget for the Smart
// Stack and watch faces (complication) with a Hot flash button. Kept as simple as the Wear OS tile.
// The phone hands over the sign-in (WatchHandoff.swift → WatchConnectivity application context).
//
// Targets: watchOS App (members: EmberWatchApp part below + Shared/EmberCore.swift + Shared/EmberIntents.swift)
// and a watchOS Widget Extension (members: EmberWatchWidget part below + the same Shared files). Add WATCH to
// "Active Compilation Conditions" on both watch targets so requests are labelled source=watch.
// Split this file into two files, one per target, when wiring it up (see README.md).

import AppIntents
import SwiftUI
import WatchConnectivity
import WatchKit
import WidgetKit

// MARK: - Watch app

final class PhoneLink: NSObject, WCSessionDelegate, ObservableObject {
    static let shared = PhoneLink()
    func activate() { WCSession.default.delegate = self; WCSession.default.activate() }
    func session(_ s: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        if !s.receivedApplicationContext.isEmpty { save(s.receivedApplicationContext) }
    }
    func session(_ s: WCSession, didReceiveApplicationContext ctx: [String: Any]) { save(ctx) }
    private func save(_ ctx: [String: Any]) {
        Ember.configure(server: ctx["server"] as? String ?? "", token: ctx["token"] as? String ?? "", person: ctx["person"] as? String ?? "")
        WidgetCenter.shared.reloadAllTimelines()
        DispatchQueue.main.async { self.objectWillChange.send() }
    }
}

struct WatchHome: View {
    @State private var v = Ember.View()
    @State private var busy = false

    var body: some View {
        ScrollView {
            VStack(spacing: 8) {
                Text(v.name).font(.footnote).foregroundStyle(.secondary)
                Text(v.temp).font(.system(size: 44, weight: .thin))
                if let end = v.flashEnd, end > .now {
                    Text(timerInterval: Date.now...end, countsDown: true).monospacedDigit()
                    HStack {
                        Button("+5") { run(ExtendFlashIntent()) }
                        Button("Stop") { run(StopFlashIntent()) }
                    }
                } else {
                    Text(v.state).font(.footnote)
                    Button { run(HotFlashIntent()) } label: { Label("Hot flash", systemImage: "snowflake") }
                    Button { run(TogglePowerIntent()) } label: { Label(v.on ? "Turn off" : "Turn on", systemImage: "power") }
                }
            }
            .disabled(busy || !v.ok)
        }
        .task { v = await Ember.view() }
    }

    private func run<I: AppIntent>(_ i: I) {
        busy = true
        Task {
            let ok = (try? await i.perform()) != nil
            WKInterfaceDevice.current().play(ok ? .click : .failure)       // a short click when it worked
            v = await Ember.view()
            busy = false
        }
    }
}

// @main  ← uncomment in the watch app target's file
struct EmberWatchApp: App {
    init() { PhoneLink.shared.activate() }
    var body: some Scene { WindowGroup { WatchHome() } }
}

// MARK: - Watch widget (Smart Stack + complications)

struct WatchProvider: TimelineProvider {
    func placeholder(in context: Context) -> EmberEntryW { EmberEntryW(date: .now, v: Ember.View(temp: "66°", short: "Cooling", on: true, ok: true)) }
    func getSnapshot(in context: Context, completion: @escaping (EmberEntryW) -> Void) { Task { completion(EmberEntryW(date: .now, v: await Ember.view())) } }
    func getTimeline(in context: Context, completion: @escaping (Timeline<EmberEntryW>) -> Void) {
        Task {
            let v = await Ember.view()
            let next = v.flashEnd.map { min($0.addingTimeInterval(20), .now.addingTimeInterval(600)) } ?? .now.addingTimeInterval(600)
            completion(Timeline(entries: [EmberEntryW(date: .now, v: v)], policy: .after(next)))
        }
    }
}

struct EmberEntryW: TimelineEntry { let date: Date; let v: Ember.View }

struct WatchWidgetView: View {
    @Environment(\.widgetFamily) var family
    let e: EmberEntryW
    var body: some View {
        switch family {
        case .accessoryCircular:
            VStack(spacing: 0) { Text(e.v.temp).font(.title3); Text(e.v.flashKind != nil ? "Flash" : e.v.short).font(.system(size: 9)) }
        case .accessoryCorner, .accessoryInline:
            Text("\(e.v.temp) \(e.v.short)")
        default:                                                    // .accessoryRectangular: Smart Stack
            HStack {
                VStack(alignment: .leading) {
                    Text(e.v.temp).font(.title2.weight(.light))
                    Text(e.v.state).font(.caption2)
                }
                Spacer()
                if e.v.ok {                                         // interactive on recent watchOS (App Intents)
                    if e.v.flashKind != nil {
                        Button(intent: StopFlashIntent()) { Image(systemName: "stop.fill") }
                    } else {
                        Button(intent: HotFlashIntent()) { Image(systemName: "snowflake") }
                    }
                }
            }
        }
    }
}

// @main  ← uncomment in the watch widget extension target's file
struct EmberWatchWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "EmberWatchWidget", provider: WatchProvider()) { e in
            WatchWidgetView(e: e).containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Ember & Tide")
        .description("Your side's temperature and a hot flash button.")
        .supportedFamilies([.accessoryRectangular, .accessoryCircular, .accessoryCorner, .accessoryInline])
    }
}
