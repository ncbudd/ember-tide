# Ember & Tide on iPhone and Apple Watch: plan

Status: research + groundwork (Swift sources in `web/ios-native/`, not compiled yet). Written 2026-11-07.

## Recommendation in short

- **Same approach as Android:** a Capacitor 8 iOS shell around the existing web app (one UI codebase, already
  phone/tablet aware), plus small native Swift pieces for what the web can't do: Apple Health, push with
  buttons, widgets, Live Activity, Siri, Control Center, Apple Watch.
- **The web app runs unchanged:** the native plugins keep the Android plugin names and methods (`EmberNative`,
  `EmberHealth`, `EmberPush`).
- **Build without buying a Mac first:** use a rented cloud Mac (about $75–125 a month, cancel any time) or
  Codemagic's free 500 macOS minutes a month to compile and sign. A used Mac mini (M1/M2) is the long-term answer
  if this becomes regular work.
- **Distribute through TestFlight:** testers install a link from Apple's TestFlight app, with no App Store
  review for internal testers.
- **This needs the paid Apple Developer Program ($99 a year).** Push notifications and TestFlight require it, and
  free 7-day provisioning would make the partner's phone stop working every week.

## What it does (feature map)

| Feature | iPhone / Watch | Android today |
|---|---|---|
| The app (Tonight, Rhythm, Sleep, Settings, Simple app) | Capacitor iOS shell, same web app | Capacitor Android |
| Watch or ring data | **Apple Health** (HealthKit): Apple Watch, Oura, Withings… → same `/wearable` + `/stages` | Health Connect |
| Push with buttons ("Turn it off", "Wait an hour") | FCM → APNs, decrypted in a Notification Service Extension, buttons via notification categories | FCM / UnifiedPush |
| Running flash on the lock screen | **Live Activity** + Dynamic Island: countdown, "+5 min", "Stop" | ongoing notification |
| Home / lock-screen widget | WidgetKit: temp, state, Hot flash + power buttons (iOS 17 interactive) | resizable widget |
| One-tap hot flash anywhere | **Control Center control** (iOS 18) | Quick Settings tile |
| Voice | **Siri / Shortcuts** App Intents: "Hot flash in Ember & Tide", "Make Ember & Tide cooler" | (HA voice) |
| Watch | watch app (temp, Hot flash, +5/Stop, power) + Smart Stack / complication widget with a Hot flash button (interactive on watchOS 11+) | Wear OS tile + complication |
| Watch sign-in | phone hands it over (WatchConnectivity) | Wear Data Layer |
| Token storage | Keychain (shared access group for extensions) | app-private prefs |

## Architecture

- **App target** (Capacitor): `EmberBridgeViewController` registers three local plugins.
  - `EmberNativePlugin` stores the sign-in for widgets and the watch, starts and ends the flash Live Activity,
    reloads widgets and controls, handles the notification permission, status-bar colour and share sheet.
  - `EmberHealthPlugin` + `HealthSync`:
    - reads HealthKit and sends the same metrics as Android;
    - dedupes per metric and hour (keeps the app with most readings);
    - first sync 7 days, then incremental with a 2-hour overlap;
    - wakes in the background via `HKObserverQuery` + hourly background delivery.
  - `EmberPushPlugin` registers the FCM token with this install's Web Push keys (`POST /api/notify/me/push`,
    `kind: fcm`). Notification categories cover the server's fixed action set (`notify.py APP_ACTS`).
- **Shared code** (`Shared/`): App Group defaults (server, person), Keychain (token), a tiny HTTP client that
  never follows redirects, and the same "view" logic as `wear/WearApi.kt`.
- **Extensions:**
  - Notification Service: decrypts the push and sets title, text and category.
  - Widgets: home/lock widget, Live Activity, Control.
  - watchOS app + widget extension.
- **Apple Health caveats:**
  - HealthKit HRV is SDNN (Health Connect gives RMSSD). The server stores it under `hrv` as is; Autopilot doesn't
    use HRV for decisions yet.
  - Apple Watch skin temperature is "sleeping wrist temperature" (iOS 16+), one value per night, not a stream: fine
    for baselines, too coarse for minute-by-minute Autopilot.
  - HealthKit never reveals whether read permission was granted. "Connected" means "asked"; no data simply means
    nothing was shared.
- **Oura → Apple Health:** the Oura app writes sleep (with stages) and heart rate to Apple Health. Sources
  disagree on HRV and temperature (likely not written). So the partner's Oura data reaches us via Apple Health for
  sleep stages and heart rate. Oura's own cloud API (OAuth) is now built (`core/embertide/oura.py`, 2026-11) as the
  server-side fallback: it works from the iPhone web app today and fills in what Apple Health doesn't carry
  (temperature deviation, readiness). Duplicates between the two paths are dropped on import.

## Server changes needed (not made yet; small)

1. **`fcm.py` `send()`, iOS messages:** today it sends data-only messages (`data.e` = ciphertext) with an
   `android` block. iOS doesn't reliably deliver data-only pushes: they're silent, throttled, and dropped when the
   app was swiped away. For iOS devices, add an `apns` block:
   ```json
   "apns": {"headers": {"apns-push-type": "alert", "apns-priority": "10"},
            "payload": {"aps": {"alert": {"title": "Ember & Tide", "body": "Open to see what's up"},
                                "mutable-content": 1, "sound": "default"}}}
   ```
   - The Notification Service Extension replaces the placeholder with the decrypted text.
   - Keep `data.e` as is.
   - Use priority 5 for low-priority messages, and set `apns-expiration` to match the 6 h TTL.
2. **Push registration (`/api/notify/me/push`):** accept and store `platform: "ios"` (the iOS app sends it), so
   `fcm.py` knows when to add the `apns` block. Simplest rule: always add it, since Android ignores `apns`, but the
   placeholder alert must not show on Android. FCM's `android` block keeps data-only there.
3. **Notification actions:** the iOS categories are fixed (`ET.manual_off`, `ET.skip`, `ET.pause60.skip.start_now`).
   If `notify.py` adds a new action combination, add a matching category in `EmberPushPlugin.swift`.
4. Optional, later: **push updates for the Live Activity** (`apns-push-type: liveactivity`, FCM HTTP v1 supports
   it), so a flash started from the dial or watch shows on the iPhone lock screen. Not needed at first: the app
   starts and ends the Live Activity itself, and the countdown runs on the phone.

## Onboarding, Apple Health and the wearable import (added 2026-11-07)

Apple Health (HealthKit) is the **primary** data path on iPhone; direct Oura is only for what Apple Health doesn't
carry. The pieces are in place so the HealthKit plugin drops in exactly like Health Connect:

- **Same import endpoint and shape.** `POST /api/people/{pid}/wearable` (`metric`, `points`, `unit`, `relative`,
  `align`) and `POST /api/people/{pid}/stages`. The body now takes `platform`: `"android"` (Health Connect), `"ios"`
  (HealthKit; `HealthSync.swift` sends it), `"server"` (Oura cloud). It's recorded in the import log only; the data
  is treated the same, aligned to the bed's heart rate and de-duplicated across devices.
- **HealthKit read types → our metrics** (as `HealthSync.swift` maps them):

  | HealthKit type | Our metric | Notes |
  |---|---|---|
  | `sleepAnalysis` (asleepCore/Deep/REM, awake, inBed) | `/stages` light / deep / rem / awake | iOS 16+ stage values |
  | `heartRate` | `hr` | count/min |
  | `heartRateVariabilitySDNN` | `hrv` | SDNN (Android sends RMSSD): compare per person, not across platforms |
  | `respiratoryRate` | `resp_rate` | |
  | `oxygenSaturation` | `spo2` | ×100 |
  | `restingHeartRate` | `resting_hr` | |
  | `appleSleepingWristTemperature` | `skin_temp` | Series 8+, iOS 16+ |
  | `bodyTemperature`, `basalBodyTemperature` | `body_temp` | |
  | workouts, `stepCount`, `activeEnergyBurned`, `dietaryCaffeine`, `bodyMass` | `exercise_min`, `steps`, `active_kcal`, `caffeine_mg`, `weight_kg` | day context, off by default |

- **Entitlements:** HealthKit + HealthKit Background Delivery; `NSHealthShareUsageDescription`. Read-only (no
  `NSHealthUpdateUsageDescription`).
- **Background delivery:** `enableBackgroundDelivery(for:frequency: .hourly)` on sleep analysis and heart rate, plus
  an `HKObserverQuery` per type that calls the same sync as the button, and a `BGAppRefreshTask` as a backstop.
  Delivery is best-effort (iOS decides); the morning sync on app open covers the rest.
- **APNs:** FCM with an `apns` block as above (Server changes, 1–2). The iPhone Home Screen web app's Web Push
  (`web_push.py`) keeps working alongside; a person with both gets both, so the iPhone app should remove the web
  subscription (`POST /api/notify/me/push/remove`) when it registers native push.
- **The setup flow already branches on platform** (`web/src/screens/WearablePicker.tsx` `platform()`):
  `ios-app` (Capacitor reports `ios`) shows each brand's Apple Health step and a "Connect Apple Health" button that
  calls the same `EmberHealth.connect()` as Android; `ios-web` shows "Apple Health: available in the iPhone app" and
  offers Oura's cloud. The brand × platform table is `web/src/wearables.ts` (also in `docs/onboarding.md`).
- **What changes on iOS vs Android:** Health Connect → Apple Health; "From this phone" sources: location (Core
  Location significant-change + region monitoring instead of WorkManager polling), Wi-Fi SSID needs the Access WiFi
  Information entitlement and location permission, next alarm is not readable on iOS (drop it), calendar via
  EventKit (times only), charging/Focus via `UIDevice` battery state and the Focus status API, bedside dial via Core
  Bluetooth iBeacon ranging (only while the app is running or with region monitoring); the Wear OS tile becomes the
  watchOS widget; Quick Settings tile → Control Center control.

### Step by step on the Mac (no Xcode build attempted from Windows)

1. Install Xcode 26+, sign in with the Apple developer account (the owner types the password), clone/copy the repo.
2. `cd web && npm ci && npm i @capacitor/ios@^8.6 && npm run build && npx cap add ios && npx cap sync ios`.
3. Open `web/ios/App/App.xcodeproj`; follow `web/ios-native/README.md` steps 2–10 (signing, capabilities, add the
   Swift files, Firebase Messaging package, `GoogleService-Info.plist`, extensions, watch targets).
4. Build to a real iPhone (HealthKit and push need a device), sign in to the server, run the first-time setup: the
   wearable step now shows Apple Health for each brand.
5. Check: Settings → Watch or ring → Sync shows rows arriving with `platform: "ios"` in Studio → Devices → imports;
   send a test notification; add the widget.
6. Make the server changes above (apns block, platform on registration) in the same pass, with tests.

## Web app changes needed (small)

- **Platform-aware wording:** "Health Connect" → "Apple Health" on iOS (`screens/HealthConnect.tsx`, Settings
  card titles). The Android-only Quick Settings and widget hints get iOS equivalents ("Add the widget", "Add Hot
  flash to Control Center").
- **Android-specific calls must be skipped on iOS:** the back-button handler and `minimizeApp`.
- **Server address in the iOS shell:** the app loads from `capacitor://localhost`, which the server's CORS already
  allows (`app.py`), so nothing to do there.
- **App Transport Security:** plain `http://` home-network addresses need an exception in Info.plist
  (`NSAllowsLocalNetworking`). An HTTPS address (the public tunnel) needs nothing.

## Building without a Mac

| Option | Cost (check current prices) | Code stays off GitHub? | Effort | Notes |
|---|---|---|---|---|
| **Rented cloud Mac** (MacinCloud, Rentamac, MyRemoteMac…) | ~$75–125/month dedicated M2/M4; ~$1/hour pay-as-you-go | yes (copy the repo over) | low: real Xcode over remote desktop | Best first step: wire the targets once, build, upload to TestFlight. Cancel after. |
| **Codemagic** CI | free 500 macOS min/month; then ~$0.10/min | yes: any Git URL / self-hosted Git (verify bare URL in their docs) | medium: needs the Xcode project committed + signing set up via App Store Connect API key | Good for repeat builds after the project exists. |
| **Xcode Cloud** | some hours included with the membership; more is paid | yes with **self-managed GitLab** (e.g. on the home server) | medium | Apple-native TestFlight uploads. Needs the project created in Xcode once. |
| **GitHub Actions macOS runners** | free minutes for public repos, paid for private | **no** | medium | Ruled out while the code stays off GitHub. |
| **Used Mac mini (M1/M2)** | ~$350–500 one-off | yes | low | Best long-term: also runs the iOS Simulator and watch pairing locally. |

`npx cap add ios` works on Windows (verified: it generates the Xcode project with Swift Package Manager). Compiling,
signing and running need macOS.

## What the owner has to do (the assistant never types Apple passwords)

1. Join the **Apple Developer Program** ($99/year) with an Apple ID (individual is fine).
2. In App Store Connect, create the app (bundle id `app.embertide`), and create an **App Store Connect API key**
   for CI uploads if Codemagic or Xcode Cloud is used.
3. In the Apple Developer site, create an **APNs key** (.p8). Upload it to the existing Firebase project (Project
   settings → Cloud Messaging → Apple app configuration). Add an iOS app `app.embertide` there and download
   `GoogleService-Info.plist`.
4. Pick the build route (table above) and sign in to it.
5. Invite testers in TestFlight (internal testers need App Store Connect access; external testers need a short
   Beta App Review). Install TestFlight on the partner's iPhone and accept the invite.
6. A privacy policy URL is required for HealthKit apps, even in TestFlight external testing. One short page on the
   home server is enough.

## Build order

1. **Shell:** `cap add ios`, wire `EmberNativePlugin` and sign-in. Run the web app on an iPhone against the
   server: Tonight, Simple app, Settings. Fix CORS/ATS. (~1 session on a Mac.)
2. **Push:** Firebase iOS app, APNs key, `EmberPushPlugin`, Notification Service Extension, the server's `apns`
   block. Test "Turn it off" from a lock-screen notification.
3. **Apple Health:** `HealthSync`, the toggles in Settings, background delivery. Verify Oura sleep stages and heart
   rate arrive and align (the server's alignment and dedupe already handle it).
4. **Widgets, Live Activity, Control, Siri:** the widget extension. Test the flash countdown and buttons from the
   lock screen.
5. **Apple Watch:** watch app + widget, sign-in handoff, Hot flash from the wrist.
6. **TestFlight** to the household; then decide on the App Store (public release) later.

## Risks

- **No Mac on hand:** the first wiring needs interactive Xcode. A rented Mac for a weekend covers it.
- **Unverified code:** the Swift here hasn't been compiled. Expect small API fixes in Xcode (it follows the
  current documented APIs).
- **iOS background limits:**
  - HealthKit background delivery is best-effort (hourly at most for most types).
  - Widgets refresh on iOS's schedule (about every 15 min, more after a button press).
  - Live Activities last up to 8 hours, which is plenty for a flash.
- **Data gaps:** Oura through Apple Health has no HRV or temperature, so the partner's Autopilot relies on bed
  data plus heart rate and sleep stages.
- **TestFlight builds expire after 90 days:** rebuild at least quarterly, or ship to the App Store.
- **Interactive watch widgets need watchOS 11+,** and Control Center controls need iOS 18+. Older systems show the
  info without buttons, and Siri still works.
