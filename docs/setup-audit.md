# Setup audit: first run as a stranger

Written while making the app locale-neutral. What a new household meets, what was fixed, and what is still open.

## What was fixed

- **AI prompts** no longer say "°F unless they use °C": the person's unit, clock style and language are named in the prompt and
  the context carries temperatures already written in that unit (raw `*_c` values stay for proposals and tool calls). No
  place, country, holiday or work-week wording.
- **Season** words in night reviews come from the household time zone (southern zones flip, zones near the equator get the
  month only) with an override (`hemisphere`, Studio, Your settings).
- **Server text** (insights, suggestions, morning and night stories, notifications, pet questions, the dial's schedule list,
  pad-lag note) follows each person's units and the new `clock` setting instead of hard-coded °F and AM/PM.
- **Time zone**: `ET_TZ`, then `TZ`, then the machine's zone, then UTC; a bad name is skipped. The example Compose file no longer
  ships a US zone (`Etc/UTC` with a "change me" comment). Studio, Your settings shows the zone in use and where it came from.
- **New people** start on the browser region's unit and the device's 12/24 h style (first-time setup); existing people keep
  what they saved.
- `.env.example` lists every variable, commented; `docs/configuration.md` has the new settings; install examples use a neutral zone.
- New settings: person `clock` (auto/12/24), person `language` (BCP-47 or auto), household `hemisphere` (auto/north/south/none).

## Still open

| What | Where | Suggested fix |
|---|---|---|
| The ESPHome dial shows a fixed "°F" label (and "Bed 70°" is whatever number it is sent) | `core/embertide/dial/embertide-dial-common.yaml` lines ~566 and ~965 | The bridge already sends numbers in each person's unit. Publish a small text sensor with the unit ("°F"/"°C") per person (`ha_bridge.py`) and have the dial read it; needs a firmware rebuild, so not done here. |
| Wear OS app formats times with `Locale.US` "h:mm a" and defaults to °F when units are missing | `web/android/wear/src/main/java/app/embertide/wear/WatchLogic.kt` (~line 82), `WearApi.kt` | Use the system 12/24 h setting (`DateFormat.is24HourFormat`) or the person's `clock`; the server now sends `clock` in settings. Needs a watch build and test. |
| Android widget/tile default to °F when settings are absent; they format numbers with `Locale.US` | `Ember.java` ~170, `EmberWidget.java` | Default to the synced `units`; keep `Locale.US` only for JSON bodies. |
| Weeks and work days assume Monday to Friday | `PersonSettings.work_days`, `seed.py` (weeknights Mon-Thu + Sun, weekends Fri-Sat) | Offer "which days do you get up early" in first-time setup with the locale's week start; seed from it. |
| The seeded rhythms use °F starting temperatures converted to °C (so °C users get 22.2, 23.9, ...) | `seed.py` `PERSONA_TEMPS_F` | Store the persona table in °C, rounded to 0.5. |
| Screens, buttons, notification wording and voice variants are English only | `web/src`, `core/embertide/voice.py` | Full translation (i18n catalog) is out of scope here. Only the assistant, Ask and reviews follow the `language` setting. |
| Web `auto` clock follows the browser, the server's `auto` follows the unit | `format.ts` `setClockPref`, `units.py` `clock_style` | First-time setup now saves an explicit 12/24 h choice, so the two agree for new people; older people on "auto" may see both styles. Consider saving the choice for everyone on first sign-in. |
| Date labels in server text use English weekday and month names (`%a`, `%B`) | `engine/summary.py`, `insights.py`, `notify.py` | Part of the translation work above. |
| Demo names "Biscuit" and "Rex" in docs and tests | docs/notifications.md, tests | Generic placeholders (see `docs/PUBLISHING.md`); fine. |
