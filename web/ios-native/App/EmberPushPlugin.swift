// web/src/native.ts → registerPlugin("EmberPush") on iPhone. Registers this phone with our server for push through
// Firebase Cloud Messaging (FCM hands it to Apple's APNs), with this install's Web Push keys so the server encrypts
// every message for this phone (Google and Apple only carry ciphertext). The Notification Service Extension
// decrypts it before the alert shows. Same server call as Android: POST /api/notify/me/push {kind: "fcm", …}.
//
// Needs: the Firebase iOS SDK (SPM: firebase-ios-sdk → FirebaseMessaging), GoogleService-Info.plist in the app
// target, the Push Notifications capability, and an APNs key uploaded to the Firebase project (see IOS-PLAN.md).

import Capacitor
import FirebaseCore
import FirebaseMessaging
import UIKit
import UserNotifications

@objc(EmberPushPlugin)
public class EmberPushPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "EmberPushPlugin"
    public let jsName = "EmberPush"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "status", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "enable", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "sync", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "disable", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "test", returnType: CAPPluginReturnPromise),
    ]
    private let d = UserDefaults(suiteName: Ember.appGroup) ?? .standard

    private func status() async -> [String: Any] {
        let s = await UNUserNotificationCenter.current().notificationSettings()
        return ["fcm_available": FirebaseApp.app() != nil, "allowed": s.authorizationStatus == .authorized,
                "method": d.string(forKey: "push_method") as Any? ?? NSNull(), "distributors": [], "distributor": NSNull(),
                "endpoint": d.string(forKey: "fcm_token") != nil, "registered": d.bool(forKey: "push_registered"),
                "error": d.string(forKey: "push_error") as Any? ?? NSNull()]
    }

    @objc func status(_ call: CAPPluginCall) { Task { call.resolve(await status()) } }

    @objc func enable(_ call: CAPPluginCall) {
        Task {
            _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
            await MainActor.run { UIApplication.shared.registerForRemoteNotifications() }
            await register()
            call.resolve(await status())
        }
    }

    @objc func sync(_ call: CAPPluginCall) { Task { await register(); call.resolve(await status()) } }

    @objc func disable(_ call: CAPPluginCall) {
        Task {
            if let t = d.string(forKey: "fcm_token") {
                _ = try? await Ember.call("POST", "/api/notify/me/push/remove", json: ["endpoint": t])
            }
            try? await Messaging.messaging().deleteToken()
            ["fcm_token", "push_registered", "push_method", "push_error"].forEach { d.removeObject(forKey: $0) }
            call.resolve(await status())
        }
    }

    @objc func test(_ call: CAPPluginCall) {
        Task {
            _ = try? await Ember.call("POST", "/api/notify/me/push/test", json: [:])
            call.resolve(await status())
        }
    }

    /// Get the FCM token and hand it, with this install's encryption keys, to our server.
    func register() async {
        guard FirebaseApp.app() != nil, Ember.ready else { return }
        do {
            let token = try await Messaging.messaging().token()
            let k = PushCrypto.keys()
            try await Ember.call("POST", "/api/notify/me/push", json: [
                "kind": "fcm", "endpoint": token, "p256dh": k.publicB64, "auth": k.authB64,
                "device": String(UIDevice.current.model.prefix(60)), "platform": "ios"])
            d.set(token, forKey: "fcm_token"); d.set(true, forKey: "push_registered"); d.set("fcm", forKey: "push_method")
            d.removeObject(forKey: "push_error")
        } catch {
            d.set(false, forKey: "push_registered"); d.set(error.localizedDescription, forKey: "push_error")
        }
    }
}

/// Notification buttons. The server's actions are a small fixed set (notify.py APP_ACTS), so each combination the
/// server sends is a registered category; the Notification Service Extension picks the category after decrypting.
enum NotificationActions {
    static let categories: [String: [(act: String, minutes: Int, label: String)]] = [
        "ET.manual_off": [("manual_off", 0, "Turn it off")],
        "ET.skip": [("skip", 0, "Turn it off tonight")],
        "ET.pause60.skip.start_now": [("pause", 60, "Wait an hour"), ("skip", 0, "Off tonight"), ("start_now", 0, "Start anyway")],
    ]

    /// Call once at launch (AppDelegate.didFinishLaunching) and set the delegate.
    static func register() {
        let cats = categories.map { id, acts in
            UNNotificationCategory(identifier: id, actions: acts.map {
                UNNotificationAction(identifier: "\($0.act)|\($0.minutes)", title: $0.label, options: [.authenticationRequired])
            }, intentIdentifiers: [])
        }
        UNUserNotificationCenter.current().setNotificationCategories(Set(cats))
    }

    /// Run a button press (from UNUserNotificationCenterDelegate.didReceive), same endpoints as EmberPush.kt.
    static func handle(_ actionId: String, person: String?) async {
        let parts = actionId.split(separator: "|"), act = String(parts.first ?? "")
        let minutes = Int(parts.last ?? "0") ?? 0
        let pid = (person?.isEmpty == false ? person! : Ember.person)
        if act == "manual_off" {
            _ = try? await Ember.call("POST", "/api/people/\(pid)/adjust/clear?source=notification")
        } else if ["pause", "skip", "start_now", "resume"].contains(act) {
            var body: [String: Any] = ["action": act, "source": "notification"]
            if minutes > 0 { body["minutes"] = minutes }
            _ = try? await Ember.call("POST", "/api/people/\(pid)/tonight", json: body)
        }
    }
}
