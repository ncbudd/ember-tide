# The Android app, widget, tile and Wear OS tile

The Android app is the phone web app wrapped with [Capacitor](https://capacitorjs.com/), plus native parts: a home-screen
widget, a Quick Settings tile ("Hot flash"), Health Connect sync, native notifications, and optional phone sources (home Wi-Fi,
location, calendar, next alarm, charging). A Wear OS tile shows tonight and starts a flash from the watch.

There is no store release yet: you build the APK yourself (about ten minutes the first time). Everything below is optional
except the build itself; the phone web app at your server's address works without any of it.

## Build the APK

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

Then build:

```bash
cd android
./gradlew assembleDebug            # Windows: gradlew.bat assembleDebug
```

The phone APK is `web/android/app/build/outputs/apk/debug/app-debug.apk`. Copy it to the phone and open it (allow "install
unknown apps" for your file manager or browser), or with USB debugging on:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Open the app, type your server's address (`http://192.168.1.20:8790` at home, or `https://sleep.example.com`), sign in. The app
accepts plain `http://` only for addresses on your home network; anything else must be `https://`.

A debug build is signed with a throwaway key, which is fine for your own phone. For a signed release build you need your
own keystore (`*.jks` files are git-ignored; never commit one) and proguard rules for the Capacitor plugins; neither is in
this repository yet.

The app's id is `app.embertide`, min Android 8 (API 26), target 36.

## Notifications: your choice of Google or not

Notifications work three ways. Pick one per phone; they can be mixed across the household.

1. **No Google (UnifiedPush through ntfy)**: nothing to build differently. Install the ntfy app on the phone, then in
   Studio, Notifications set the ntfy server the phones use. Messages are encrypted for each phone. Without a
   `google-services.json`, the app builds fine and uses this path.
2. **Google push (Firebase Cloud Messaging)**: free, quickest to set up on a normal Android phone, but Google carries the
   (encrypted) message.
3. **Fallback**: ntfy topics or Home Assistant notification targets, which step in only when app push fails.

### Setting up your own Firebase project (only for option 2)

Everything here is yours, never the maintainer's: you make a project, and nothing about it belongs in this repository.

1. In the [Firebase console](https://console.firebase.google.com/), create a project (Analytics not needed).
2. Add an Android app with package name **`app.embertide`**. Download its **`google-services.json`** and put it at
   `web/android/app/google-services.json`. It is git-ignored. The Gradle build applies the Google services plugin only
   when the file exists.
3. In Project settings, Service accounts, **Generate new private key**. Save the file on the server as
   `fcm-service-account.json` in the data folder (`./data` in the Compose setup), or anywhere and set
   `ET_FCM_CREDENTIALS` to its path. It is picked up without a restart. Studio, Notifications then says "On (Firebase
   project ...)".
4. Rebuild the APK (`npm run build && npx cap sync android && ./gradlew assembleDebug`) so it carries your
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

## Wear OS tile

The watch app shares the phone app's id and signs in by itself over the Wear data layer once the phone app is signed in.

```bash
cd web/android
./gradlew :wear:assembleDebug
adb -s <watch-serial> install -r wear/build/outputs/apk/debug/wear-debug.apk
```

Turn on the watch's developer options and ADB over Wi-Fi to get a serial (`adb pair` then `adb connect`). Then on the watch,
swipe to the tile list, Add tile, **Ember & Tide**. The tile shows tonight and a flash button; the app's "Heart rate at night" screen
asks for its sensor permission and sends heart rate to the phone only while your night is on.

## Updating the app

Rebuild and `adb install -r` over the old one (same id, so settings and sign-in stay). The app carries its own copy of the web
interface, so after a server upgrade rebuild the app too to get the newest screens.
