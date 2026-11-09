// Apple Health (HealthKit) → Ember & Tide. The iPhone twin of web/android/.../HealthSync.kt: same metric names,
// same POST /api/people/{pid}/wearable and /stages formats, same duplicate rule (per metric and hour keep the app
// with the most readings), first sync 7 days back, then incremental with a 2-hour overlap (the server drops repeats).
//
// Oura, Apple Watch, Withings and others write into Apple Health; we only read. HealthKit never says whether READ
// permission was granted (privacy), so "allowed" = the types we asked for; empty results simply mean no data.
// Target membership: app only. Capability: HealthKit (+ Background Delivery). Info.plist: NSHealthShareUsageDescription.

import Foundation
import HealthKit

final class HealthSync {
    static let shared = HealthSync()
    let store = HKHealthStore()
    private let firstDays: TimeInterval = 7 * 86400
    private let overlap: TimeInterval = 2 * 3600
    private let chunk = 20000
    private var running = false
    private let defaults = UserDefaults(suiteName: Ember.appGroup) ?? .standard

    struct Kind { let key: String; let metric: String?; let night: Bool; let types: [HKObjectType] }

    /// The same toggle keys as Android, so the web Settings screen works unchanged.
    lazy var kinds: [Kind] = {
        func q(_ id: HKQuantityTypeIdentifier) -> HKObjectType { HKQuantityType(id) }
        var k: [Kind] = [
            Kind(key: "heart_rate", metric: "hr", night: true, types: [q(.heartRate)]),
            Kind(key: "sleep", metric: nil, night: true, types: [HKCategoryType(.sleepAnalysis)]),
            Kind(key: "hrv", metric: "hrv", night: true, types: [q(.heartRateVariabilitySDNN)]),   // Apple stores SDNN, Android RMSSD
            Kind(key: "breathing", metric: "resp_rate", night: true, types: [q(.respiratoryRate)]),
            Kind(key: "spo2", metric: "spo2", night: true, types: [q(.oxygenSaturation)]),
            Kind(key: "resting_hr", metric: "resting_hr", night: true, types: [q(.restingHeartRate)]),
            Kind(key: "body_temp", metric: "body_temp", night: true, types: [q(.bodyTemperature), q(.basalBodyTemperature)]),
            Kind(key: "exercise", metric: "exercise_min", night: false, types: [HKObjectType.workoutType()]),
            Kind(key: "steps", metric: "steps", night: false, types: [q(.stepCount)]),
            Kind(key: "active_kcal", metric: "active_kcal", night: false, types: [q(.activeEnergyBurned)]),
            Kind(key: "caffeine", metric: "caffeine_mg", night: false, types: [q(.dietaryCaffeine)]),
            Kind(key: "weight", metric: "weight_kg", night: false, types: [q(.bodyMass)]),
        ]
        if #available(iOS 16.0, *) {
            k.insert(Kind(key: "skin_temp", metric: "skin_temp", night: true, types: [q(.appleSleepingWristTemperature)]), at: 0)
        }
        return k
    }()

    var available: Bool { HKHealthStore.isHealthDataAvailable() }
    var enabled: Set<String> {
        get { Set(defaults.stringArray(forKey: "hk_types") ?? kinds.filter(\.night).map(\.key)) }
        set { defaults.set(Array(newValue), forKey: "hk_types") }
    }
    var connected: Bool { defaults.bool(forKey: "hk_connected") }
    var last: Double { defaults.double(forKey: "hk_last") }

    func status() -> [String: Any] {
        var o: [String: Any] = ["sdk": available ? "available" : "unavailable", "last": last * 1000,
                                "types": Array(enabled), "available": kinds.map(\.key),
                                "allowed": connected ? Array(enabled) : [], "needs_permission": connected ? [] : Array(enabled),
                                "granted": [], "missing": [], "connected": connected,
                                "skin_supported": kinds.contains { $0.key == "skin_temp" }, "background": connected]
        if let r = defaults.dictionary(forKey: "hk_report") { o["report"] = r }
        return o
    }

    func connect() async throws {
        let want = Set(kinds.filter { enabled.contains($0.key) }.flatMap(\.types))
        try await store.requestAuthorization(toShare: [], read: want)
        defaults.set(true, forKey: "hk_connected")
        enableBackground()
    }

    func disconnect() {
        defaults.set(false, forKey: "hk_connected")
        store.disableAllBackgroundDelivery { _, _ in }
    }

    /// Wake the app when new heart rate or sleep arrives (at most hourly), then sync.
    func enableBackground() {
        for id in [HKQuantityTypeIdentifier.heartRate] {
            let t = HKQuantityType(id)
            store.enableBackgroundDelivery(for: t, frequency: .hourly) { _, _ in }
            store.execute(HKObserverQuery(sampleType: t, predicate: nil) { [weak self] _, done, _ in
                Task { _ = try? await self?.sync(full: false); done() }
            })
        }
        store.enableBackgroundDelivery(for: HKCategoryType(.sleepAnalysis), frequency: .hourly) { _, _ in }
    }

    // MARK: sync

    private struct Series { var metric: String; var device: String; var bundle: String; var points: [(Double, Double)] = [] }

    func sync(full: Bool) async throws -> [String: Any] {
        guard Ember.ready, connected, !running else { return defaults.dictionary(forKey: "hk_report") ?? [:] }
        running = true
        defer { running = false }
        let now = Date()
        let from = (full || last == 0) ? now.addingTimeInterval(-firstDays) : Date(timeIntervalSince1970: last - overlap)
        var series: [String: Series] = [:]
        var read: [String: Int] = [:]

        func add(_ metric: String, _ s: HKSample, _ t: Date, _ v: Double) {
            let dev = device(s), bundle = s.sourceRevision.source.bundleIdentifier
            series["\(metric)|\(bundle)", default: Series(metric: metric, device: dev, bundle: bundle)].points.append((t.timeIntervalSince1970, v))
        }
        for k in kinds where enabled.contains(k.key) {
            guard let metric = k.metric else { continue }
            for type in k.types {
                if type == HKObjectType.workoutType() {
                    let ws = try await samples(HKObjectType.workoutType(), from, now) as? [HKWorkout] ?? []
                    read[k.key, default: 0] += ws.count
                    ws.forEach { add(metric, $0, $0.endDate, $0.duration / 60) }
                    continue
                }
                guard let qt = type as? HKQuantityType else { continue }
                let ss = try await samples(qt, from, now) as? [HKQuantitySample] ?? []
                read[k.key, default: 0] += ss.count
                for s in ss {
                    switch metric {
                    case "hr", "resting_hr", "resp_rate": add(metric, s, s.startDate, s.quantity.doubleValue(for: .count().unitDivided(by: .minute())))
                    case "hrv": add(metric, s, s.startDate, s.quantity.doubleValue(for: .secondUnit(with: .milli)))
                    case "spo2": add(metric, s, s.startDate, s.quantity.doubleValue(for: .percent()) * 100)
                    case "skin_temp", "body_temp": add(metric, s, s.startDate, s.quantity.doubleValue(for: .degreeCelsius()))
                    case "steps": add(metric, s, s.endDate, s.quantity.doubleValue(for: .count()))
                    case "active_kcal": add(metric, s, s.endDate, s.quantity.doubleValue(for: .kilocalorie()))
                    case "caffeine_mg": add(metric, s, s.startDate, s.quantity.doubleValue(for: .gramUnit(with: .milli)))
                    case "weight_kg": add(metric, s, s.startDate, s.quantity.doubleValue(for: .gramUnit(with: .kilo)))
                    default: break
                    }
                }
            }
        }
        let skipped = dedupe(&series)
        var stored: [String: Int] = [:]
        var warnings = Set<String>(), notes = Set<String>()
        let pid = Ember.person
        for s in series.values where !s.points.isEmpty {
            let pts = s.points.sorted { $0.0 < $1.0 }
            for i in stride(from: 0, to: pts.count, by: chunk) {
                var body: [String: Any] = ["device": s.device, "metric": s.metric, "platform": "ios",
                                           "points": pts[i..<min(i + chunk, pts.count)].map { [$0.0, $0.1] }]
                if s.metric == "skin_temp" || s.metric == "body_temp" { body["unit"] = "C"; body["relative"] = false }
                let rep = try JSONSerialization.jsonObject(with: try await Ember.call("POST", "/api/people/\(pid)/wearable", json: body)) as? [String: Any] ?? [:]
                stored[s.metric, default: 0] += rep["stored"] as? Int ?? 0
                (rep["warnings"] as? [String])?.forEach { warnings.insert($0) }
                (rep["notes"] as? [String])?.forEach { notes.insert($0) }
            }
        }
        if enabled.contains("sleep") { stored["sleep_sessions"] = try await syncSleep(from, now, pid) }
        if skipped > 0 { notes.insert("\(skipped) readings came from two apps at once; kept one.") }
        let report: [String: Any] = ["ts": now.timeIntervalSince1970 * 1000, "from": from.timeIntervalSince1970 * 1000, "read": read,
                                     "stored": stored, "warnings": Array(warnings), "notes": Array(notes), "nights_rechecked": 0]
        defaults.set(now.timeIntervalSince1970, forKey: "hk_last")
        defaults.set(report, forKey: "hk_report")
        return report
    }

    /// Sleep stages → /stages, one night per source (Apple Watch, Oura…); "in bed" alone isn't a stage.
    private func syncSleep(_ from: Date, _ to: Date, _ pid: String) async throws -> Int {
        let ss = try await samples(HKCategoryType(.sleepAnalysis), from, to) as? [HKCategorySample] ?? []
        var bySource: [String: (label: String, segs: [[String: Any]])] = [:]
        for s in ss {
            guard let stage = stageName(s.value) else { continue }
            let src = s.sourceRevision.source.bundleIdentifier
            bySource[src, default: (label: "\(device(s)) · Apple Health", segs: [])].segs.append(
                ["start": s.startDate.timeIntervalSince1970, "end": s.endDate.timeIntervalSince1970, "stage": stage])
        }
        var n = 0
        for (src, v) in bySource where !v.segs.isEmpty {
            _ = try await Ember.call("POST", "/api/people/\(pid)/stages",
                                     json: ["source": "healthkit-\(src.suffix(30))", "label": v.label, "segments": v.segs])
            n += 1
        }
        return n
    }

    private func stageName(_ v: Int) -> String? {
        if #available(iOS 16.0, *) {
            switch HKCategoryValueSleepAnalysis(rawValue: v) {
            case .awake: return "awake"
            case .asleepCore: return "light"
            case .asleepDeep: return "deep"
            case .asleepREM: return "rem"
            case .asleepUnspecified: return "light"
            default: return nil                          // inBed
            }
        }
        return v == HKCategoryValueSleepAnalysis.awake.rawValue ? "awake" : v == 1 ? "light" : nil
    }

    /// Per metric and hour, keep the app with the most readings (two apps copying the same watch = duplicates).
    private func dedupe(_ all: inout [String: Series]) -> Int {
        var removed = 0
        let byMetric = Dictionary(grouping: all.keys) { all[$0]!.metric }
        for (_, keys) in byMetric where keys.count > 1 {
            var hours: [Int: [String: Int]] = [:]
            for k in keys { for p in all[k]!.points { hours[Int(p.0 / 3600), default: [:]][k, default: 0] += 1 } }
            for (h, counts) in hours where counts.count > 1 {
                let keep = counts.max { a, b in a.value < b.value }!.key
                for k in counts.keys where k != keep {
                    let before = all[k]!.points.count
                    all[k]!.points.removeAll { Int($0.0 / 3600) == h }
                    removed += before - all[k]!.points.count
                }
            }
        }
        return removed
    }

    /// A friendly, stable device label without personal names ("Alex's Apple Watch" → "Apple Watch").
    private func device(_ s: HKSample) -> String {
        let b = s.sourceRevision.source.bundleIdentifier
        if b.hasPrefix("com.apple.health") { return s.sourceRevision.productType?.hasPrefix("Watch") == true ? "Apple Watch" : "iPhone" }
        if b.contains("oura") { return "Oura" }
        if b.contains("withings") { return "Withings" }
        return String(s.sourceRevision.source.name.prefix(40))
    }

    private func samples(_ type: HKSampleType, _ from: Date, _ to: Date) async throws -> [HKSample] {
        try await withCheckedThrowingContinuation { cont in
            let pred = HKQuery.predicateForSamples(withStart: from, end: to)
            store.execute(HKSampleQuery(sampleType: type, predicate: pred, limit: HKObjectQueryNoLimit,
                                        sortDescriptors: [NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: true)]) { _, r, e in
                if let e { cont.resume(throwing: e) } else { cont.resume(returning: r ?? []) }
            })
        }
    }
}
