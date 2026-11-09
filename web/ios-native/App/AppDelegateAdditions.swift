// What to add to the AppDelegate that `npx cap add ios` generates (ios/App/App/AppDelegate.swift). Shown as an
// extension + a delegate class so the generated file only needs three calls (see README.md, step 6).

import FirebaseCore
import FirebaseMessaging
import UIKit
import UserNotifications

enum EmberAppSetup {
    /// Call at the top of application(_:didFinishLaunchingWithOptions:).
    static func launch() {
        if Bundle.main.path(forResource: "GoogleService-Info", ofType: "plist") != nil { FirebaseApp.configure() }
        UNUserNotificationCenter.current().delegate = EmberNotificationDelegate.shared
        NotificationActions.register()
        WatchHandoff.shared.activate()
        if HealthSync.shared.connected { HealthSync.shared.enableBackground() }
    }

    /// Call from application(_:didRegisterForRemoteNotificationsWithDeviceToken:).
    static func gotAPNsToken(_ token: Data) {
        Messaging.messaging().apnsToken = token
    }
}

final class EmberNotificationDelegate: NSObject, UNUserNotificationCenterDelegate {
    static let shared = EmberNotificationDelegate()

    /// Show alerts even while the app is open (the server only sends what matters).
    func userNotificationCenter(_ c: UNUserNotificationCenter, willPresent n: UNNotification,
                                withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .sound])
    }

    /// A button on a notification: do it, then a quiet confirmation (like Android's "Done: …").
    func userNotificationCenter(_ c: UNUserNotificationCenter, didReceive r: UNNotificationResponse,
                                withCompletionHandler done: @escaping () -> Void) {
        let id = r.actionIdentifier
        guard id != UNNotificationDefaultActionIdentifier, id != UNNotificationDismissActionIdentifier else { return done() }
        Task {
            await NotificationActions.handle(id, person: r.notification.request.content.userInfo["person"] as? String)
            done()
        }
    }
}
