// Hands the server address, token and person to the Apple Watch (WatchConnectivity application context), like
// EmberWear.kt does over the Wear Data Layer. The watch keeps them in its own Keychain. Signing out sends empties.
// Target membership: app only.

import Foundation
#if canImport(WatchConnectivity)
import WatchConnectivity

final class WatchHandoff: NSObject, WCSessionDelegate {
    static let shared = WatchHandoff()

    func activate() {
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    func send() {
        guard WCSession.isSupported(), WCSession.default.activationState == .activated,
              WCSession.default.isPaired, WCSession.default.isWatchAppInstalled else { return }
        try? WCSession.default.updateApplicationContext(["server": Ember.server, "token": Ember.token, "person": Ember.person])
    }

    func session(_ s: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) { if state == .activated { send() } }
    func sessionDidBecomeInactive(_ s: WCSession) {}
    func sessionDidDeactivate(_ s: WCSession) { WCSession.default.activate() }
    func sessionWatchStateDidChange(_ s: WCSession) { send() }
}
#else
final class WatchHandoff { static let shared = WatchHandoff(); func activate() {}; func send() {} }
#endif
