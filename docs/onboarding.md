# First-time setup: what's asked, what's defaulted, what waits

The phone app's first sign-in (`web/src/screens/Welcome.tsx`) is a short guided setup: one decision per screen,
every step skippable, everything changeable later in Settings. The aim: someone who only ever uses the phone app is
fully set up without opening Studio. Steps already done (`PersonSettings.setup_steps`) are skipped next time;
Settings → "Run first-time setup again" brings them all back.

## Settings audit

"Onboarding" = asked in setup. "Default" = sensible value, no question. "Later" = Settings or Studio only.

| Setting | Decision | Why |
|---|---|---|
| Name | Onboarding (This is you) | Shown everywhere, on the dials and in Home Assistant; "Left"/"Right" is a placeholder. |
| Side / zone | Onboarding, confirm only | Shown with a bed picture; changing it is "Swap sides" (affects both people), so it stays in Settings. |
| Colour | Onboarding (This is you) | One tap, makes the chart and dials readable at a glance. |
| Units °F / °C | Onboarding (This is you) | Every temperature on every screen depends on it. |
| Appearance / theme | Default (follow the phone) | The phone's own light/dark setting is the right answer for almost everyone. |
| App style Simple / Full | Onboarding | Changes the whole app; the single biggest comfort choice. |
| Comfort start points | Onboarding (Ember / Tide / in between) | Sets the persona and, when it changes, the rhythm's starting temperatures. Fine-tuning is learned. |
| Work days | Onboarding | Decides which rhythm runs which night (weekend workers). |
| Bedtime / wake, work nights and days off | Onboarding | Pre-cool and the warm wake hang off these. The "night starts" window follows the bedtime. |
| Pre-cool lead, warm wake length, phase temperatures | Default | Good defaults; tuning guide and Autopilot suggest changes from real nights. |
| Autopilot mode | Onboarding (default Suggest) | Trust question: off / suggest / auto, with the 7-night learning explained. |
| Autopilot limits, phases, cadence | Default, later | Safe defaults; power-user detail. |
| Notifications per type | Onboarding | Four plain toggles, plus turning notifications on for this phone (Android native, iPhone Home Screen guide, browser Web Push). |
| Morning check-in ("How did it feel?") | Onboarding (in Notifications) | It's the "morning" notification; on by default because one tap helps Autopilot most. |
| Phone sources (location, Wi-Fi, calendar, alarm, charging, watch, dial) | Onboarding, Android app only | Each asks its own permission; iPhone shows an honest "can't / use instead" card. |
| Watch or ring | Onboarding (brand tiles) | Brand + platform specific how-to with the connect button right there; "Do it later". |
| Health Connect data types | Default (night group on) | Already sensible; detail in Settings → Watch or ring. |
| Pets | Onboarding (owner edits; others see what's there) | Household-level: shown as already set up so nobody adds the same pet twice; skipped for a non-owner when there are none. |
| Sharing with partner (flash / adjust) | Default (flash yes, adjust no) | A relationship choice better made together later; summary links to Settings. |
| Calendar link (ICS) | Later (owner, Studio → Devices); shown as done | Needs the owner's device setup; setup lists it under "Already set up in this home" when present. |
| Bedside dial pairing | Later; shown as done | Each dial is set to its side in its firmware; nothing per person to ask. |
| Context devices, radar, scale, room sensors | Later (owner, Studio); shown as done | Household hardware; counted under "Already set up in this home". |
| Guest mode | Later | Only matters when someone visits. |
| Watch app / tile (Wear OS) | Later | Installed separately; signs in by itself from the phone. |
| Sign-in / security, invites | Before setup (sign-in page) / later | Handled by the invite and sign-in screens; Account in Settings. |
| Oura app credentials | Later (owner, Studio → Integrations) | Developer registration, once per home. |

## Watches and rings: which path gives what

Kept in code as `web/src/wearables.ts` (the UI reads it). "Yes" = reliably written, "some" = depends on model,
app version or a setting, "no" = not written. Brands change what they share; the UI says "usually".

| Brand | Android: Health Connect | iPhone: Apple Health (iPhone app) | Direct cloud |
|---|---|---|---|
| Oura | stages, HR yes; HRV, temperature some; breathing no | stages, HR, HRV, breathing yes; temperature some | stages, HR, HRV, temperature deviation, readiness (this repo's `oura` driver) |
| Apple Watch | not available | stages, HR, HRV, wrist temperature (Series 8+), breathing | none |
| Pixel Watch / Fitbit | stages, HR, HRV, breathing yes; temperature some | not available | none |
| Samsung Galaxy Watch | stages, HR yes; HRV, temperature, breathing some | not available | none |
| Garmin | stages, HR yes; breathing some; HRV, temperature no | HR yes; stages, breathing some; HRV, temperature no | none |
| Whoop | HR yes; stages, temperature, breathing some; HRV no | HR yes; stages, breathing some; HRV, temperature no | none |
| Withings | HR yes; stages, temperature, breathing some; HRV no | HR yes; stages, temperature, breathing some; HRV no | none |

Paths: Android reads Health Connect in the Android app (`HealthSync.kt`). iPhone will read Apple Health in the
native iPhone app (`web/ios-native/App/HealthSync.swift`, same `/wearable` and `/stages` calls, `platform: "ios"`).
Oura's cloud is the fallback for anything a phone's health store doesn't carry and for the iPhone web app. Readings
that arrive more than one way are stored once (same metric, same second, same value).

## iPhone web app (PWA) facts used in the UI

- Notifications: iOS 16.4+, only for a site added to the Home Screen and opened from its icon. Setup shows the
  three-step Share → Add to Home Screen → open-from-icon guide in plain Safari, and the permission button only in
  the installed app.
- Not possible from a web app: Apple Health, background location, Bluetooth scanning (bedside dial). Instead: the
  bed's own sensors, Oura via the cloud, a calendar feed added by the owner.
