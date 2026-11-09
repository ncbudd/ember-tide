// web/src/native.ts → registerPlugin("EmberHealth") on iPhone, backed by Apple Health instead of Health Connect.
// Same method names and result shapes as EmberHealthPlugin.kt. The web copy says "Health Connect" today; on iOS it
// should say "Apple Health" (see docs/IOS-PLAN.md → web changes).

import Capacitor
import UIKit

@objc(EmberHealthPlugin)
public class EmberHealthPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "EmberHealthPlugin"
    public let jsName = "EmberHealth"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "status", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "connect", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "sync", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "setTypes", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "openSettings", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "disconnect", returnType: CAPPluginReturnPromise),
    ]
    private let hk = HealthSync.shared

    @objc func status(_ call: CAPPluginCall) { call.resolve(hk.status()) }

    @objc func connect(_ call: CAPPluginCall) {
        guard hk.available else { return call.resolve(hk.status()) }
        Task {
            do { try await hk.connect(); call.resolve(hk.status()) } catch { call.reject(error.localizedDescription) }
        }
    }

    @objc func sync(_ call: CAPPluginCall) {
        let full = call.getBool("full") ?? false
        Task {
            do { call.resolve(try await hk.sync(full: full)) } catch { call.reject(error.localizedDescription) }
        }
    }

    /// Turning a type on asks Apple Health for just the new permission, then re-imports a week (like Android).
    @objc func setTypes(_ call: CAPPluginCall) {
        let keys = Set((call.getArray("types") as? [String]) ?? [])
        let added = keys.subtracting(hk.enabled)
        hk.enabled = keys.intersection(Set(hk.kinds.map(\.key)))
        Task {
            if hk.connected && !added.isEmpty {
                try? await hk.connect()
                _ = try? await hk.sync(full: true)
            }
            call.resolve(hk.status())
        }
    }

    /// iOS has no deep link into Health's per-app permissions; open our Settings page, which links to it.
    @objc func openSettings(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            if let u = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(u) }
            call.resolve()
        }
    }

    @objc func disconnect(_ call: CAPPluginCall) { hk.disconnect(); call.resolve(hk.status()) }
}
