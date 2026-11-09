# Minimal Phone 2 support (compact keyboard mode)

Status: built and tested on an emulator shaped like the device (1080×1240 @ 480 dpi = 360×413 dp, built-in QWERTY).
The API 37 image is installed and boots, but this PC's emulator build can't screenshot it (screencap asserts
`hasReadColorBufferDma`, the same bug as the Wear OS 6.1 image) and segfaults with `-gpu host`, so the visual testing
ran on an identical API 36 twin (`ember_minimal2_36`). Updating the emulator binary should fix it; it's shared with
another project's running emulator, so that's left for a quiet moment. Real hardware arrives December 2026.

## What the device is (public info, Nov 2026)

- 3.92" AMOLED, 1080×1240, 90 Hz → about 360×413 dp: short and nearly square.
- Backlit physical QWERTY keyboard; the keyboard surface is touch-capacitive (how apps receive that is unknown).
- Minimal OS on Android: a command-bar launcher, no app grid. Reported to launch on Android 17 (API 37).
- MediaTek Dimensity 8300, 8/12 GB RAM, 5G. Google Play / GMS certification was pending at announcement.

## What the app does

### Detection (web/src/keyboard.ts)
Compact keyboard mode turns on when any of these is true, unless the person forced it in Settings → Appearance →
Keyboard layout (Automatic / On / Off, kept per device):
1. The Android app reports a built-in hardware keyboard: `Configuration.keyboard == KEYBOARD_QWERTY`,
   `hardKeyboardHidden != YES`, AND a non-virtual, non-external input device with letters (`InputDevice`
   KEYBOARD_TYPE_ALPHABETIC). Pushed as `window.__etKeyboard` + `et-keyboard`, also `EmberNative.keyboard()`.
   External keyboards (Bluetooth on a tablet) get every shortcut but not the compact layout. Note: Android emulators
   pass the PC keyboard through as a built-in letter keyboard, so every emulator reads as "has a keyboard"; use
   Settings → Keyboard layout → Off on an emulator to see the normal layout. A real Pixel 9 Pro XL has no such device
   (checked read-only with `dumpsys input`), so real phones without keyboards are unaffected.
2. In a browser: the first physical key press (the on-screen keyboard reports `Unidentified`/229 and doesn't count).
3. A short near-square screen (≤ 460 px tall, aspect 0.75–1.25), i.e. the Minimal Phone 2's shape.

Everything compact lives in `web/src/compact.css`, scoped to `.app.compact`; no other device changes.

### Compact layout
The usable area is tiny: ~360×344 CSS px after Android's status and navigation bars, ~314 px tall once the tab strip
and hint bar are in. What gives:
- No bottom tab bar; a slim top strip shows the tabs with their keys (1 Tonight, 2 Rhythm, 3 Sleep, 4 More).
- Tonight: no chart (the plan list carries the night), no whose-night strip (V), no "Tonight ⋯" row (S), daytime meta
  line hidden, hero number capped, 36 px controls. Verified on screen: daytime (turn-on card + flashes + ±), a running
  flash, and the evening plan all fit without scrolling. The tablet "glance" column is hidden.
- The Simple app keeps its three tabs as a slim strip (keys 1 2 3).
- A one-line key-hint bar at the bottom (dismissable with ×); sheets fit the short screen.

### Keys (web/src/components/KeyLayer.tsx)
| Where | Keys |
|---|---|
| Tonight / Simple Home | ↑ or + warmer, ↓ or − cooler, H hot flash, C cold flash, E +5 min, X stop flash, O off tonight / back on, N turn on now (1 h), P pause an hour, F then 1/2/3 how it feels |
| Anywhere (full app) | 1–4 or T R L M switch tabs |
| Lists (Rhythm phases) | J/K or ↑/↓ move, Enter open, ← → change the temperature of the focused phase |
| Other screens (Settings…) | J/K step through buttons and switches (text fields are skipped; Tab reaches them), Enter presses |
| Tonight extras | S = "Tonight ⋯" sheet (pause, undo, off…), V = whose night (me → both → partner) |
| Sleep | ← → previous / next night |
| Welcome | Enter / → next, ← back, S skip, 1 Simple, 2 Full |
| Everywhere | Backspace back / close, Esc close, ? or holding Sym = shortcut sheet |

Keys never fire while typing in a text field or with Ctrl/Alt/Meta held. Screens declare their own keys with
`data-key="…"` on a button (clicked when the key is pressed); list items use `data-nav`. Actions from keys are logged
with source `keyboard`, so the night story says "from the keyboard".

### Minimal OS launcher
- Static app shortcuts (`res/xml/shortcuts.xml`): Hot flash, Cold flash, Off tonight, Simple screen. A command-bar
  launcher (and long-press on any launcher) lists them.
- Command text: `ACTION_SEND` with `text/plain` ("hot flash", "cooler", "off tonight", "5 more", "stop") or
  `embertide://do?q=hot+flash`. MainActivity hands the text to the web app (`EmberNative.takeAction()` +
  `et-action`), which parses it (`parseCommand`) and runs the same action as the key, logged as source `launcher`.
- Test: `adb shell am start -n app.embertide/.MainActivity --es et_action hot`, or
  `adb shell am start -a android.intent.action.VIEW -d "embertide://do?q=cooler"`.

## Android 17 (API 37)

Our app builds with compileSdk/targetSdk 36 (web/android/variables.gradle). It runs on API 37 as is (tested on the
emulator). Moving the target to 37 is a separate decision (it changes behaviour for all devices); what it would mean:

- **Local network access permission (ACCESS_LOCAL_NETWORK, runtime):** the app talks to a server on the home network
  (`http://192.168.x.x`). When targeting 37 this needs the new permission and a prompt (or the public https
  address). This is the big one for us: add the permission + a friendly explanation in Server pick before bumping.
- **Certificate transparency on by default:** fine for the Cloudflare-tunnel https address; self-signed home https
  certificates would fail (plain http on the LAN is unaffected).
- **RemoteViews memory limit:** our home-screen widget (EmberWidget, RemoteViews) must stay small: it draws text and
  vector icons only, no bitmaps, so it should be fine; check after bumping.
- **Background audio / foreground services:** we play no audio; no change.
- **Static final fields / MessageQueue reflection / native code loading:** not used by our code; Capacitor 8 plugins
  should be checked on the bump.
- **All apps on 17 (any target):** RAM-based memory limits (our WebView app is modest), IME visibility not restored
  after an unhandled configuration change (we handle keyboard/orientation changes in the activity, so no effect),
  touchpad pointer-capture changes (not used).
- **Edge-to-edge and predictive back** have been enforced since targeting 35/36; the app already draws edge to edge
  and handles back through the Capacitor App plugin. Large-screen orientation/resizability rules (API 36+) are
  already followed (MainActivity only locks portrait on small screens; resizeable).

To bump: install platform 37 (already in the SDK), set compileSdkVersion/targetSdkVersion = 37, add
`ACCESS_LOCAL_NETWORK` handling, run lint + the emulator sweep (phone, tablet, Fold, Minimal 2, Wear).

## Open questions to check on the real device

1. **Keyboard touch surface:** does it reach apps as scroll / DPAD / trackpad events (Unihertz Titan / KEY2 style),
   or only move the system cursor? If DPAD, our J/K/arrows already work; if scroll, the plan list scrolls natively.
2. **Reserved keys:** which keys Minimal OS keeps for itself (Sym, a launcher/command key, Alt+Space?). The shortcut
   sheet also opens with ?, so it works even if Sym is reserved. Check which key code Sym sends (KeyEvent.KEYCODE_SYM
   = "Symbol"/"AltGraph" in the WebView?).
3. **Does Minimal OS show static app shortcuts** in its command bar, and can it send text to an app (ACTION_SEND or a
   custom intent)? If it uses App Actions / shortcuts.xml capabilities instead, add `<capability>` entries.
4. **GMS / FCM:** if Google Play services are missing, notifications must use UnifiedPush (ntfy) — already supported
   (Settings → Notifications on this phone). Health Connect also needs checking (it's part of Android 14+, but the
   companion apps that write to it may differ).
5. **Real density and font scale:** the layout targets 360×413 dp; a different density or a large default font may
   need the compact sizes nudged.
6. **AMOLED + backlit keys at night:** the Simple night screen (pure black) is ideal; consider dimming key hints at
   night.

## Emulator recipe

AVD `ember_minimal2`: `system-images;android-37.0;google_apis;x86_64`, `hw.lcd.width=1080`, `hw.lcd.height=1240`,
`hw.lcd.density=480`, `hw.keyboard=yes`. Start on its own port (`emulator -avd ember_minimal2 -port 5650 -gpu host`);
the API 37 image renders blank with `-gpu swiftshader_indirect` on this PC. Point the app at a sim server via
`http://10.0.2.2:<port>`. Keys: `adb -s emulator-5650 shell input keyevent KEYCODE_H` etc.
