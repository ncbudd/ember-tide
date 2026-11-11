# Changelog

All notable changes. The project follows [Semantic Versioning](https://semver.org/) loosely while it is 0.x: anything may change between
minor versions, and each entry says what to check when upgrading.

## 0.1.0 (first public release)

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
- Android app with widget, Quick Settings tile, Health Connect and phone sources; Wear OS tile.
- Home Assistant over MQTT discovery; ESPHome bedside dial package.
- Docker image, Compose example with a pinned Mosquitto and an access list, and install, bed cutover, Android, configuration and
  troubleshooting guides.

### Known limits
- No signed Android release build yet; build the APK yourself.
- No native iPhone app yet; the Home Screen web app has no Apple Health or background location.
- Only the Orion Sleep topper is supported.
