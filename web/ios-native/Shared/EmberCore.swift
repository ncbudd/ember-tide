// Shared by the app, the widget extension, the notification service extension and (copied) the watch app.
// Mirrors web/android/.../Ember.java + wear/WearApi.kt: where the server is, the sign-in token, a tiny HTTP client
// that never follows redirects, and the "view" the widgets, Live Activity and watch show.
//
// Storage: server + person in the App Group's UserDefaults (widgets need them); the token in the Keychain with a
// shared access group, never in UserDefaults.

import Foundation
import Security

public enum Ember {
    /// Set both in Xcode: Signing & Capabilities → App Groups and Keychain Sharing, on every target.
    public static let appGroup = "group.app.embertide"
    public static let keychainGroup = "app.embertide.shared"     // Xcode prefixes this with the team ID

    static var defaults: UserDefaults { UserDefaults(suiteName: appGroup) ?? .standard }

    public static var server: String {
        get { defaults.string(forKey: "server") ?? "" }
        set { defaults.set(newValue, forKey: "server") }
    }
    public static var person: String {
        get { defaults.string(forKey: "person") ?? "" }
        set { defaults.set(newValue, forKey: "person") }
    }
    public static var token: String {
        get { Keychain.get("token") ?? "" }
        set { newValue.isEmpty ? Keychain.delete("token") : Keychain.set("token", newValue) }
    }
    public static var ready: Bool { !server.isEmpty && !token.isEmpty && !person.isEmpty }

    public static func configure(server: String, token: String, person: String) {
        self.server = server; self.token = token; self.person = person
    }

    public static func signOut() { server = ""; token = ""; person = "" }

    // MARK: HTTP

    public struct HTTPError: Error, LocalizedError {
        public let status: Int
        public var errorDescription: String? { status == 0 ? "Can't reach your server" : "HTTP \(status)" }
    }

    private final class NoRedirects: NSObject, URLSessionTaskDelegate {
        func urlSession(_ s: URLSession, task: URLSessionTask, willPerformHTTPRedirection r: HTTPURLResponse,
                        newRequest: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
            completionHandler(nil)            // never carry the token to wherever a redirect points
        }
    }
    private static let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.timeoutIntervalForRequest = 10
        return URLSession(configuration: c, delegate: NoRedirects(), delegateQueue: nil)
    }()

    @discardableResult
    public static func call(_ method: String, _ path: String, json: Any? = nil) async throws -> Data {
        guard let url = URL(string: server + path), ready || path.hasPrefix("/healthz") else { throw HTTPError(status: 0) }
        var r = URLRequest(url: url)
        r.httpMethod = method
        r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        r.setValue("1", forHTTPHeaderField: "X-Embertide")
        r.setValue("app", forHTTPHeaderField: "X-Embertide-Client")
        if let json {
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = try JSONSerialization.data(withJSONObject: json)
        }
        let (data, resp) = try await session.data(for: r)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<400).contains(code) else { throw HTTPError(status: code) }
        return data
    }

    // MARK: What widgets, the Live Activity and the watch show (same rules as WearApi.view on Android)

    public struct View: Codable, Hashable {
        public var name = "Ember & Tide"
        public var temp = "–"
        public var state = "Open the app to sign in"
        public var short = "Sign in"
        public var on = false
        public var flashKind: String? = nil
        public var flashEnd: Date? = nil          // already converted to this device's clock
        public var ok = false
    }

    public static func view() async -> View {
        guard ready else { return View() }
        do {
            let data = try await call("GET", "/api/snapshot")
            guard let snap = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let people = snap["people"] as? [[String: Any]],
                  let me = people.first(where: { $0["id"] as? String == person }),
                  let night = me["night"] as? [String: Any] else {
                return View(state: "This account has no side", short: "–")
            }
            let fahrenheit = ((me["settings"] as? [String: Any])?["units"] as? String ?? "F") != "C"
            let now = snap["now"] as? Double ?? Date().timeIntervalSince1970
            let skew = Date().timeIntervalSince1970 - now                     // device clock minus server clock
            let target = night["target_c"] as? Double
            var v = View(name: me["name"] as? String ?? "", ok: true)
            v.on = target != nil
            v.temp = target.map { temp($0, fahrenheit) } ?? "Off"
            if let f = night["flash"] as? [String: Any], let end = f["end"] as? Double, now < end {
                let kind = f["kind"] as? String ?? "hot"
                let mins = Int(ceil((end - now) / 60))
                v.flashKind = kind
                v.flashEnd = Date(timeIntervalSince1970: end + skew)
                v.state = "\(kind == "hot" ? "Hot" : "Cold") flash · \(mins) min"
                v.short = "\(kind == "hot" ? "Hot" : "Cold") \(mins)m"
            } else if target == nil {
                switch night["state"] as? String {
                case "skipped": (v.state, v.short) = ("Off tonight", "Off")
                case "paused": (v.state, v.short) = ("Paused", "Paused")
                default: (v.state, v.short) = ("Tap power to turn on", "Off")
                }
            } else {
                let bed = (me["bed"] as? [String: Any])?["measured_c"] as? Double
                let w: String
                if let m = bed, let t = target { w = t < m - 0.4 ? "Cooling" : t > m + 0.4 ? "Warming" : "Holding" } else { w = "On" }
                (v.state, v.short) = (w, w)
            }
            return v
        } catch {
            return View(state: "Can't reach your server", short: "–")
        }
    }

    public static func temp(_ c: Double, _ fahrenheit: Bool) -> String {
        fahrenheit ? "\(Int((c * 9 / 5 + 32).rounded()))°" : String(format: "%.1f°", c)
    }
}

// MARK: Keychain (shared access group so extensions can read the token)

enum Keychain {
    private static func base(_ key: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "app.embertide",
         kSecAttrAccount as String: key, kSecAttrAccessGroup as String: Ember.keychainGroup,
         kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
    }
    static func get(_ key: String) -> String? {
        var q = base(key)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data else { return nil }
        return String(data: d, encoding: .utf8)
    }
    static func set(_ key: String, _ value: String) {
        delete(key)
        var q = base(key)
        q[kSecValueData as String] = Data(value.utf8)
        SecItemAdd(q as CFDictionary, nil)
    }
    static func delete(_ key: String) { SecItemDelete(base(key) as CFDictionary) }
}
