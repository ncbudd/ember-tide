# Changelog

All notable changes. The project follows [Semantic Versioning](https://semver.org/) loosely while it is 0.x: anything may change between
minor versions, and each entry says what to check when upgrading.

## Unreleased

## 0.1.25

Sleep stage minutes from WHOOP and Garmin. Update the server; the phone and watch apps need no update for this one.

- **WHOOP and Garmin sleep stages on the Sleep screen.** Both share how many minutes you spent in deep, light and REM
  sleep and awake each night, but not when each stage happened. Those totals now show in the week chart, in Sleep
  stages (pick WHOOP or Garmin next to the bed's own estimate) and as a 14-night chart in History. There's no timeline
  for them, because neither service provides one. Thanks to @maulikl, who built the WHOOP part (#107).
- **WHOOP naps and sleep efficiency.** Naps from WHOOP show on their day, with whether they were on the bed or away, as
  Oura and Garmin naps do. Nights WHOOP hasn't finished scoring are picked up on a later sync. After updating, a
  connected WHOOP account fetches its last 120 days once, so earlier nights get their stage minutes too.
- **Contributing.** Pull requests and issues now ask whether an AI tool helped, and which one; the README says how the
  project itself is built.

## 0.1.24

BedJet support, for testing. Update the server; the phone apps need no update for this one.

- **BedJet and other beds through Home Assistant (new, not yet tried on a real unit).** A new bed option,
  `ET_BED=ha-climate`, runs the night on a bed that Home Assistant shows as a thermostat, one per side. First up is
  the BedJet 3: warm phases heat to the planned temperature, cool phases run its fan (a faster fan for a cooler
  setting), and when its heat switches off by itself, Ember & Tide turns it back on. It's all local, over Bluetooth
  through Home Assistant. Setup is in docs/drivers.md. If you try it, please tell us how it goes.
- **Your bedside dial can be the BedJet's Bluetooth bridge.** An optional add-on file for the dial connects it to a
  BedJet 3 next to it, so you don't need another board. The dial setup in Studio offers it.

## 0.1.23

A small follow-up. Update the server; the apps need no changes for this one.

- **Catching up after a gap counts calendar days.** When a cloud account (Oura, Garmin and the others) missed a few
  nights, the catch-up could fetch one night too few if the gap was a little under a whole number of days. It now
  counts the nights on the calendar.

## 0.1.22

Garmin connects directly, cloud accounts catch up after a gap, and "Not now" works from the notification. Update the
server and the phone app: the Garmin card is new in both.

- **Garmin connects directly.** Each person pastes their own Garmin key (Settings → Watch or ring) and their sleep,
  heart rate and HRV arrive after waking, then every hour. Nothing for the owner to set up. The key is stored
  encrypted on your server. You can say how long the key lasts to get a reminder a week before it runs out, or skip
  that. Thanks to @kevinklaes for this one.
- **Cloud accounts catch up after a gap.** After a key ran out or the server was off for a few days, the next look
  fetches every night you missed (up to two weeks), not just the last two.
- **"Not now" works from the notification.** On the morning and review notifications it showed "Couldn't reach
  Ember & Tide" and the card stayed. It now puts the card away.
- **Someone else's cloud account isn't shown as yours.** With sign-in on, viewing Settings as another person now says
  they connect their own account, instead of showing (and changing) yours.

## 0.1.21

The night review opens on the night that just ended. Update the server; the apps need no changes for this one.

- **Last night is ready as soon as you're up.** When the alarm or getting up ends the night early, Sleep → Review now
  shows that night right away. Before, it kept showing the night before until the rhythm's scheduled end time, even
  though the review notification had already arrived.

## 0.1.20

The night review reads your night correctly. Update the server; the apps need no changes for this one.

- **Your own changes read the right way round.** Turning the dial down now reads as cooler in the review, with the
  amount, what the plan had, and what you used, in your units. Before, it could describe a turn down as a turn up.
- **Everything in your units, clock and order.** Autopilot's notes, signals, skin temperature and how closely the bed
  followed the plan are in your units; times use your clock; events are in order, with your phase names.
- **A "just right" morning stays just right.** A check-in or tap that says the temperature was right no longer makes
  the review suggest changes.

## 0.1.19

The review listens to your morning check-in, and check-ins now count toward suggestions. Update the server; the apps
need no changes for this one (the watch can update itself from its Settings page).

- **The night's review follows your check-in.** If you check in after the review was written, or change your answer,
  the review is rewritten with it. There's no second notification; Sleep, Review and Tonight's card show the new text.
- **Morning check-ins count toward suggestions.** Saying "Too cold" or "Too warm" on 3 of the last 7 mornings suggests
  warming the coolest step (or cooling the warmest) among the ones you were in bed for, worded as your mornings.
- Maintenance: the release build retries when the Android package servers refuse downloads.

## 0.1.18

The watch can now update itself, and the morning check-in tidies up after itself. Update the server and both apps; the
watch app needs this one installed the usual way, and updates itself from then on.

- **The watch app updates itself.** When a new version is out, the watch's Settings page shows "Update to …". It
  downloads the watch app from the release, checks it is the real, newer Ember & Tide, and the watch asks you to confirm.
  The first time, allow Ember & Tide to install apps when the watch asks.
- **Choosing how the night felt puts the "Last night" card away,** whether you answer on Tonight or from the morning
  notification. You can still change it under Sleep, Review.
- Maintenance: dependency updates (Capacitor, Vite, Firebase, Gradle). A new Python for the Docker image is now a
  deliberate change rather than an automatic update.

## 0.1.17

Clearer mornings: the morning notification, Tonight and Sleep now work together. Update the server and the phone app
together: the new notification buttons and screens need both.

- **Morning check-in in the notification.** The morning notification has Too cold / Just right / Too warm every morning.
  When suggestions are waiting it says how many, with See suggestions (opens Sleep, Review) and Not now.
- **Tapping "Your night, reviewed" opens the review.** A review that finishes soon after an unanswered morning
  notification is added to it quietly instead of sending a second one.
- **"This morning" on Tonight.** Last night at a glance with a one-tap check-in, the night's review (shown straight away
  while it's being written), and tonight's suggestions with Apply / Not now. Swipe a card away when you're done; it stays
  gone on your other devices too.
- **Sleep has Review and History.** Review shows the night's review and what it's based on, lets you change how the night
  felt, and lists tonight's suggestions (Apply, Not now, Don't suggest this). Anything you applied can be undone there for
  a week. History lists your last 30 nights with their check-ins and reviews; tap one to open it.
- **A screen that needs a newer server no longer stays blank.** Unknown API addresses now answer "not found" instead of
  the web page, so after an app update the screen shows its usual "couldn't load" message until the server is updated.
- **The dial shows "Guest" in guest mode** instead of the usual sleeper's name. Re-install the dial firmware from Studio,
  Devices, Dials to get it.

## 0.1.16

Fixes from a walkthrough of every page with real data, and from a night with a dog on the bed.

- **Nights away stay out of insights.** Insights, the tuning guide and Ask now leave out nights while you were marked away,
  like the Sleep screen already did, so a pet on your side no longer turns into advice for you. While you're away they say so.
- **Steadier insight suggestions.** "Settling takes…" uses your typical night and only shows while it's still true lately,
  "too warm/too cold" says how many mornings, and a change goes to the rhythm (weeknights or weekends) those nights belong to.
- **Insights you don't want can be put away.** Every card on Insights & Ask has "Not now", and the Ask panel can be hidden;
  both are remembered for you on every device.
- **No "hasn't sent anything" card for a watch you replaced.** If one source of watch or ring readings stops but another
  still sends (for example after moving from Health Sync to Health Connect), Insights no longer says it has gone quiet.
- **Lying in lasts while you're in bed.** After your wake time your side keeps its warm wake going until you get up,
  instead of turning off 45 minutes later while you're still asleep. A 4-hour limit stays as a safety net.
- **Watch or ring shows what is actually sending.** Instead of "None picked", Studio and phone Settings list each watch or
  ring source that reports and when it was last seen (or that it went quiet).
- **Check for an app update from the phone.** Settings, App version now has Check now: the server looks for a new
  release straight away instead of waiting for its daily check.
- **Sleep and Tonight read right.** The night's moments are in order ("Got into bed" before "In bed early"), the Nights
  explorer shows the same moments and only that night's pet questions, Tonight says where a moved wake time comes from
  ("· from your alarm"), and the week bars match the night summary.
- **Studio tidy-ups.** Overview says "Away · side stays off" for someone marked away; a short note explains when a rhythm
  phase goes past Autopilot's Never below / Never above; Autopilot opens on last night; every away night appears in
  "Nights that didn't count"; the rhythm editor's chart covers every phase temperature.
- **Clearer wording in a few places.** The asleep-time tip credits your watch or ring when stages come from it, the
  small-changes tip follows °C or °F, phone sharing labels include watch heart rate and steps, broker errors use plain
  words, and Help matches the current control names.

## 0.1.15

Update the server and the phone app together: the new Steps switch and the "Is that you in bed?" question need both.

- **Asks whether it's you or your pet when the signs disagree.** When someone gets on your side of the bed while your watch
  says it isn't you or your phone counts you walking around (using the new **Steps** switch in From this phone), a
  high-priority notification asks right away so you can tap who's there. Saying it's your pet keeps that side out of
  your night until it's empty and undoes a false start.
- **Resume when your night is on hold.** If your side turned off because the bed saw nobody there for a while (or lost
  a pet lying on it), Tonight now shows Resume, which picks your night back up. Before, "Turn on" was accepted and
  cancelled straight away.
- **Your side stays ready for bed after a false start.** If a pet (or a short lie-down) starts your night and the side
  then empties, it now keeps the getting-in temperature until you get in, instead of turning off after 30 minutes.
  A night that's really under way still turns off when you're up for a while.
- **A phone picked up from the bedside stops vouching for you.** One scan of your side's dial no longer counts as you
  being in bed for the next 20 minutes once the phone has been picked up and used since.
- **Got into bed and got up from the bed when the watch can't know.** A watch night that starts the moment it saw sleep
  (or ends the moment you woke) no longer sets the night's in-bed or got-up time: the bed's is used, so the Sleep summary
  doesn't show a false "0 min to drift off" or start the night late.
- **"Night screen by default" applies right away.** Turning it on or off now changes Tonight straight away, even if
  the screen was already open; before, it could need a trip away and back. A night screen you opened yourself stays open.

## 0.1.14

- **The Sleep summary follows "Which to trust more".** When you lean on your watch (the default), the night's story uses
  the watch's times: got into bed, fell asleep, woke and got up, the time in bed worked out from them, and "asleep" from
  the watch's stages. It used to mix the bed's times with the watch's, so the numbers didn't add up. Trusting the bed
  keeps the bed's numbers.
- **The demo's sample nights follow the plan.** The public demo's past week showed the bed several degrees off its plan
  and suggested "the bed lags behind the plan"; that came from how the demo builds its nights, not from the controller.
- **Studio says when something fails.** People & access buttons (reset password, cancel invite, revoke) show an error
  if they fail, and a list that couldn't load says so. Bed hub and Care say they couldn't load instead of Loading… forever.
- **Each watch or ring is named once** in the Sleep page's "Readings from …".
- **Setup fixes.** "Run first-time setup again" works even right after finishing setup, and says so if it fails.
  Skipping setup keeps your region's temperature unit, as the last screen showed.
- **Small app fixes.** Last night's review shows its suggestion in your units and with the phase's name (it showed °C
  and an id like `fall_asleep`). Ending your partner's night says their side is off, not yours. The Sleep page says so
  when a night can't be loaded instead of showing Loading… forever. Warmer/Cooler before Turn on stays within what the
  bed accepts. Studio's simulator and live signals use your own units. Ending guest mode from Settings shows an error
  if it fails. The Help text for Warmer/Cooler points at the right place.
- **Setup and Help layout fixes.** In setup, the "More below" pill no longer covers the work-day buttons: it shows only
  when a good part of the step is hidden, and goes away once you scroll. On a phone, a link to a Studio section (Help,
  Updates) no longer leaves the heading under the tab row.

## 0.1.13

**Upgrade note:** install this Android app version by hand one last time (from the release, as before). From then on the app
can update itself from Settings, App version.

- **Update check and in-app update.** The server checks once a day for a new release (one plain request to GitHub; turn it
  off in Studio, Help, or with `ET_UPDATE_CHECK=off`), and Studio tells the owner when one is out. The Android app can update
  itself from Settings, App version: it downloads the new APK from the release, checks its checksum and that it's signed with
  the same key and newer, then hands it to Android's installer. Each release now includes a `latest.json` manifest.
- **Hearing about updates.** The testing guide and the Android guide explain how to get an email for each release
  and how to let Obtainium install new Android app versions from the GitHub releases.

## 0.1.12

**Upgrade note:** the half-degree °F option is gone; anyone who used it sees whole degrees, and a target saved at a half degree shows rounded.

- **Easier first connection in the Android app.** The server address can be typed as it is, for example `192.168.1.20:8790`:
  for a home address the app tries `https://`, then plain `http://`, then port 8790 if the port was left out. The field
  no longer starts with `https://`, which a plain home server doesn't answer.
- **Setup is easier to find on a computer.** Studio shows a "Finish setting up your side" banner with a Start setup
  button until the short setup is done. The welcome card on a computer no longer repeats the text beside it.
- **For testers.** A beta tester feedback form and a link to Discussions for questions; install guide fixes (the broker
  on Portainer, Unraid, TrueNAS and CasaOS, Tailscale Serve with Docker, a live backup with nothing extra installed,
  the cloud option without Docker, the Home Assistant line).
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
