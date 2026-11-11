# Ember & Tide · developer handoff

Where the project stands and how to work on it. Household-specific details (addresses, devices, accounts,
deploy targets) are deliberately NOT in this repo; they live in a private notes file next to it
(`../ember-tide-private/HANDOFF-PRIVATE.md`). Read both before changing anything that touches a real bed.

Last updated: 2026-11-11.

**2026-11-11, public-repo refresh.** README rewritten as a project front page with screenshots (`docs/images/`, made with
`scripts/screenshots/`); new guides `docs/install.md`, `docs/bed-cutover.md` (replaces LOCAL.md), `docs/android.md`,
`docs/configuration.md` (all `ET_*` variables), `docs/troubleshooting.md`; `docker-compose.example.yml` now includes a pinned Mosquitto 2.0.20
with `deploy/mosquitto/{mosquitto.conf,acl}`; CONTRIBUTING, CODE_OF_CONDUCT, CHANGELOG, issue and PR templates; CI also builds the image.
Two test files were reworded so `publish_check.py` is clean (`hub_first`, banned-word list). The repository is still private.

## What it is

A local-first controller for a dual-zone heated/cooled mattress topper (Orion). One server runs on the home
network; everything else (phone app, web Studio, bedside dials, Home Assistant, watch, widgets) talks to it.

- **Rhythms**: per-person nightly temperature plans made of phases (wind-down, fall asleep, deep, late, warm
  wake) anchored to bedtime, in-bed time, clock time or wake.
- **Presence**: the night starts when the person is actually in bed (confirm minutes), with a schedule fallback
  when sensors are silent. Presence is a weighted vote of the bed's own sensor strip plus optional devices.
- **Flashes**: hot/cold flash = temporary target with +5 min, stop, ease-back.
- **Tonight controls**: warmer/cooler (until next phase / rest of night / every night = edit rhythm), pause,
  off tonight, start now, turn on by hand with a timer (naps).
- **Autopilot**: watches signals (feedback taps, skin temp, movement, HR, flashes, pad vs plan, room) and either
  suggests or makes small bounded changes; morning suggestions; optional AI review via an OpenAI-compatible LLM.
- **Wearables**: Health Connect import from the Android app; import API with clock-shift alignment.
- **Notifications**: native app notifications (FCM default, UnifiedPush/ntfy optional), Web Push-encrypted
  end to end; fallback to ntfy / Home Assistant notify targets.

## Layout

```
core/                     Python 3.12 · FastAPI · SQLite (one file in the data dir)
  embertide/
    app.py                wiring: config, store, controller, routers, auth middleware, CORS
    config.py             all ET_* environment settings (see below)
    models.py             pydantic models: Household, Person, Rhythm, Phase, Schedule, settings, AutopilotPolicy, NotifyPrefs
    store.py              SQLite: kv, events, telemetry, samples, stages, users/sessions/invites/tokens
    engine/
      night.py            NightEngine: one person's night state machine (state, target, flash, override, skip, pause)
      controller.py       Controller: tick loop, presence fusion (fuse), bed commands, snapshot()
      rhythm.py           resolve a rhythm for a date into timed phases, curve(), target_at()
      presence.py         bed sensor filtering (echo, blips, stale)
      autopilot.py        evaluate() window → decision; Autopilot.tick/replay; signals; calibration week (learning),
                          per-person baseline, trust per source (bed/wearable/room), bed + watch HR merge
      context.py          person_context(): home/away (phone Wi-Fi, GPS), heading-home ETA, calendar busy_until, lights
      guide.py            tuning guide: bedtime/wake drift (schedule proposals), lights, late events, away, day data
      summary.py          per-night summaries and "moments" timeline
      insights.py, stages.py
    drivers/              base.py interface; sim.py (simulated bed + scripted sleepers); orion_mqtt.py (real bed, local MQTT); orion_cloud.py
    api.py                snapshot, adjust, flash, tonight, household, rhythms, nights, sim, console, export
    autopilot_api.py      autopilot status/night/replay/feel/wearable import/imports/recheck/trends/suggestions/ai-review
    studio_api.py         sources (devices) CRUD + validation, calibration, explorer, stages, agreement, insights, AI ask
    sources.py            DEVICE_TYPES catalog + SourceManager (HA entity / MQTT topic / sim drivers, per-type vote logic);
                          context types phone (home), location (km), calendar (event end), light; sim_step() for tests
    auth.py, auth_api.py  users, sessions (cookie or Bearer for the app), invites, API tokens, authorize()
    notify.py             Notifier: triggers, actions, providers (ntfy/HA), app push (UnifiedPush + FCM), fallback order
                          + quiet hours, daily caps, de-dup, morning batching (inventory and rules: docs/notifications.md)
    voice.py              wording: 3+ variants per message type per tone (calm / friendly / cheeky); alerts always plain
    notify_api.py         providers, settings (incl. push server), /api/notify/me/push*, one-tap action endpoints
    webpush.py            RFC 8291 aes128gcm encryption (tested against the RFC vector)
    web_push.py           Web Push for the browser / iPhone Home Screen app: VAPID key in the data dir (vapid-private.pem),
                          browser push-service allowlist, payload; sent from notify.py for device kind "web"
    cloud/                cloud accounts (docs/integrations.md): base.py shared OAuth2/refresh/polling/backoff/storage;
                          oura.py, withings.py, whoop.py, polar.py; generic.py webhooks + JSON pollers; cloud_api.py routes
    oura_api.py           Oura's own routes (/api/oura/*, callback /api/oura/callback) on the cloud framework
    fcm.py                FCM HTTP v1 sender (service-account JWT → OAuth token)
    ha_bridge.py          Home Assistant MQTT discovery bridge (climate, buttons, sensors, numbers per person)
    integrations_api.py   HA bridge settings
    align.py              wearable import alignment (lag cross-correlation)
  tests/                  pytest (asyncio auto); ~194 tests
web/                      React + Vite + TypeScript (one bundle: phone app + Studio)
  src/
    App.tsx               phone shell (tabs Tonight/Rhythm/Sleep/More), theme, routes; /studio → Studio
    api.ts                fetch wrapper (X-Embertide header, Bearer in the app, 12 s timeout), live WebSocket
    auth.tsx              AuthGate: server pick (app), setup, login, join (invite), act (one-tap)
    native.ts             bridges to the Android plugins (EmberNative, EmberHealth, EmberPush) + back button
    screens/              Tonight, Simple (night mode), RhythmScreen, SleepScreen, Settings (autosave), Account,
                          HealthConnect, PhoneNotifications
    components/           Horizon (chart), Sheet (swipe-down bottom sheet), Glance (tablet 2nd column), back.ts
                          Fold (collapsible section, phone + Studio `box`), Tip (ⓘ: hover/focus popover, tap note on touch),
                          ShowMore (first N of a long list + "Show all N")
    studio/               Studio shell + Explorer, Autopilot, Insights, RhythmEditor, Devices, Integrations,
                          Notifications, Access (people & invites)
    screens/Welcome.tsx   first-time setup (one decision per screen; docs/onboarding.md has the settings audit)
    wearables.ts          brand × platform data table (Health Connect / Apple Health / cloud) used by setup + Settings
    pwa.ts, public/sw.js  iPhone/standalone detection, Web Push subscribe, service worker (push only, no caching)
    theme.css             all styles (dark default; .light; .amoled/.night for Simple)
  android/                Capacitor 8 Android project (appId app.embertide)
    app/src/main/java/app/embertide/
      MainActivity.java   registers plugins; phones locked portrait
      EmberNativePlugin   configure/signOut (hands server+token to widget/tile/watch), flash notif, bars, share
      Ember.java          prefs + tiny HTTP client for native parts (no redirects)
      EmberWidget.java    resizable home widget (1×1 … 4×2 layouts)
      FlashNotifier.java  ongoing flash notification + action receiver (also NOTIF_ACT for pushes)
      FlashTileService    Quick Settings "Hot flash" tile
      HealthSync.kt / EmberHealthPlugin.kt   Health Connect reads → /wearable, /stages; WorkManager 6 h
      EmberPush.kt / EmberPushPlugin.kt / EmberFcmService.kt / EmberCrypto.kt   app notifications (FCM + UnifiedPush)
      EmberWear.kt        pushes sign-in to the watch over the Wear Data Layer
    app/src/debug/        HealthTestData receiver (fake night into Health Connect; adb only)
    wear/                 Wear OS tile app (same appId): EmberTileService, PhoneLinkService, WearApi
docs/                     LOCAL.md (running the topper locally), this file
Dockerfile                web build stage + Python image; serves web from the core on one port
```

## The bed hub (control unit health and maintenance)

A driver that can read the control unit fills `BedFrame.hub` (`drivers/base.py` `HubHealth`) and lists the matching
capabilities (`hub_health`, `water_status`, `faults`, plus the existing `led_dim`, `led_rgb`, `fan_quiet`, `reboot`,
`water_fill`). Vendor flags are translated inside the driver into generic fault codes; `hub.py` holds the plain-language
names and one-line advice (never "open the unit"). Optional driver methods: `set_light`, `set_quiet_fan`, `restart`,
`start_water_fill`; a driver may mark capabilities it offers but has never run on a real unit in `untried` (the UI says so).

- Everyone signed in sees `system.hub` in the snapshot: status line, water ok, faults (names only). No network name or address.
- `GET /api/hub` (owner only) adds advice, Wi-Fi signal and network details, restart time, firmware, last command and
  whether the unit confirmed it, fan speed, humidity. Studio → Bed hub shows it; the phone's Settings → Bed hub has the
  status line for everyone and light, colour and quiet fan for the owner.
- Owner-only actions: `PUT /api/hub/light` `{brightness?, rgb?}`, `PUT /api/hub/fan` `{quiet}`, `POST /api/hub/restart`
  and `POST /api/hub/fill` `{confirm: true, force?}`. Restart and fill refuse while a night is running unless `force`; a
  restart can be asked for once every 5 minutes. After a restart the controller drops its retry back-offs, so the current
  targets are re-sent as soon as the unit reports again (the unit forgets its setpoints).
- Water: only the value seen on a healthy unit counts as OK; any other value is "Check the water" (its meaning is unconfirmed).
- Notifications: kind `hub` (`NotifyPrefs.hub`), once per problem per day, owner phones only, held by quiet hours unless the
  safety check fails while a night runs. The simulator can raise a fault or low water (`POST /api/sim` `fault`, `low_water`).

## Care and maintenance reminders

Generic list of jobs with intervals, kept in the store (kv `care`); the hardware profile (`drivers/profiles.py`, a driver's
`profile`) supplies the default jobs and the hub light-colour guide. `care.py` (model, dates), `care_api.py` (`/api/care`),
`notify.py` `_care` (kind `maintenance`), `ha_bridge.py` (sensor and button per job), `web/src/components/Care.tsx`
(Studio cards, phone list, Tonight line, light guide). Full description, permissions, default jobs and their sources:
docs/care.md. Owner-only edits, sleepers may mark done or snooze (auth.py `authorize`). The reminder goes to every owner or
sleeper with their own switch on (`care_persons` hook in app.py); Android push buttons call the two care routes with the
phone device key (auth.py `DEVICE_CARE`; EmberPush.kt `care_*`).

## Running locally

```bash
# server (simulated bed, sign-in off) — Windows needs the venv python
cd core && ET_AUTH=off ET_TZ=America/New_York ET_DATA_DIR=./data-dev ET_PORT=8790 .venv/Scripts/python.exe -m embertide
# web dev (or `npm run build` and the server serves web/dist)
cd web && npm run dev
```
- Studio: `/studio`. Simulator page moves the clock (`POST /api/sim {"jump_to": ISO, "speed": n, "presence": {...}}`).
- A jump skips presence confirmation; use `POST /api/people/<id>/tonight {"action":"start_now"}` to start a night.
- A second instance with sign-in on (different data dir/port) is handy for app testing.

### Settings (environment)
`ET_BED` (sim | orion-mqtt | orion-cloud), `ET_MQTT_URL`, `ET_ORION_SERIAL`, `ET_DATA_DIR`, `ET_PORT`, `ET_HOST`,
`ET_TZ`, `ET_AUTH` (on|off), `ET_TRUST_PROXY` (behind Cloudflare), `ET_PUBLIC_URL`, `ET_WEB_DIR`, `ET_SIM_SPEED`,
`ET_LLM_URL`/`ET_LLM_MODEL`/`ET_LLM_KEY` (Ollama/OpenAI-compatible; Ollama needs `reasoning_effort: "none"`,
already sent), `ET_LLM_PROVIDER` (openai default | anthropic: Claude via the SDK, `pip install embertide[claude]`, model
defaults to claude-opus-5-5, key from ET_LLM_KEY or the SDK's own credentials), `ET_HA_MQTT_URL`, `ET_HA_DISCOVERY_PREFIX`, `ET_FCM_CREDENTIALS` (else `<data>/fcm-service-account.json`).

## Tests and checks

```bash
cd core && .venv/Scripts/python.exe -m pytest -q            # all; ~3 min
cd web && npx tsc --noEmit -p . && npm run build
cd web/android && ./gradlew.bat :app:assembleDebug :wear:assembleDebug --no-daemon -q
cd web/android && ./gradlew.bat lintDebug --no-daemon -q      # expect 0 errors
cd web/android && ./gradlew.bat :app:testDebugUnitTest        # EmberCrypto RFC vector
npm audit --omit=dev ; uvx pip-audit -r <freeze>              # deps (2 moderate react-router left: needs v7)
```
Simulation lab: tests/lab.py + test_lab_*.py (profiles over many nights, wearable misalignment, fusion, decisions,
replay, context combinations), test_context, test_calibration, test_guide, test_real_hc (real Health Connect data,
skipped unless ET_REAL_HC points at the private extract).
Whole-night simulations of presence, sensors and the night engine: `tests/simnight.py` (the kit: `Sim` rig with a recorder,
`Plan` for who is in bed when, HA / MQTT / phone / watch devices, a bed with garbage frames, a stale clock, ignored commands)
and `test_sim_night_{sensors,people,time,controls,calibration,gaps}.py`. `xfail` there means a judgement call or a known gap
(the reason says which and what the question or proposed wording is); `test_sim_night_gaps.py` is strict, so it fails loudly
when a missing notification is added and the marker should come off. Radar tests need a step of about a minute (the settle-in
check wants samples); `Sim.add` handles that.
Key suites: test_scenarios (night situations end to end), test_use_cases (sensors start/stop nights, every Tonight
button over HTTP, settings, rhythm editor), test_studio_sweep (every Studio call), test_devices (device types),
test_notify (triggers, push, FCM, fallback), test_webpush, test_autopilot_suite, test_auth.

## Android

```bash
cd web && npm run build && npx cap sync android
cd android && ./gradlew.bat assembleDebug --no-daemon -q
adb -s <device> install -r app/build/outputs/apk/debug/app-debug.apk
adb -s <watch> install -r wear/build/outputs/apk/debug/wear-debug.apk
```
- `local.properties`: `sdk.dir=C\:/Users/.../Android/Sdk`. JDK 21.
- `app/google-services.json` (gitignored) enables FCM; without it the app runs and falls back to UnifiedPush.
- Emulator tips: WebView devtools via `adb forward tcp:9333 localabstract:webview_devtools_remote_<pid>` and a
  small CDP script to evaluate JS; avoid typing while the keyboard suggests words; Git Bash mangles device
  paths (`MSYS_NO_PATHCONV=1`).
- Wear OS emulator: the Wear OS 6.1 image crashes this emulator version (hasReadColorBufferDma); use Wear OS 5.
  Add a tile from adb: `am broadcast -a com.google.android.wearable.app.DEBUG_SURFACE --es operation add-tile --ecn component app.embertide/app.embertide.wear.EmberTileService`.
- Release build NOT done yet: needs a signing key, and proguard keep rules for Capacitor plugin annotations.

### "From this phone" (PhoneSources.kt, EmberPhonePlugin.kt; server phone_api.py + engine/phone_context.py)
- Switches in Settings: location (distance to a saved home spot only), home Wi-Fi, calendar (times only), next
  alarm, charging/bedtime mode, watch connected, bedside dial. Each asks for its permission when switched on;
  location/Wi-Fi/dial then ask "Allow all the time" (a settings page on Android 11+, not awaited).
- Sending: WorkManager every 15 min, every 5 min while away in the evening, at once on switch changes and
  NEXT_ALARM_CLOCK_CHANGED; a failed send (offline, 429) retries after 1 min. No wake locks. Uses LocationManager
  (no play-services-location): a fix under 60 s old, else one fresh fix per provider (fused first).
- Bedside dials: iBeacon UUID 7c1f0e2a-5d3b-4e8a-9b6c-ed0a11e1de00, major 1, minor 1 = left / 2 = right
  (server kv `dial_beacons`, `PUT /api/dial-beacons`). The app listens 8 s, low-power, filtered on that UUID, only
  19:00–11:00 and only while charging, in bedtime mode or with the screen off; reports median RSSI per dial.
  Server: phone by its owner's own dial (≥ −70 dBm, or 8 dB clearer than the other) + charging/bedtime = a 0.15
  in-bed vote; never a "not in bed" vote. BLUETOOTH_SCAN is NOT `neverForLocation`: Android filters iBeacons
  out of scan results when that flag is set.
- Emulator testing: geo fixes only arrive while GPS is listening, and Play services' fused provider keeps
  replaying an old spot, so use a mock fused provider:
  `appops set com.android.shell android:mock_location allow`, then `cmd location providers add-test-provider fused`,
  `set-test-provider-enabled fused true`, `set-test-provider-location fused --location LAT,LON`. Calendar: create a
  local calendar with `content insert --uri 'content://com.android.calendar/calendars?caller_is_syncadapter=true&account_name=x&account_type=LOCAL'`.
  Alarm: `am start -a android.intent.action.SET_ALARM --ei android.intent.extra.alarm.HOUR 6 --ez android.intent.extra.alarm.SKIP_UI true`.
  Charging `adb emu power ac on`, DnD `cmd notification set_dnd on`. Fake dial (debug builds only):
  `am broadcast -n app.embertide/.FakeDial --ei minor 1 --ei rssi -60` (also makes the app send now;
  `--ez clear true` stops pretending).
- targetSdk 36. Moving to 37 (Android 17): talking to the server on the home network may need the new
  local-network permission (NEARBY_WIFI_DEVICES / ACCESS_LOCAL_NETWORK, depending on the final API), which would
  need its own switch-style prompt; background location rules are unchanged as far as we know. Recheck the
  BLE scan and Wi-Fi SSID access (SSID needs fine location today) when bumping.

### Me or not me: watch heart rate, pets, corrections (engine/watch.py, petq.py, compare.py; identity_api.py)
- Live watch heart rate: Wear app registers Health Services PASSIVE monitoring (HR + steps) only while the phone says
  the person's night is on (server `watch_hr_wanted` on phone-context replies and on each relay); batches go to the phone
  over MessageClient (/embertide/hr) at most every 5 min, the phone's WatchHrListener posts them to
  POST /api/people/{pid}/watch-hr. Switch: Settings → From this phone → "Watch heart rate (live)". Watch permissions:
  BODY_SENSORS(+_BACKGROUND) ≤ Wear OS 5, health.READ_HEART_RATE + READ_HEALTH_DATA_IN_BACKGROUND on Wear OS 6+, asked
  by the watch app's "Heart rate at night" screen. Debug-only: `am broadcast -n app.embertide/.wear.DebugHr --ez on true
  --ez wanted true` (watch) and `am broadcast -n app.embertide/.FakeWatchHr --ei bpm 62` (phone). Emulator synthetic data:
  `am broadcast -a whs.USE_SYNTHETIC_PROVIDERS com.google.android.wearable.healthservices` then `whs.synthetic.user.START_WALKING`.
- Fusion (controller._watch_vote): watch agrees with the pad → `watch:me` vote; clearly apart (or footsteps while the pad
  is steady) held 5 min, early in the stretch, never after it agreed → "not me" vote; radar/scale seeing a person wins.
  Weights follow "Which to trust more" (AutopilotPolicy.trust_more). fused[zone]["watch"] has the verdict.
- Pets: watch mismatch, far outside the learned range (Autopilot band hr/br, after the learning week) and learned pet
  signatures (kv pet_sigs) on an otherwise empty side ("otherwise empty" comes from the strip's own stretch, watch or
  not). Sleeper marked away (not a guest's side): the strip occupied 3 min = the pet, evidence "away" ("you're away,
  so this is probably <pet>"; "a pet" when several fit and none is told apart), unless a radar there sees someone for
  most of the stretch (then left for the morning away check) or their watch says it's them; the stay is held for
  learning even when the radar zone saw nobody (pets.py AWAY_CONFIRM_S, away.hold).
- Is it them or the dog, outside a running night (pets.py `_identity`, controller `_pet_hints`): watch "not me" = the
  pet even when the radar sees someone (it sees the dog); their phone (phone_context.bedside_state: fresh within 20 min,
  home Wi-Fi, not charging, own dial unheard or weaker than −70 dBm = "out"; bedside charger or a strong own dial =
  "in"; stale = unknown) alone = the pet only before the usual bedtime, with a pet allowed there and the readings like
  its signature or no learned range yet, held 5 min: it holds off an early start, never ends a running night or an
  out-of-bed hold. Watch "me" overrides both. With a pet allowed on a side, a radar seeing "someone" no longer ends the
  wait for the watch's verdict (scale still does). By day/evening the watch is asked for heart rate for the first
  20 min of a stretch, at most 3 stretches an hour (watch.day_ask; the phone checks in every 15 min, so 10 min could be
  missed). Tests: test_sim_night_dog_identity.py. Pet questions (kv pet_questions, one open per person, grouped per
  night) on Tonight, Sleep and Studio Nights; Yes teaches the signature, "No, it was me" widens kv person_range:<pid>.
- Corrections: kv night_fix:<pid>:<date> laid over summaries (raw kept in `raw`), win for Autopilot learning; per-person
  in-bed offset kv inbed_offsets:<pid>. Comparison: GET /api/people/{pid}/compare (also in /night). Tuning:
  PersonSettings.in_bed_after_min / up_after_min (None = recommended 3 / 3).

### Watches and rings coming and going (engine/wearables.py)
- Every device's skin temperature and wearable heart rate has its own level, learned from the learning nights. The
  device with the most nights is the reference (the normal range lives on its level); another device is lined up by the
  nights both reported (two at once: one night) or, after `RELEARN_NIGHTS` (3) nights of its own, by the level
  difference. Until then a new device is "learning": its readings count within a night but never against the normal
  range and don't teach it (`Window.unlearned`). If a known device reports in the same window, the new one is matched
  to it there and then. `Autopilot.skin()` / `wear_hr()` return the lined-up series; `wear_hr(level=False)` is raw (the
  comparison uses raw). The same reading under two names (live watch path + a Health Connect import, ≤ 2 s apart)
  counts once, at import and at read time. A copying app (Health Sync etc., `wearables.COPY_APPS`) with the same value
  as another app within 30 s (`COPY_S`, as on the phone) is a copy: the original app's reading is kept (it replaces a
  copy that arrived first). Two real devices (a watch and a ring) keep the 2 s rule.
- Flat heart rate (an import of 30+ readings with spread under 0.6 bpm: probably not worn) is stored and marked
  (kv `wear_flat:<pid>`, `wearables.mark_flat`), never deleted. `device_series` leaves marked stretches out by default,
  so baselines, wearable levels, live evaluation and the bed comparison never use them; the device list, the Sleep
  screen's comparison (`not_worn` + a plain line) and Settings → Watch or ring coverage still show them.
- `Autopilot.wearable_status()` → `/autopilot` `wearables` (learning / lined up / quiet, with `text` such as
  "New ring (Oura): learning its readings for 2 more nights"); Insights shows a card for a learning or quiet device
  (`studio_api.wearable_cards`). `compare_night` has `devices` and a "Readings from …" line; rechecks store `devices`.
- Lab: tests/lab.py `Wearable` (name, skin/hr, bias, sampling, gaps, live path, on/off); test_lab_wearable_switch.py.

### Health Connect sync and import timing (HealthSync.kt, HealthMerge.kt, align.py, autopilot_api._import_wearable)
- Watch apps (Samsung Health in particular) write a night's heart rate, SpO2 and sleep session into Health Connect hours
  late, and Health Connect matches series/session records by START time. So every sync re-reads at least the last 48 h
  (`HealthMerge.readFrom`) and queries a day earlier still (`queryFrom`); the server drops exact repeats of a device's
  own readings before anything else. Sleep sessions are sent when new or changed (prefs `hc_sessions_sent`: id → last
  change). Copies by Health Sync etc. are dropped only where the original app has a reading within 30 s.
- Clock shifts: phone data carries the phone's clock, so times are kept unless `align.judge` finds strong evidence
  (distinct best fit, r ≥ 0.6, ≥ 120 min overlap, beats no-shift by 0.15). Short/patchy imports get a plain note, not a
  warning. The watch's live heart rate (`watch_live`) vouches for timing when it overlaps (`align.live_agreement`).
- `GET /api/people/{pid}/wearable/nights` (per night: HR coverage per device, stages, plain text about what's missing;
  shown in Settings → Watch or ring). `POST /api/people/{pid}/wearable/repair` (`wear_repair.py`): undoes heart-rate
  shifts older versions applied on weak evidence by forgetting those readings; lists by default, `{"apply": true}`
  carries it out once (kv `wear_repair:*`); then tap Re-import last 7 days on the phone.
- Phone log: `adb logcat -s EmberHealth` shows each sync's window and per kind/app counts and time ranges (no values).

### Assistant (assistant.py, assistant_api.py, ai.py; web studio/Insights.tsx Ask panel)
- `/api/assistant/ask` → answer + change cards. Tools (assistant.ACTIONS, JSON schema + strict pydantic): adjust_tonight,
  change_rhythm_phase, start_flash, stop_flash, turn_off_tonight, set_autopilot_mode, handle_suggestion,
  mark_night_stretch, set_notifications. The model only proposes; `/proposals/<id>/apply` runs the change through the
  app's own routes in-process with the asker's credentials (same validation and auth); `/changes/<id>/undo` reverses
  it unless it was changed again since. Log in kv `assistant:<pid>` + `assistant_change` / `assistant_undo` events.
- `PersonSettings.assistant_small_changes` ("Let the assistant make small changes for me"): own side, asked for in the
  person's words, tonight ±1.5 °C in total or a flash; ≤ 3 automatic per night, ≤ 10 changes an hour, 30 asks an hour.
- Another person's side only with their sharing (partner_can_adjust / _flash), never automatic; never their rhythm or
  settings. Household text (notes, device and pet names) is cleaned and sent inside `<data>`; a question that doesn't
  ask for a change keeps only advice cards (rhythm phase, a waiting suggestion), marked "you didn't ask for this".
- Tests use a scripted fake model (test_assistant.py); the OpenAI-compatible and Anthropic clients are checked with
  fakes (no network, no keys). `/api/ai/ask` (older apps) and the morning review now drop any proposal that isn't a
  real phase within ±1.5 °C.

## Conventions

- Plain, calm language in the UI; no jargon; names default to "Left"/"Right" until people name their side.
- Person colours: `PersonSettings.color` picks from `models.PALETTE` ("" = side default, left teal / right orange).
  Web uses `personaColor(p)` → `var(--pc-<key>)` (dark/light/night variants in theme.css); the HA bridge
  publishes `sensor.<obj>_color` ("#rrggbb") for dials. Swapping sides pins each person's colour.
- Right-handed layouts: primary controls on the right / bottom.
- Light theme must keep text ≥ 4.5:1 contrast on its darkest card.
- Long sections use Fold (one-line state summary in the header); jargon gets a Tip instead of a paragraph.
  Long lists use ShowMore. Form fields: `.fld` / `.field` (rows never stretch, inputs share one height); a hint
  that only one field of a row has goes under the row as `.fld-hint`, never inside that field.
- Pets: pet_suspected/pet_cleared events have no person; engine/summary.py `pet_moments` turns them into a "pet"
  moment (with a plain `text`) in the story of whoever sleeps on that side, naming the pet only when it's the
  only one that fits (same kind; for a heart-rate reading, any fast-hearted pet).
- Bed scales (engine/bedscale.py): each scale keeps an empty-bed zero (follows drift slowly while the side is confirmed
  empty; takes in a steady new load such as a duvet or laundry after 30 min empty; follows weight coming off after
  3 min) and a learned sleeper weight; "in bed" = 60% of that weight (never above 85%, never below the device setting),
  plus settle 10 s / hold 20 s. Pet flags from a scale need the strip or a radar to see something alive and last 60 s.
  Calibration keeps `empty_kg` / `side_kg` per scale id; tests in test_bed_clutter.py.
- Autopilot calm: baseline()["band"] = the learning nights' middle 80% of bed temp, HR, skin temp, plus restless p90.
  Inside it (with small padding) evaluate() makes no decision unless the person tapped or flashed; a reading must be
  out both in the last 5 min and the 10 min before. Phone suggestions: at most one per cadence period (kv
  `ap_shown:<pid>`), "Not now" rests a phase+direction 14 nights. `POST /people/<pid>/autopilot/recalibrate` sets
  `learn_since` (only later nights teach), resets trust, deletes that person's autopilot events, ap:<pid>:* samples,
  rechecks and suggestion history; settings saves can't move `learn_since`. Recheck stores `moments`, `text`.
- Learning nights left out (autopilot._nights, each with a plain reason in `learning.left_out`, shown on the Studio
  Autopilot card and phone Settings while learning): under 3 h in bed, away, 2 h+ later than usual, ring says running
  warm, plan mostly not running, and (2026-11-11) the bed or server off for 60 min+ while in bed (summary `outage_min`:
  holes of 10 min+ in the 15 s history; `OUTAGE_MIN`) or awake and moving for half the time in bed or more (summary
  `awake_moving_share`: 10-minute steps with active.py's signs, no 2 h limit; `AWAKE_SHARE`).
- Learned restlessness (restless_p90, band.restless) pools the learning nights' 10-minute windows as before, but with 3+
  nights a night whose own 90th percentile is over twice the middle night's and 0.10+ above it is left out of that pool
  (`Autopilot._usual_restless`); same meaning and scale, unchanged for ordinary weeks.
- Pets editor: a form with Save; saved pets are summary cards with Edit/Remove.
- Devices (studio/Devices.tsx): ONE add flow (studio/AddDevice.tsx) from both "+ Add a device" and the catalog tiles:
  kind → (wearable: brand, from web/src/wearables.ts) → path (HA / MQTT / calendar link / phone app / Health Connect /
  Apple Health "coming" / Oura cloud via /api/oura/status / web hook via /api/cloud/providers / Simulated only on a
  sim bed) → who it's for → set up. Kinds and "who" rules live in studio/deviceKinds.tsx. Every tile shows
  "Added: N · Add another" with Edit per item. The list is grouped by person + "Both of you"; each row: state dot,
  plain reading, who tag, Edit (who / name / kind / connection / tuning / Zero / Pause / Remove).
- Who it's for (server, studio_api._clean): wearable/phone/location need a person (a wearable follows its owner's
  side); a calendar with no person is shared (engine/context._mine); a bed sensor on one side belongs to that
  side's sleeper; one radar/scale covering both sides = sources sharing `SourceConfig.group` (one per side),
  managed by POST/PUT/DELETE /api/sources/group[/<gid>] (parts {zone, params, id?}; ids keep history; duplicate
  check across the group). sources.migrate() runs at start. HA entity filters per kind: ha_connection_api.NOT_FIT.
- Presence for both sides has three cards in the add flow's "who" step: one radar with zones, two single-zone sensors
  one per side (e.g. two FP1E by the nightstands), or one side only. Two sensors = a group with `layout: "two_sensors"`
  (GroupBody.layout, stored in each part's params; kept on Edit unless sent). Its HA picker uses kind `radar_single`
  (occupancy/presence binary sensors). Fusion (controller._crosstalk): when both of the pair fire but this side's strip
  and scales all say empty, this side's radar vote is left out (`fused[zone].crosstalk`). Tests: test_two_sensor_radar.py.
- Dials: /api/dials (HA template on ESPHome devices named "dial" + beacon sightings; side per dial in kv
  `dial_sides`), studio/DialSetup.tsx wizard (found dials / from scratch: hardware → count → generated YAML →
  wait + knob test). The shared package is core/embertide/dial/ (public copy, Wi-Fi via !secret), served at
  /api/dials/files/<name>. Integrations is grouped: Home Assistant · Bedside dials · Cloud accounts (stub until
  CloudAccounts.tsx lands) · Advanced (MQTT).
- Sides of the bed: ONE picker, components/BedSidePicker.tsx (bed from above, headboard at the top, seen from the foot,
  so "Left side · as you lie" is the RIGHT half of the drawing; tap a half). Used by setup, Who's using this phone,
  Settings, People & access, guest mode, the add flow and BedZones. SideCheck (same file) watches snapshot `zones`
  (per-side presence) and offers one-tap swap/move. Controller.swap_sides: tonight's in-bed state stays with the side
  (the person really lying there takes over a started night), personal devices follow their owner
  (SourceManager.follow_owners), dial_sides and dial beacon sides flip, bed sensors stay. Tests: test_sides.py.
- Dials ↔ beacons: the dial package publishes "Home side" (the person it starts on, as their HA object id) and
  "Beacon" (beacon_minor) diagnostic text sensors. /api/dials links each dial to its beacon (firmware, kv
  `dial_beacon_of`, or a beacon named after it), shows heard-by, `hidden` (kv `dial_hidden`, PUT /api/dials/<id>/hidden);
  setting a dial's side moves its beacon too. Devices lists everything by person through studio/deviceGroups.ts
  (sources, dials, phones, cloud accounts, the Settings watch); add new device kinds there.
- Late bedtime (rhythm.py `_fit_late_start`): when getting in late would leave a phase between in-bed and the warm
  wake with under half its fair share, those phases are squeezed proportionally (a 3 AM start with a 7 AM wake: fall
  asleep 03:00, deep ~03:17, late ~04:58, warm wake 06:30 as usual). A little late keeps the usual layout. The
  `rhythm_started` event carries `fitted`; summaries mark `late_by_min` (≥ 2 h after bedtime); Autopilot doesn't
  learn from nights that started 2 h+ later than the person's own median in-bed time. Pre-conditioning an empty side still follows "Pre-condition an empty side until" (window_end).
- A night given up early starts again (night.py `_given_up_early`): a night started by presence, then out of bed
  60 min+ without a break (`REANCHOR_AWAY_S`), having got up within 45 min of the start (`REANCHOR_IN_BED_S`), or
  within 90 min when up before the usual bedtime (`REANCHOR_EVENING_IN_BED_S`), and not in the last 2 h before wake:
  getting back in un-starts it (`NightState.reanchor`, persisted with the night) and it starts through the usual start (settle gate, confirm,
  away, skip, pause) from the beginning. Events `rhythm_started` (`restarted`) + `rhythm_restarted` (`in_bed`,
  `was_in_bed`, `up_since`); summaries count in bed from the new start (`earlier_in_bed` note), the story says
  "Started again from the beginning (you'd been up since …)". A next-phase change from the given-up night ends; a
  whole-night change carries. A 3 AM hour up after hours asleep resumes as always.
- In bed early, waiting for sleep (night.py `_sleep_tick`, engine/sleepsign.py; `Schedule.wait_for_sleep`, default on,
  phone Rhythm tab + Studio Rhythm editor under "Start when I'm in bed on my side"): a night started by getting in before
  the usual bedtime runs as always but the in-bed phases after the first ("Deep" at in-bed + 40) count from the sleep
  anchor `NightState.sleep_at` (persisted), the earliest of: phone bedtime mode coming on after getting in
  (`phone_ctx.bedtime_since`), the phone going on the charger after getting in by their own dial (`bedside_side`),
  the bed settling (still in 90%+ of readings for 30 min and the heart rate in each 5 min of the last 15 at least 4 bpm
  under the 15 min before that stretch; never while active), a live sleep-stage segment from a watch/ring, a bedroom
  light going off, and the usual bedtime as the backstop (no signs = as if they got in at bedtime). Signs only count
  while they're in bed on their side. Event `sleep_anchor` (`at`, `why`), trace `night.sleep`, snapshot `night.sleep`
  and `when_settled` on pending phases / `next`; Tonight says "In bed · waiting until you settle to sleep", the
  plan says "When you settle", the story "You settled around …". Getting in at or after bedtime, start now, the
  schedule start and the setting off are unchanged. Tests: test_sim_night_sleepsign.py.
- Restarts / power cuts (controller._restore): nights are persisted with `_saved_at`; downtime doesn't count towards
  an out-of-bed hold; presence is judged afresh, and when both pads come back with identical readings the side whose
  night is running is the real one (PresenceFilter.prefer, `_tie_echo`). No telemetry from a frozen frame while the
  bed is offline; one "Can't reach the bed" note (no per-sensor stale notes while offline); command back-off resets
  when the bed reports again. Summaries don't count a hole in the history as time out of bed. Tests:
  test_power_loss.py (tests/sim_edges.py has PowerBed / ActiveBed / Rig).
- Active, not sleeping yet (engine/active.py): sustained moving + heart ≥ 90 on one or both sides (other side moving
  or empty, a person's heart when they lay down, ≤ 2 h). While on: Autopilot only acts on a tap and never learns from
  it (Window.active_windows), no pet / watch "not me" / near-copy echo decisions, no out-of-bed hold, a night that
  hasn't started waits (one that just started holds "Fall asleep"). Events `active_started` (with `began`) /
  `active_ended`, no person; the story shows "Active, not sleeping yet" and counts it as before sleep (`active_min`).
  Tests: test_active_period.py. The simulator's echo now starts a few seconds after a pad changes (as on the real bed).
- Cold / warm room (engine/padlag.py): each side learns (hourly, 14 days of telemetry + room sensors, kv
  `padlag:<zone>`) how fast its pad warms / cools per 2 °C room bucket. In a room under 21 °C a warmer next phase
  starts early by the lag beyond its ramp (≤ 30 min, ≤ half the current phase; not after a "too hot" tap in the last
  30 min; flash/override/guest unchanged; the phase name doesn't change early); above 23 °C the same for cooler phases
  and the evening pre-condition. A steady warm target missed by > 0.5 °C for 30 min in a room under 20 °C gives
  night.room_note (Tonight) and one `room_cold` story moment per phase ("The room is cold (65°), so your side is running
  a little below 80°"); Autopilot then ignores the bed-temperature band (Window.room_short). No room sensor = no change.
  Tests: test_cold_room.py (sim_edges.RoomBed; SimBed._drive is the thermal hook).
- Compact (keyboard) Tonight: compact.css squeezes only below 700 px tall; taller phones keep the chart and grow.
- Settings: docs/settings-map.md lists every setting, where it is shown and who may change it; keep it current. Phone
  Settings and Studio → Your settings share their controls (components/PersonPrefs.tsx, FlashPrefs.tsx,
  screens/SettingsSleep.tsx): change one, both follow. Left/right shown to people always goes through sideTitle /
  sideLower / sideWord (bed orientation); stories and questions for a person say "your side".
- Settings autosave; destructive actions confirm inline; sheets close by swipe, outside tap, Esc and Android back.
- One calm note at a time on Tonight, highest first: no connection > bed not reporting > bed hub problem (App.tsx
  banners) > away > pet question > care due > a suggestion (Today card). Order lives in Tonight.tsx `.notes` + theme.css.
- Tonight's hero: one primary line (what the side is doing), one quieter line (what happens next and when).
- Every write needs the `X-Embertide` header (CSRF guard); the app sends a Bearer token instead of the cookie.
- Server validates everything it stores (ranges, roles, push endpoints only on the owner-approved host).
- Tests accompany behaviour changes; run the full suite before deploying.
- Never put personal names, addresses or household details in this repo.

## Status (2026-11-07)

Done and in daily use: real bed under local control; phone web app + Android app (widget, tile, flash
notification, Health Connect, native push via FCM, back button, light/dark); Studio (all pages swept); Wear OS
tile on a real watch; two bedside dials on the v5 dial firmware (home side per dial, person colours); HA bridge;
Autopilot (suggest mode); device types with per-device tuning and votes.

New 2026-11-07 (built and tested on the simulator, not yet deployed): person colours; desktop opens Studio; web
sign-in page; Autopilot calibration week + baseline + trust weights + monthly cadence; context devices (phone,
GPS, calendar, light) with away / heading-home / late-event behaviour; tuning guide; Health Connect with 13
toggleable types and duplicate removal; ~350 tests including the simulation lab.

New 2026-11-07 (branch onboarding-pwa): work days per person (`PersonSettings.work_days`, rhythms with
`nights: "work" | "off"` follow them; hand-picked days opt out); guided first-time setup; Web Push for the iPhone
Home Screen app; Oura cloud connection; iPhone/Studio safe-area and narrow-nav fixes; PNG icons.

## Next up / open

1. Deploy the 2026-11-07 work to the server; install the new debug APK on the phone.
2. Signed release build of the Android app (signing key + proguard rules), then reinstall on devices.
3. Partner onboarding (invite from More → Invite; push turns on by itself with FCM).
4. Setup wizard for first-time users (step by step).
5. Connect real context devices (phone tracker, proximity, calendar, bedroom light) in Studio → Devices.
6. iOS app later (FCM → APNs reuses the same server push code).
7. React Router v7 to clear the last moderate advisories.
