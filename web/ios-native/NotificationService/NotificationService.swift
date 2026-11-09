// Notification Service Extension: the server sends an alert with a neutral placeholder ("Ember & Tide") and
// mutable-content: 1, plus the real message encrypted for this phone in the data field "e". This runs before the
// alert is shown, decrypts it with the keys in the shared Keychain, and fills in the title, text and buttons.
// If anything fails, the placeholder shows (never the ciphertext).
//
// Target: Notification Service Extension. Members: this file + Shared/EmberCore.swift + Shared/PushCrypto.swift.
// Capabilities: App Groups + Keychain Sharing (same groups as the app).

import UserNotifications

final class NotificationService: UNNotificationServiceExtension {
    private var handler: ((UNNotificationContent) -> Void)?
    private var best: UNMutableNotificationContent?

    override func didReceive(_ request: UNNotificationRequest, withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void) {
        handler = contentHandler
        let content = (request.content.mutableCopy() as? UNMutableNotificationContent) ?? UNMutableNotificationContent()
        best = content
        defer { contentHandler(content); handler = nil }       // delivered once; the timeout path won't repeat it
        guard let e = request.content.userInfo["e"] as? String, let data = PushCrypto.unb64u(e),
              let plain = try? PushCrypto.decrypt(data),
              let m = try? JSONSerialization.jsonObject(with: plain) as? [String: Any], m["t"] as? String == "et" else {
            return
        }
        content.title = String((m["title"] as? String ?? "Ember & Tide").prefix(120))
        content.body = String((m["body"] as? String ?? "").prefix(600))
        content.threadIdentifier = m["kind"] as? String ?? "ember"
        content.userInfo["person"] = m["person"] as? String ?? ""
        let acts = (m["actions"] as? [[String: Any]] ?? []).compactMap { a -> String? in
            guard let act = a["act"] as? String else { return nil }
            let mins = a["minutes"] as? Int ?? 0
            return act == "pause" ? "pause\(mins)" : act
        }
        content.categoryIdentifier = acts.isEmpty ? "" : "ET." + acts.joined(separator: ".")
        if let p = m["priority"] as? Int, p >= 4, #available(iOS 15.0, *) { content.interruptionLevel = .timeSensitive }
    }

    override func serviceExtensionTimeWillExpire() {
        if let h = handler, let b = best { h(b) }
    }
}
