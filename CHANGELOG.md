# Changelog

All notable changes. The project follows [Semantic Versioning](https://semver.org/) loosely while it is 0.x: anything may change between
minor versions, and each entry says what to check when upgrading.

## Unreleased

- **Easier first connection in the Android app.** The server address can be typed as it is, for example `192.168.1.20:8790`:
  for a home address the app tries `https://`, then plain `http://`, then port 8790 if the port was left out. The field
  no longer starts with `https://`, which a plain home server doesn't answer.
- **Setup is easier to find on a computer.** Studio shows a "Finish setting up your side" banner with a Start setup
  button until the short setup is done. The welcome card on a computer no longer repeats the text beside it.
- **For testers.** A beta tester feedback form and a link to Discussions for questions; install guide fixes (the broker
  on Portainer, Unraid, TrueNAS and CasaOS, Tailscale Serve with Docker, a live backup with nothing extra installed,
  the cloud option without Docker, the Home Assistant line).

- **Water leak emergency off.** Added a `Water leak sensor` device type (`leak`, Home Assistant `binary_sensor` with
  `device_class: moisture` or MQTT topic; multiple sensors supported). When any enabled leak sensor reports wet, both
  sides turn off immediately without backoff and stay off under a persistent `leak_hold` (blocking rhythms, flashes,
  overrides, guest mode, HA and API turn-on commands with 409) until checked and cleared in the app (`POST /api/leak/clear`,
  owner and sleepers allowed; viewers 403).
- **Urgent leak alert and clearer hub water/fault messages.** Added urgent priority-5 `leak` notifications (bypassing
  quiet hours, sent to the household, and included in the morning summary if it occurred overnight), a Home Assistant
  `Leak hold` binary sensor, and plain-English hub water/fault guidance.
- **Nights start again in the demo and the fast Simulator.** The radar settle check needed eight readings over
  seven minutes, which a demo night (one reading every five minutes) or the Simulator at ×300 could never give,
  so those nights stayed "waiting" with a flat chart and a false pet question. The check now scales to how often
  readings arrive; real nights (one reading a second) are unchanged.
- **Removed the half-degree °F setting.** °F is now always whole degrees, and each step is 1 °F (°C is unchanged,
  in 0.5 °C steps). The "Temperature steps" choice is gone from Settings and Studio, and Home Assistant shows
  1 °F steps. A saved half-degree choice is ignored, and an older app that still sends it keeps working.

## 0.1.11

**Upgrade note:** rhythm and phase names are now limited to 80 characters, with up to 12 rhythms per person.

- **Try it in your browser.** A public demo with a simulated bed is linked from the README; nothing there controls a
  real bed and changes reset every hour.
- **Demo mode hardened for public hosting.** Small requests only, a per-visitor change limit, a cap on live
  connections, an early reset if the database grows, no trace files or API docs pages, and app error reports are
  neither stored nor shown. The example demo Compose file adds memory, CPU and process limits.
- **Fixes.** Feedback dates must be real dates.

## 0.1.10

- **Leak sensor emergency off.** If a leak sensor reports water, both sides switch off and stay off until someone
  clears the alert. Filling, priming, the pump and turning on are refused meanwhile, a banner says what happened, and
  owners are told at once.
- **Demo mode.** `ET_DEMO=on` runs a self-contained demo with a simulated bed and household: no network calls, only
  safe changes allowed, and a fresh start every hour. See `docs/demo.md` and `docker-compose.demo.yml`.
- **Half-degree steps in °F.** Each person can choose ½° steps in Fahrenheit (Settings, your preferences). Whole
  degrees stay the default; the app and notifications show the same value.
- **Screens.** The night screen fits short phones, and the Studio menu scrolls on short tablets.
- **Fixes.** Hub faults keep the hub's own description.

## 0.1.9

**Upgrade notes:** behind Cloudflare (tunnel or proxy) with `ET_TRUST_PROXY=on`, add `ET_PROXY_CLOUDFLARE=on` to keep
using Cloudflare's visitor address. With `ET_AUTH_HEADER`, `ET_TRUSTED_PROXIES` must now name your proxy. A calendar
link that redirects now asks for the final link. Rhythm and phase ids are limited to letters, digits, `-` and `_`.

- **Phone notifications with no setup.** The Android app (Google push version) now gets notifications through the
  Ember & Tide push relay when your server has no Firebase key of its own. Messages are encrypted end to end; the relay
  only forwards them. Turn it off with `ET_PUSH_RELAY=off`; Web Push, ntfy, Home Assistant and webhooks remain.
- **Safety.** A side left heating or cooling with nobody on it turns off after 2 hours (a timer you chose still runs).
  "Turn my side on" stops when your rhythm starts; flashes end when you get up or go away; every-night changes,
  suggestions and the assistant always respect your limits; an "off" press is never lost while another command is on
  its way; a lie-in past noon is no longer cut off; the bedside dial works in °C.
- **Security and privacy.** Stricter proxy and sign-in handling; viewers and sleepers no longer see the partner's live
  sensors or the AI server's address; calendar links don't follow redirects and are hidden in logs; an account with
  no side doesn't count as owning every side; the MCP lockout behind a proxy is fixed.
- **Fixes.** "Up N×" no longer double-counts a brief sensor blip; deleting a pet no longer breaks notifications;
  "in bed after 0 minutes" is respected; older nights find their events again; the Both view shows "I'm up" and turn
  on; the AI model page handles Replace and load errors; AI texts point to Studio, AI model.

## 0.1.8

**Upgrade note:** the example Compose file now keeps your data in `./data` and adds a tiny `data-init` helper that
prepares the folder before the app starts (new, empty or root-owned folders are handed to the app's user). If you copied
an earlier example, add the `data-init` service and the `depends_on` lines from the new one, or keep your own setup.

- **Easier first start.** A fresh `docker compose up -d` with the example file just works: no folder to create, no
  permissions to fix. If the data folder can't be written, the app stops with a plain message saying how to fix it.
- **Beta testing guide.** `TESTING.md`: what to try, safety notes, which connection to start with (simulator, then the
  maker's API key), known limits and how to report problems. The install guide covers running without Docker.
- **Version in Studio.** Studio, Help shows the version to include in bug reports. Release notes list the APK signing
  certificate's fingerprint.
- **Suggestions in your clock style.** The tuning guide's times follow your 12/24-hour setting.

## 0.1.7

**Upgrade notes:** the image now includes the Anthropic SDK and the MCP server package. `ET_BED` with an unknown value
now stops startup with the list of drivers instead of quietly using the simulator. In the example Compose file the
bundled Mosquitto broker moved under the `local-bed` profile (`docker compose --profile local-bed up -d`), and `TZ`
defaults to `Etc/UTC`: set your own zone.

- **Bring your own AI model.** Studio, AI model: pick a local model (Ollama, LM Studio, any OpenAI-compatible server)
  or a hosted one (Claude, OpenAI, GitHub Models, Google Gemini, OpenRouter), load its models, test and save. The key
  stays on the server. `ET_LLM_*` settings remain the defaults. See docs/ai-models.md.
- **Connect AI apps (MCP).** Studio, Connect an AI app: personal keys let Claude Desktop, Claude Code, VS Code with
  Copilot, Cursor and other MCP clients read your nights and, if allowed, make the same changes Ask can, within the
  same limits. See docs/mcp.md.
- **Your language, units and clock everywhere.** New Assistant language and 12/24-hour clock settings. The assistant,
  reviews, insights, summaries and notifications follow each person's units and clock; seasons follow your time zone
  (southern hemisphere included). New people start with their region's units.
- **More home setups.** Notifications to any webhook or an Apprise server (Gotify, Pushover, Telegram, Matrix,
  email…); sign-in through a forward-auth proxy (`ET_AUTH_HEADER`); `ET_TRUSTED_PROXIES`; MQTT over TLS (`mqtts://`);
  your own bed driver (`ET_BED=python:module:Class`, docs/drivers.md); install without Docker; `.env.example`.
- **"I'm up" ends the night.** A new button on the Tonight screen (big in the morning and during a lie-in, in the
  Tonight menu otherwise) ends the current night now: the side turns off and the morning summary counts it as done,
  even if the side was paused or switched off earlier. The dial's or watch's off press in the morning does the same
  instead of switching off the coming night. A lie-in also ends about 3 minutes after your phone and watch stop
  vouching for the bed, even if something (a pet) stays on the pad.
- **Suggestions you can act on.** Tuning-guide suggestions on Tonight now open with the full text, the nights they're
  based on and the choices right there; swipe one away for "not now". Dismissals are remembered on the server, so they
  stay dismissed on every device until the pattern changes.

## 0.1.6

**Upgrade note:** the container now runs as uid and gid 1000 instead of root. If an existing bind-mounted `data` folder
is owned by root, run `chown -R 1000:1000` on it once before updating, or add `user: "0:0"` to the Compose service to
keep the old behaviour (see docs/install.md).

- **Partner away, no false dog.** When one side's sleeper is away and that pad picks up the other sleeper's own
  heartbeat (it tracks theirs and that side's radar sees nobody), it's treated as an echo of them, not as a pet, and
  never makes the present sleeper's side look empty.
- **Lie-in.** Still in bed when the night is due to end? The wake temperature keeps running until you get up (at most
  45 minutes). A snoozed or later alarm keeps the morning going a little longer, and the phone now sends alarm
  changes right away.
- **Morning summary counts only real trips.** "Up N×" counts getting up while the night was running, not lying down
  before it started, getting up after it ended, or moments when the sensors misread an echo.
- **AI review reads the night properly.** Your phase temperatures are read as the plan you chose, the bedroom
  temperature, the season and whether the pad reached its setting are taken into account, and Autopilot's own
  findings and suggestions are summed up. With no sign of discomfort it doesn't suggest a change.
- **Morning review on Tonight.** With "Every morning" on, the review arrives as a notification ("My night, reviewed",
  its own switch) and as a "Last night's review" card you can swipe away.
- **Security.** Every pull request runs tests, type checks, Android and container builds, Semgrep, Trivy, gitleaks,
  CodeQL and dependency review; releases are scanned, signed (cosign) and published with an SBOM and build
  provenance. React Router updated to 7.18 (two moderate advisories).

## 0.1.0 (first public release)

- **The container no longer runs as root.** It runs as uid and gid 1000. If an existing bind-mounted `data` folder is owned by root, run
  `chown -R 1000:1000` on it once before updating (or add `user: "0:0"` to the Compose service to keep the old behaviour).
- **Wear OS app.** Opening Ember & Tide on the watch shows pages you swipe between (Compose for Wear OS). Page 1 fits the round screen with
  nothing to scroll: your side's state, the wind-down and in-bed times (or the next change), temperature with − and + and the crown, and
  Off tonight / Resume / Turn on now. Page 2: Hot flash (snowflake) and Cold flash (sun) with +5 min and Stop. Page 3: your partner's side,
  read-only, if the server shares it. Page 4: settings (Heart rate at night, who is signed in, version). A tile and a watch-face complication too.
- Licensed under the PolyForm Noncommercial License 1.0.0: free for personal and noncommercial use, no commercial use without permission.

The first version intended for other people's homes.

### Bed control
- Drivers for the **Orion Sleep** topper over its **local MQTT** protocol (after a local DNS record), the maker's **cloud API**, and a
  built-in **simulator** with scripted sleepers, faults and a movable clock.
- One engine owns every write: everyday range 12 to 35 °C, hard range 10 to 45 °C, per-side rate limits, an end time stored before any flash.
  Firmware, Wi-Fi and low-level pump and valve commands are refused. Verified on firmware 2.6.2.
- Bed hub page: status, water, faults, Wi-Fi signal, light colour and quiet fan, restart.

### Nights and rhythms
- Per-person rhythms of phases anchored to bedtime, in-bed time, clock time or wake, for work nights and days off.
- The night starts when the person is in bed; schedule fallback, pre-conditioning, getting up at night, pause, off tonight, start now, naps.
- Hot flash and cold flash, warmer and cooler with three scopes, a Simple screen and an optional Simple app.

### Presence and sensors
- Weighted presence vote: bed strip, mmWave radar, bed scales, phone, watch, room sensors; echo, blip and stale-clock handling;
  misbehaving devices set aside with a reason.
- Context: phone home, away or heading home, calendar feeds, lights.

### Autopilot
- Seven-night calibration week, per-person normal range, explained suggestions or small bounded automatic changes, trust per source,
  morning suggestions on a cadence, optional AI assistant through a model you choose (proposals only, undoable).
- Tuning guide for bedtime and wake drift.

### Sleep and wearables
- Night summaries, moments timeline, Nights explorer, sleep stage estimates and imported stages, comparison of bed and watch.
- Health Connect sync (Android), cloud accounts for Oura, Withings, WHOOP and Polar, generic webhooks and JSON pollers, clock-shift
  alignment, duplicate handling across devices.

### Pets, guests, away, care
- Pet detection with a question you can teach, guest mode, away mode, and care reminders for the bed.

### Notifications
- FCM (optional, your own Firebase project), UnifiedPush through ntfy, Web Push for browsers and the iPhone Home Screen app, ntfy and Home
  Assistant fallbacks; quiet hours, daily caps, morning summary, one-tap actions, three tones.

### Apps and integrations
- Web app (phone layout and Studio) served by the same container; sign-in with owner, sleeper and viewer roles, invites, API keys and limited
  device keys.
- Android app with widget, Quick Settings tile, Health Connect and phone sources; Wear OS tile. Two signed builds on the Releases page: one with
  **Google push** and one with **no Google at all** (ntfy / UnifiedPush or Web Push). Same app id and key, so either installs over the other.
- Home Assistant over MQTT discovery; ESPHome bedside dial package.
- Ready-made server image `ghcr.io/ncbudd/ember-tide` (amd64 and arm64); the Compose example pulls it. Compose example with a pinned Mosquitto and an access list, and install, bed cutover, Android, configuration and
  troubleshooting guides.

### Known limits
- The Google-push app uses the project's own Firebase project, so a server needs that project's key to send to it; other servers use the no-Google app or
  build the Google-push app with their own Firebase project. A push relay that removes this limit is planned.
- No native iPhone app yet; the Home Screen web app has no Apple Health or background location.
- Only the Orion Sleep topper is supported.
