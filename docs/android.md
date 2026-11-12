# The Android app, widget, tile and Wear OS app

The Android app is the phone web app wrapped with [Capacitor](https://capacitorjs.com/), plus native parts: a home-screen
widget, a Quick Settings tile ("Hot flash"), Health Connect sync, native notifications, and optional phone sources (home Wi-Fi,
location, calendar, next alarm, charging). A Wear OS app, tile and watch-face complication control your side from the watch.

Everything below is optional except the app itself; the phone web app at your server's address works without any of it.

## Download the app

Each release has its apps on the repository's **Releases** page, signed with the project's release key:

| File | Pick it if |
|---|---|
| `ember-tide-<version>-android-google-push.apk` | **Most people.** Notifications arrive through Google push (nothing else to install). It uses the project's own Firebase project, so your server needs that project's key to send to it: see [Which server can use which app](#which-server-can-use-which-app) below. |
| `ember-tide-<version>-android-no-google.apk` | Your phone has no Google services (GrapheneOS, LineageOS, a Huawei phone), or you would rather not use Google. Notifications come through **ntfy** (UnifiedPush) or Web Push. Works with any server. |
| `ember-tide-<version>-wear-os.apk` | You have a Wear OS watch and want the watch app, tile and night heart rate. Install it **in addition to** a phone app. |
| `SHA256SUMS.txt` | To check that a download is intact. |

The two phone apps are the same app: same name, same id (`app.embertide`), same signing key. They differ only in how notifications
are delivered. Everything else (widget, tile, Health Connect, watch handoff, the whole interface) is identical.

**Install an APK.** Download it on the phone (or copy it over), open it, and allow "install unknown apps" for the browser or file
manager when Android asks. Or with USB debugging on: `adb install -r ember-tide-<version>-android-google-push.apk`.

**Check it (optional).** In the folder with the downloads, `sha256sum -c SHA256SUMS.txt` (Windows PowerShell:
`Get-FileHash <file>` and compare with the line in the file).

Open the app, type your server's address (`http://192.168.1.20:8790` at home, or `https://sleep.example.com`), sign in. The app
accepts plain `http://` only for addresses on your home network; anything else must be `https://`.

**Updates.** Nothing updates itself. When a new release comes out, download the newer APK and install it over the old one: your
sign-in and settings stay. Android only accepts an update with a higher version number signed with the same key, which every release is.
Switching between the Google-push and no-Google apps works the same way: both have the same id and the same key, so installing one over the other
keeps your sign-in and settings (tested). After switching, open More, Settings, Notifications on this phone once more: going from Google push to
no-Google clears the old Google push registration, and going the other way offers Google push again. The app carries its own copy of the web interface, so
update it after a server upgrade to get the newest screens. The watch app is updated the same way, with `adb install -r` or from the watch's own installer.

### Which server can use which app

- **No-Google app:** any server. Notifications use ntfy or Web Push, which you set up in Studio, Notifications.
- **Google push app:** the published app belongs to the project's Firebase project. For Google push to work, your server needs that project's
  service-account key (`fcm-service-account.json`, see below), which the maintainer does not hand out. Without it the app still works fully and
  falls back to ntfy, but there is no Google push. If you run your own server and want Google push, **build the Google-push app yourself** with your own
  Firebase project (below).
- **Planned:** a small push relay, so the published Google-push app works with any server without anyone sharing a Firebase key. It is not built yet.

## Build it yourself

You need: Node 20+, JDK 21, and the Android SDK (Android Studio's, or the command-line tools) with platform 36.

```bash
cd web
npm ci
npm run build
npx cap sync android
```

Tell Gradle where the SDK is, once, in `web/android/local.properties` (this file is git-ignored):

```properties
sdk.dir=/home/you/Android/Sdk
# Windows: sdk.dir=C\:/Users/you/AppData/Local/Android/Sdk
```

There are two flavors, **full** (Google push) and **minimal** (no Google at all), each in `debug` and `release`:

```bash
cd android
./gradlew assembleMinimalDebug     # Windows: gradlew.bat assembleMinimalDebug
./gradlew assembleFullDebug        # needs app/google-services.json (see below); without it, Google push is simply off
./gradlew :wear:assembleDebug      # the watch app
```

The phone APK is `app/build/outputs/apk/<flavor>/debug/app-<flavor>-debug.apk`; the watch's is `wear/build/outputs/apk/debug/wear-debug.apk`.
The minimal flavor contains no Firebase code and needs no Firebase file. A debug build is signed with a throwaway key, which is fine for your own phone,
and carries a few test hooks (fake push, fake health data) that release builds leave out.

**Release builds and signing.** `assembleFullRelease`, `assembleMinimalRelease` and `:wear:assembleRelease` sign with your own key. Make one once
(`keytool -genkeypair -v -keystore embertide.jks -alias embertide -keyalg RSA -keysize 4096 -validity 10000`), keep it and its password somewhere safe
(`*.jks` and `*.keystore` are git-ignored; never commit one), and give Gradle either environment variables:

```bash
export ANDROID_KEYSTORE_FILE=/path/to/embertide.jks ANDROID_KEYSTORE_PASSWORD=... ANDROID_KEY_ALIAS=embertide ANDROID_KEY_PASSWORD=...
```

or a `keystore.properties` file (`storeFile`, `storePassword`, `keyAlias`, `keyPassword`; `storeFile` is read relative to that file's folder):

```bash
./gradlew assembleMinimalRelease -PembertideKeystoreProps=/path/to/keystore.properties
```

With neither, release builds come out unsigned (and Gradle says so). The watch app must use the **same key** as the phone app: Wear's phone-to-watch link only pairs apps
signed with the same certificate, and Android only updates an app with one signed by the same key. Set the version with `EMBERTIDE_VERSION` (default `0.1.0-dev`) and
`EMBERTIDE_VERSION_CODE` (a whole number that only goes up; default 1), as environment variables or `-P` properties.

Copy a phone APK to the phone and open it, or with USB debugging on: `adb install -r app/build/outputs/apk/minimal/release/app-minimal-release.apk`.

The app's id is `app.embertide`, min Android 8 (API 26), target 36.

## Notifications: your choice of Google or not

Notifications work three ways. Pick one per phone; they can be mixed across the household.

1. **No Google (UnifiedPush through ntfy)**: the no-Google app, or either app when Google push isn't set up. Install the ntfy app on the phone, then in
   Studio, Notifications set the ntfy server the phones use. Messages are encrypted for each phone. In the no-Google app, More, Settings, Notifications
   on this phone says plainly that Google push isn't part of this build and points to ntfy.
2. **Google push (Firebase Cloud Messaging)**: the Google-push app. Free, quickest to set up on a normal Android phone, but Google carries the
   (encrypted) message. It needs the server to hold the key of the Firebase project the app was built with (the published app's is the project's own; see
   [Which server can use which app](#which-server-can-use-which-app)).
3. **Fallback**: ntfy topics or Home Assistant notification targets, which step in only when app push fails.

### Setting up your own Firebase project (only for option 2 on your own build)

Everything here is yours, never the maintainer's: you make a project, and nothing about it belongs in this repository.

1. In the [Firebase console](https://console.firebase.google.com/), create a project (Analytics not needed).
2. Add an Android app with package name **`app.embertide`**. Download its **`google-services.json`** and put it at
   `web/android/app/google-services.json`. It is git-ignored. The Gradle build applies the Google services plugin only
   when the file exists, and only the **full** flavor uses it.
3. In Project settings, Service accounts, **Generate new private key**. Save the file on the server as
   `fcm-service-account.json` in the data folder (`./data` in the Compose setup), or anywhere and set
   `ET_FCM_CREDENTIALS` to its path. It is picked up without a restart. Studio, Notifications then says "On (Firebase
   project ...)".
4. Rebuild the Google-push APK (`npm run build && npx cap sync android && ./gradlew assembleFullRelease`, signed as in [Build it yourself](#build-it-yourself)) so it carries your
   `google-services.json`, and install it.
5. On each phone: More, Settings, Notifications, turn on notifications for this phone. Android asks for the notification permission.

Keep both files private. They are covered by `.gitignore`; `scripts/publish_check.py` flags key blocks.

## Health Connect (watch and ring data)

Health Connect is Android's shared health store. Ember & Tide reads heart rate, sleep sessions, and (if your watch writes
them) HRV, skin temperature, breathing rate, SpO2, resting heart rate and similar, and sends them to your server
(Studio, Insights show what came in). It only reads, and only what you allow.

1. Install the Health Connect app if your Android version doesn't include it, and make sure your watch's app (Samsung
   Health, Fitbit or Pixel Watch, Garmin Connect via a bridge, Oura, ...) writes to it.
2. In Ember & Tide: More, Settings, Watch or ring, pick your brand and connect. Android shows the permission list: allow the
   sleep group; add "Read in the background" so syncs happen without opening the app.
3. The app syncs every 6 hours and when opened. A night's data often reaches Health Connect **hours late** (Samsung Health
   in particular); each sync re-reads the last 48 hours for that reason, so a night fills in during the day.

If something looks missing, see [Troubleshooting](troubleshooting.md#health-connect-data-arrives-late-or-not-at-all).

## Widget and Quick Settings tile

- **Widget**: long-press the home screen, Widgets, Ember & Tide. It resizes from 1x1 to 4x2 and shows your side's temperature,
  phase and a flash button.
- **Quick Settings tile**: pull the shade down, pencil (edit), drag the **Hot flash** tile in.

Both use a limited device key made when you sign in on the phone (only your own side's flash, temperature and tonight's snapshot, never
your password); signing out of the app ends them. See and revoke them under Settings, Account, Signed-in devices.

## Phone sources (optional)

Settings, From this phone: home Wi-Fi, distance to a saved home spot, calendar (times only), next alarm, charging and bedtime mode,
watch connected, bedside dial. Each switch asks for its own permission when you turn it on. Location and Wi-Fi ask for
"Allow all the time". None of it is required; the bed's own sensors do the main work.

## Wear OS app and tile

The watch app shares the phone app's id and signs in by itself over the Wear data layer once the phone app is signed in.

Install the `ember-tide-<version>-wear-os.apk` from the release (or build it: `./gradlew :wear:assembleRelease` in `web/android`, signed with the same key as the phone app):

```bash
adb -s <watch-serial> install -r ember-tide-<version>-wear-os.apk
```

Turn on the watch's developer options and ADB over Wi-Fi to get a serial (`adb pair` then `adb connect`). Then on the watch,
swipe to the tile list, Add tile, **Ember & Tide**.

**The watch app** (open Ember & Tide from the app list) is made of pages you swipe between sideways, built for a round screen and
the rotary crown. The first page has everything you need in the night, with nothing to scroll:

1. **Your bed.** Your name in your colour, what your side is doing in plain words ("Deep · 81°", "Pre-conditioning", "Off tonight",
   "Not in bed yet") and the times: before bed "Wind-down 10:15 PM / In bed 11:00 PM" (from your rhythm; while waiting for a sign of sleep
   "Fall asleep when you settle"), during the night the next change ("Warm wake at 9:00 AM"). Then the temperature with **−** and **+**
   (°F or °C as you set them, within the server's limits; tap the number, then turn the crown; taps and turns are gathered for half a
   second and sent once) and the power button: **Off tonight** (asks you to tap again), **Resume**, **Turn on now** or **Turn off**, with the
   same rules as the tile. While your side is off the number is dimmed and labelled "Off": it shows the temperature "Turn on now" would
   start at, and **−** / **+** choose that temperature (nothing is sent until you turn it on). When your side is off for tonight or
   paused, **−** / **+** are dimmed; Resume goes back to your rhythm.

   **In bed.** When the server says you are in bed (the same signal as "In bed" on the phone's Tonight screen), the first page
   switches by itself to a quiet night layout: a big temperature in the middle, large **−** and **+**, and one big **Off** / **On** /
   **Resume** button (**Off** asks you to tap again, so a bump in the night doesn't switch the bed off). No name, no sentences, no
   times. The crown changes the temperature straight away, with no tap to arm it. A flash running shows as a small
   "Hot flash · 12 min" chip you tap to stop, and a dim line appears only if the bed can't be reached. Colours follow the phone's night
   screen: near-black, warm off-white numerals, or dim red if "Red text on the night screen" is on. The layout switches in at the next
   refresh after you get in bed, and back to the full page only after you have been out of bed for about half a minute, so a blip in
   the presence reading never flickers it. The other pages stay a swipe away.
2. **Flashes.** **Hot flash** (snowflake: cooling relief) and **Cold flash** (sun: warming relief). While one runs you see the minutes left,
   **+5 min** and **Stop**.
3. **Your partner's side**, read-only, in their colour. This page is only there if the server shares the other side with you.
4. **Settings.** **Heart rate at night** (shows whether the permission is allowed; tap it for the explanation and the permission steps;
   heart rate goes to the phone only while your night is on, and only if you switched it on in the phone app), who and which server the
   watch is signed in to, the app version, and "Sign in on your phone" when it isn't signed in.

If the watch isn't signed in the first page says "Sign in on your phone"; if the server can't be reached, "Can't reach your bed right now"
with Try again. The screen refreshes when you open it and every 30 seconds while it is on screen, and stops when it isn't. Every change also refreshes
the tile and complication. The crown only changes the temperature after you tap the number; it never swipes between pages.

**The tile** shows tonight, a Hot flash button and the on/off button (during a flash: +5 and Stop).

## Updating the app

Install the newer APK over the old one (`adb install -r`, or open the file): same id and same key, so settings and sign-in stay. See
[Download the app](#download-the-app). The app carries its own copy of the web interface, so after a server upgrade update the app too to get the newest screens.
