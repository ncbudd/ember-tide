// The iPhone side of web/src/native.ts → registerPlugin("EmberNative"). Same methods as the Android plugin, so the
// web app runs unchanged: sign-in handed to widgets/watch, the flash Live Activity, widget refresh, notification
// permission, status bar, share sheet, fold (always none on iPhone).
// Target membership: app only. Registered in EmberBridgeViewController.

import Capacitor
import UIKit
import UserNotifications
#if canImport(WidgetKit)
import WidgetKit
#endif

@objc(EmberNativePlugin)
public class EmberNativePlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "EmberNativePlugin"
    public let jsName = "EmberNative"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "configure", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "signOut", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "showFlash", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "clearFlash", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "refreshWidgets", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "requestNotifications", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "setBars", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "share", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "fold", returnType: CAPPluginReturnPromise),
    ]

    @objc func configure(_ call: CAPPluginCall) {
        let server = call.getString("server") ?? "", token = call.getString("token") ?? "", person = call.getString("person") ?? ""
        guard server.hasPrefix("https://") || server.hasPrefix("http://") else { return call.reject("bad server address") }
        Ember.configure(server: server, token: token, person: person)
        WatchHandoff.shared.send()                    // hands the sign-in to the Apple Watch, like EmberWear.kt
        reloadWidgets()
        call.resolve()
    }

    @objc func signOut(_ call: CAPPluginCall) {
        Ember.signOut()
        WatchHandoff.shared.send()
        if #available(iOS 16.2, *) { FlashActivity.clear() }
        reloadWidgets()
        call.resolve()
    }

    /// end = server's flash end (ms); now = server's clock (ms) so a phone whose clock differs still counts correctly.
    @objc func showFlash(_ call: CAPPluginCall) {
        let endMs = call.getDouble("end") ?? 0
        let serverNowMs = call.getDouble("now") ?? Date().timeIntervalSince1970 * 1000
        let left = max(0, (endMs - serverNowMs) / 1000)
        if #available(iOS 16.2, *) {
            FlashActivity.show(kind: call.getString("kind") ?? "hot", end: Date().addingTimeInterval(left), name: call.getString("name") ?? "")
        }
        reloadWidgets()
        call.resolve()
    }

    @objc func clearFlash(_ call: CAPPluginCall) {
        if #available(iOS 16.2, *) { FlashActivity.clear() }
        reloadWidgets()
        call.resolve()
    }

    @objc func refreshWidgets(_ call: CAPPluginCall) { reloadWidgets(); call.resolve() }

    @objc func requestNotifications(_ call: CAPPluginCall) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            if granted { DispatchQueue.main.async { UIApplication.shared.registerForRemoteNotifications() } }
            call.resolve(["granted": granted])
        }
    }

    /// The web app tells us its colours; on iPhone only the status bar text colour matters.
    @objc func setBars(_ call: CAPPluginCall) {
        let light = call.getBool("light") ?? false
        DispatchQueue.main.async { [weak self] in
            (self?.bridge?.viewController as? EmberBridgeViewController)?.statusStyle = light ? .darkContent : .lightContent
        }
        call.resolve()
    }

    @objc func share(_ call: CAPPluginCall) {
        let text = [call.getString("title"), call.getString("text")].compactMap { $0 }.joined(separator: "\n")
        DispatchQueue.main.async { [weak self] in
            guard let vc = self?.bridge?.viewController else { return call.reject("no view") }
            let sheet = UIActivityViewController(activityItems: [text], applicationActivities: nil)
            sheet.popoverPresentationController?.sourceView = vc.view          // iPad
            vc.present(sheet, animated: true) { call.resolve() }
        }
    }

    @objc func fold(_ call: CAPPluginCall) { call.resolve(["fold": false]) }

    private func reloadWidgets() {
        #if canImport(WidgetKit)
        WidgetCenter.shared.reloadAllTimelines()
        if #available(iOS 18.0, *) { ControlCenter.shared.reloadAllControls() }
        #endif
    }
}

/// Registers our local plugins (Capacitor 8: subclass the bridge view controller and set it as the storyboard's
/// root view controller class in Main.storyboard).
class EmberBridgeViewController: CAPBridgeViewController {
    var statusStyle: UIStatusBarStyle = .lightContent { didSet { setNeedsStatusBarAppearanceUpdate() } }
    override var preferredStatusBarStyle: UIStatusBarStyle { statusStyle }

    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(EmberNativePlugin())
        bridge?.registerPluginInstance(EmberHealthPlugin())
        bridge?.registerPluginInstance(EmberPushPlugin())
    }
}
