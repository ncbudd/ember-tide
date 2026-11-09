# Ember & Tide for iPhone and Apple Watch: native pieces

Swift sources for the iOS app, written on Windows and **not compiled yet** (that needs Xcode on a Mac). They mirror
the Android app's native parts one for one, so the shared web app (`web/src`) runs unchanged inside the iPhone shell.
The plan, costs and decisions are in [`docs/IOS-PLAN.md`](../../docs/IOS-PLAN.md).

| File | Does | Android twin |
|---|---|---|
| `Shared/EmberCore.swift` | server / token (Keychain) / person (App Group), HTTP client without redirects, the "view" widgets and the watch show | `Ember.java`, `wear/WearApi.kt` |
| `Shared/PushCrypto.swift` | Web Push (RFC 8291) keys + decryption | `EmberCrypto.kt` |
| `Shared/EmberIntents.swift` | App Intents: hot/cold flash, +5, stop, warmer/cooler, power; Siri phrases | widget + tile actions |
| `Shared/FlashActivity.swift` | Live Activity model for a running flash | `FlashNotifier.java` |
| `App/EmberNativePlugin.swift` | `EmberNative` plugin (configure, flash, widgets, notifications, bars, share) + bridge VC | `EmberNativePlugin.java` |
| `App/HealthSync.swift`, `App/EmberHealthPlugin.swift` | Apple Health → `/wearable`, `/stages`; `EmberHealth` plugin | `HealthSync.kt`, `EmberHealthPlugin.kt` |
| `App/EmberPushPlugin.swift` | `EmberPush` plugin (FCM token + keys → server), notification buttons | `EmberPush.kt` |
| `App/WatchHandoff.swift` | sends the sign-in to the watch | `EmberWear.kt` |
| `App/AppDelegateAdditions.swift` | launch setup, APNs token, notification delegate | `MainActivity`, receivers |
| `NotificationService/NotificationService.swift` | decrypts each push and adds its buttons before it shows | `EmberFcmService.kt` |
| `Widgets/EmberWidgets.swift` | home + lock screen widget, flash Live Activity, Control Center button | `EmberWidget.java`, `FlashTileService` |
| `Watch/EmberWatch.swift` | watch app + Smart Stack/complication widget | `wear/EmberTileService.kt`, complication |

## Wiring it up (on a Mac with Xcode 26+)

1. In `web/`: `npm i @capacitor/ios@^8.6 && npm run build && npx cap add ios && npx cap sync ios`
   (`cap add ios` also works on Windows: it writes the Xcode project with Swift Package Manager, no CocoaPods.)
2. Open `web/ios/App/App.xcodeproj`. App target → Signing: your team; bundle id `app.embertide`; iOS 17.0+
   (interactive widgets); keep iPhone + iPad.
3. Capabilities on the **app**: HealthKit (+ Background Delivery), Push Notifications, Background Modes (Remote
   notifications, Background fetch), App Groups `group.app.embertide`, Keychain Sharing `app.embertide.shared`.
4. Add `App/*.swift` and `Shared/*.swift` to the app target. In `Main.storyboard` set the root view controller's
   class to `EmberBridgeViewController`.
5. Swift Package: `https://github.com/firebase/firebase-ios-sdk` → FirebaseMessaging (app + nothing else). Drop
   `GoogleService-Info.plist` (from the Firebase project, iOS app `app.embertide`) into the app target.
6. `AppDelegate.swift`: call `EmberAppSetup.launch()` first in `didFinishLaunchingWithOptions`, and add
   `func application(_:didRegisterForRemoteNotificationsWithDeviceToken:) { EmberAppSetup.gotAPNsToken(deviceToken) }`.
7. Info.plist: `NSHealthShareUsageDescription` ("Reads your watch or ring's night data so Autopilot can tell if you're
   too warm or too cold"), `NSSupportsLiveActivities = YES`.
8. File → New → Target → **Notification Service Extension** "EmberNotify": replace its file with
   `NotificationService/NotificationService.swift`; add `Shared/EmberCore.swift` + `Shared/PushCrypto.swift`;
   App Groups + Keychain Sharing as in step 3.
9. New Target → **Widget Extension** "EmberWidgets" (include Live Activity + Control): replace its files with
   `Widgets/EmberWidgets.swift`; add `Shared/*.swift`; App Groups + Keychain Sharing.
10. New Target → **watchOS App** (+ its widget extension): split `Watch/EmberWatch.swift` into the app part and the
    widget part, uncomment each `@main`, add `Shared/EmberCore.swift` + `Shared/EmberIntents.swift`, set
    `WATCH` in Active Compilation Conditions for both watch targets.
11. Run on a device (HealthKit and push don't work in the Simulator for real data), sign in, allow Apple Health,
    turn on notifications, add the widget, try "Hey Siri, hot flash in Ember & Tide".

## Server changes this needs (not made yet)

See `docs/IOS-PLAN.md` → "Server changes": FCM messages need an `apns` block (visible alert placeholder,
`mutable-content: 1`, priority) because iOS doesn't reliably deliver data-only pushes; and the push registration
may carry `platform: "ios"`.
