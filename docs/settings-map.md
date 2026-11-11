# Settings map

Every stored setting, where people see and change it, and who may change it. Checked 2026-11-08.

Places: **Phone** = the phone app's Settings (More tab; Simple app = its Me tab), **Me** = Studio → Your settings,
**Setup** = first-time setup on the phone, other Studio pages by name. "Person" = that person, plus the owner (the
owner can pick the other person in Studio → Your settings). "Owner" = owner only. With sign-in off everyone is owner.
Phone Settings and Studio → Your settings share their controls (components/PersonPrefs.tsx, FlashPrefs.tsx,
screens/SettingsSleep.tsx) so the two always use the same words and limits.

## Person (PersonSettings, models.py)

| Setting | Where | Who |
|---|---|---|
| name, persona (Run hot · Tide / Run cold · Ember / In between) | Phone, Me, Setup | Person |
| side of the bed (Person.zone), swap sides | Phone, Me, Setup, Studio → People & access | Person (swap: either; owner) |
| `units` (°F/°C) | Phone (Tonight screen), Me (Units), Setup, Simple app → Me | Person |
| `weight_units` (auto/lb/kg) | Phone (Tonight screen → Weights), Me (Units), Setup | Person |
| `color` | Phone (Appearance), Me (You), Simple app → Me | Person |
| `app_mode` (Full/Simple app) | Phone, Me (App), Setup, Studio → People & access, Simple app → Me ("Switch to the full app") | Person, owner |
| `welcomed`, `setup_steps` | Phone → "Run first-time setup again" (resets both); Setup sets them | Person |
| `default_view` (Tonight opens on) | Phone (Tonight screen), Me (App) | Person |
| `simple_default`, `simple_at_night`, `simple_red` (night screen) | Phone (Tonight screen), Me (App, Full app only) | Person |
| `appearance`, `light_from`, `dark_from` | Phone (Appearance), Me (App); Simple app → Me (Automatic/Light/Dark only) | Person |
| `hot_flash`, `cold_flash`: target or "cooler/warmer by" (`relative`, `delta_c`), lasts, ease back, extra press, never longer than (`max_total_min`) | Phone (Flash relief), Me (Hot and cold flashes) | Person |
| `partner_can_flash`, `partner_can_adjust` | Phone (Sharing), Me (Sharing) | Person |
| `notify.morning / away_precool / empty_side / left_on / not_started / pet_question / problems / hub / maintenance / quiet_hours` | Phone (Notifications), Me (Notifications), both via components/NotifyPrefs.tsx; Setup (some); docs/notifications.md | Person |
| `tone` (calm / friendly / cheeky wording, voice.py) | Phone (Notifications), Me (Notifications), with a preview | Person |
| `pet_questions` (ask at all) | Phone (Sleep data), Me (Sleep data and Autopilot) | Person |
| `work_days` | Phone (Work days), Me (Your week), Setup | Person |
| `wearable` (setup's brand pick) | Phone (Watch or ring, browser/iPhone), Setup; Me shows it | Person |
| `in_bed_after_min`, `up_after_min` | Phone (Sleep data), Me (Sleep data and Autopilot). Overrides each rhythm's "In bed this long before starting"; the rhythm editor says so | Person |
| `night_colors` | Legacy, unused: kept in the model so old saves load, shown nowhere | — |

## Autopilot (AutopilotPolicy, per person)

| Setting | Where | Who |
|---|---|---|
| `mode` | Phone, Me, Studio → Autopilot, Setup | Person |
| `suggest_cadence`, `suggest_weekday` | Phone, Me (cadence), Studio → Autopilot | Person |
| `max_delta_c`, `floor_c`, `ceiling_c`, `max_actions`, `phases`, `quiet_before_wake_min` | Phone (Autopilot), Studio → Autopilot | Person |
| `min_gap_min`, `respect_manual_min`, `sensitivity`, `signals`, `trust` (per source), `suggest_manual`, `ai_review` | Studio → Autopilot | Person |
| `learn_nights` | Studio → Autopilot | Person |
| `learn_skip` | Phone ("Start now"), Studio → Autopilot | Person |
| `learn_since` | Set by "Recalibrate" (Phone, Studio → Autopilot); not edited directly | Person |
| `trust_more` | Phone (Sleep data), Me, Setup | Person |

## Rhythms (Rhythm, Schedule, Phase; per person)

| Setting | Where | Who |
|---|---|---|
| phases (temperature, start anchor, offset, change over, Autopilot may adjust) | Studio → Rhythm editor (all); Phone Rhythm tab (temperatures, anchors, change over); Simple app → Schedule (overall warmer/cooler) | Person |
| `bedtime`, `wake`, `window_start`, `window_end` | Phone Rhythm tab, Rhythm editor, Simple app → Schedule (bedtime/wake), Setup | Person |
| `precool_lead_min`, `start_on_presence` | Phone Rhythm tab, Rhythm editor | Person |
| `wait_for_sleep` ("Wait until I'm settling to sleep", default on; shown when `start_on_presence` is on) | Phone Rhythm tab, Rhythm editor | Person |
| `confirm_min`, `after_wake_min`, `absent_hold_min` | Rhythm editor | Person |
| `days`, `nights` (follow work days) | Rhythm editor; follows `work_days` | Person |

## Household (Household, models.py)

| Setting | Where | Who |
|---|---|---|
| `pets` (name, kind, weight, size, sleeps on bed, side) | Phone (Pets on the bed), Studio → People & access (Pets), Setup | Owner |
| `pets_ignore` | Same pets editor ("Ignore readings that look like a pet") | Owner |
| `guest` (GuestSide: on, temperature, turn off after they get up, ends, dial screen) | Studio → Guest mode (all); Phone (Guest mode: side, temperature, turn-off; end); Tonight guest card (on/off, temperature) | Owner (guest card: whoever can see it) |
| bed orientation (`bed_from_foot`, kv) | Studio → People & access (Sides of the bed), Phone (My side of the bed) | Owner |
| `room_c_default`, `bed_min_c`, `bed_max_c`, `user_min_c`, `user_max_c` | Not settings: fixed safety limits of the bed (room default is unused) | — |

## Devices and calibration (sources.py)

| Setting | Where | Who |
|---|---|---|
| each device: name, kind, who it's for, side, connection (`params`), enabled (Pause) | Studio → Devices (Edit on each row; one add flow) | Owner |
| each device type's tuning (vote weight, hold/min-on seconds, invert, value map, forget after, thresholds, offsets, radius, ready-before, travel time, "trust it even when it acts up") | Studio → Devices → Edit → "How it's read" | Owner |
| Calibration: vote weights per signal | Studio → Devices → "In-bed vote" | Owner |
| Calibration: scale zero (`tare_kg`), body weight (`person_kg`), learned empty/sleeper weight | Studio → Devices → Calibration (Zero, body weight); learned ones are automatic | Owner |
| dial sides, hidden dials, dial beacons | Studio → Integrations → Bedside dials | Owner |
| this phone's sharing switches (location, Wi-Fi, calendar, alarm, charging, watch, dial, watch heart rate) | Phone (From this phone, Android app only); Me shows them read-only | Person, on their phone |
| Health Connect types | Phone (Watch or ring, Android app) | Person, on their phone |
| keyboard layout (compact) | Phone (Appearance); kept on that phone only | That phone |

## Changes made in this pass

- Studio → Your settings gained hot/cold flash settings and the night-screen switches (were phone-only).
- Flashes: "cooler/warmer by" (`relative`, `delta_c`) and "never longer than" (`max_total_min`) had no control
  anywhere; both are now in the shared flash block.
- Bed orientation can be set from the phone too (owner), next to the side picker.
- Phone and Studio used different words for the same thing (persona, light/dark); both now share one control.
- `night_colors` removed from the web types (still accepted by the server for old saves).
- Rhythm editor: each schedule field keeps to the server's limits (it allowed 720 min) and "In bed this long
  before starting" says when the personal setting overrides it.
