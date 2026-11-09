// Widget extension: the home-screen and lock-screen widget (your temperature, what it's doing, a Hot flash button),
// the Live Activity for a running flash (countdown + "+5 min" / "Stop"), and an iOS 18 Control Center button.
// Mirrors the Android widget (EmberWidget.java) and Quick Settings tile. Buttons are App Intents (EmberIntents.swift).
//
// Target: Widget Extension (iOS 17+ for interactive buttons). Members: this file + Shared/*.swift.
// Capabilities: App Groups + Keychain Sharing (same groups as the app).

import ActivityKit
import AppIntents
import SwiftUI
import WidgetKit

// MARK: timeline

struct EmberEntry: TimelineEntry {
    let date: Date
    let v: Ember.View
}

struct EmberProvider: TimelineProvider {
    func placeholder(in context: Context) -> EmberEntry {
        EmberEntry(date: .now, v: Ember.View(name: "Left", temp: "66°", state: "Cooling", short: "Cooling", on: true, ok: true))
    }
    func getSnapshot(in context: Context, completion: @escaping (EmberEntry) -> Void) {
        Task { completion(EmberEntry(date: .now, v: await Ember.view())) }
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<EmberEntry>) -> Void) {
        Task {
            let v = await Ember.view()
            // refresh just after a flash ends, otherwise every 15 minutes (iOS may stretch this)
            let next = v.flashEnd.map { min($0.addingTimeInterval(20), .now.addingTimeInterval(900)) } ?? .now.addingTimeInterval(900)
            completion(Timeline(entries: [EmberEntry(date: .now, v: v)], policy: .after(next)))
        }
    }
}

// MARK: home + lock screen widget

struct EmberWidgetView: View {
    @Environment(\.widgetFamily) var family
    let e: EmberEntry
    var hot: Color { Color(red: 0.37, green: 0.72, blue: 0.69) }      // tide
    var cold: Color { Color(red: 0.93, green: 0.54, blue: 0.33) }     // ember

    var body: some View {
        switch family {
        case .accessoryInline:
            Text("\(e.v.temp) · \(e.v.short)")
        case .accessoryCircular:
            VStack(spacing: 0) { Text(e.v.temp).font(.title3.weight(.light)); Text(e.v.short).font(.caption2) }
        case .accessoryRectangular:
            VStack(alignment: .leading) {
                Text(e.v.name).font(.caption).foregroundStyle(.secondary)
                Text(e.v.temp).font(.title2.weight(.light))
                Text(e.v.state).font(.caption)
            }
        default:
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(e.v.name).font(.caption).foregroundStyle(.secondary)
                    Text(e.v.temp).font(.system(size: 40, weight: .thin))
                    if let end = e.v.flashEnd, end > .now {
                        Text(timerInterval: Date.now...end, countsDown: true).font(.caption).monospacedDigit()
                    } else {
                        Text(e.v.state).font(.caption)
                    }
                }
                Spacer()
                if e.v.ok {
                    VStack(spacing: 8) {                          // right side: the thumb's side
                        if e.v.flashKind != nil {
                            Button(intent: ExtendFlashIntent()) { Label("+5", systemImage: "plus") }
                            Button(intent: StopFlashIntent()) { Label("Stop", systemImage: "stop.fill") }
                        } else {
                            Button(intent: HotFlashIntent()) { Label("Hot", systemImage: "snowflake") }.tint(hot)
                            Button(intent: TogglePowerIntent()) { Label(e.v.on ? "Off" : "On", systemImage: "power") }
                        }
                    }
                    .labelStyle(.titleAndIcon).buttonStyle(.bordered).font(.caption)
                }
            }
        }
    }
}

struct EmberWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "EmberWidget", provider: EmberProvider()) { e in
            EmberWidgetView(e: e).containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Ember & Tide")
        .description("Your side's temperature, with a hot flash button.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryCircular, .accessoryRectangular, .accessoryInline])
    }
}

// MARK: Live Activity during a flash

struct FlashLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: FlashAttributes.self) { ctx in
            HStack {
                VStack(alignment: .leading) {
                    Text("\(ctx.state.kind == "hot" ? "Hot" : "Cold") flash · \(ctx.attributes.name)").font(.headline)
                    Text(timerInterval: Date.now...max(ctx.state.end, .now), countsDown: true).font(.title2.monospacedDigit())
                }
                Spacer()
                Button(intent: ExtendFlashIntent()) { Text("+5 min") }.buttonStyle(.bordered)
                Button(intent: StopFlashIntent()) { Text("Stop") }.buttonStyle(.bordered)
            }
            .padding()
        } dynamicIsland: { ctx in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) { Text(ctx.state.kind == "hot" ? "Hot flash" : "Cold flash") }
                DynamicIslandExpandedRegion(.trailing) {
                    Text(timerInterval: Date.now...max(ctx.state.end, .now), countsDown: true).monospacedDigit()
                }
                DynamicIslandExpandedRegion(.bottom) {
                    HStack {
                        Button(intent: ExtendFlashIntent()) { Text("+5 min") }
                        Button(intent: StopFlashIntent()) { Text("Stop") }
                    }
                }
            } compactLeading: {
                Image(systemName: ctx.state.kind == "hot" ? "snowflake" : "flame")
            } compactTrailing: {
                Text(timerInterval: Date.now...max(ctx.state.end, .now), countsDown: true).monospacedDigit().frame(width: 44)
            } minimal: {
                Image(systemName: ctx.state.kind == "hot" ? "snowflake" : "flame")
            }
        }
    }
}

// MARK: Control Center (iOS 18): one tap hot flash, like Android's Quick Settings tile

@available(iOS 18.0, *)
struct HotFlashControl: ControlWidget {
    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: "app.embertide.hotflash") {
            ControlWidgetButton(action: HotFlashIntent()) { Label("Hot flash", systemImage: "snowflake") }
        }
        .displayName("Hot flash")
        .description("Cool your side right down for a while.")
    }
}

@main
struct EmberWidgets: WidgetBundle {
    @WidgetBundleBuilder var body: some Widget {
        EmberWidget()
        FlashLiveActivity()
        if #available(iOS 18.0, *) { HotFlashControl() }
    }
}
