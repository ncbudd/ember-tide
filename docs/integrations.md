# Cloud accounts and other ways in

Ember & Tide reads wearables three ways: the phone's health store (Health Connect on Android, Apple Health in the
iPhone app), a person's own **cloud account** at the maker, and two **generic** paths for anything else. Everything
lands in the same wearable store (`wear:<person>:<metric>:<device>` samples, plus sleep stages) through the same
import, so clock alignment and the cross-device duplicate rule (same metric, second and value = stored once) apply
whichever way a reading arrives.

Voice assistants (Siri, Google Assistant, Alexa, Home Assistant's Assist) are the other direction, controlling the bed:
see [Talk to your bed](voice.md).

## Cloud accounts (`core/embertide/cloud/`)

One module per provider on a shared base (`cloud/base.py`): OAuth2 authorization code with a single-use state,
tokens kept on the server and refreshed (single-use refresh tokens: the newest is kept), polling once after the
person wakes and then hourly (never during the night), Retry-After backoff on 429, and a "connect again" state after
a dead sign-in. The owner enters each provider's client id and secret once in Studio → Integrations → Cloud
accounts; each person connects their own account from the phone (Settings → Watch or ring, or first-time setup).

API: `GET /api/cloud/providers` (everyone; configured/connected per person, plus the not-available list),
`GET|PUT /api/integrations/cloud/<id>` (owner credentials), `GET /api/cloud/me/<id>`,
`POST /api/cloud/me/<id>/connect|sync|disconnect`, redirect `GET /api/cloud/<id>/callback` (Oura keeps
`/api/oura/callback`).

Status as researched in November 2026 (check each developer site before relying on it):

| Provider | Data we get | Auth | Approval needed | Latency | Built |
|---|---|---|---|---|---|
| Oura (API v2) | sleep stages (5-min), heart rate through the night, HRV, temperature deviation, readiness | OAuth2 (personal tokens retired Dec 2025); body client auth; scopes `daily heartrate` | No; up to 10 users per app before Oura's review | after the ring syncs to the Oura app (usually on waking) | yes |
| Withings (Public API) | sleep states (awake/light/deep/REM), heart rate and breathing rate per minute (Sleep Analyzer, ScanWatch), weight | OAuth2; token call `action=requesttoken`, wrapped answers, errors as HTTP 200 + status; scopes `user.metrics,user.activity,user.sleepevents` | No (free developer account; some advanced sleep metrics are US-only or plan-dependent) | minutes to an hour after the device syncs | yes |
| WHOOP (Developer API v2) | per-night resting heart rate, HRV (RMSSD), skin temperature, SpO2, breathing rate, recovery score; no minute data or stage timeline | OAuth2; `offline read:sleep read:recovery` | No (any member can create an app) | after the strap syncs and WHOOP scores the sleep | yes |
| Polar (AccessLink v3) | sleep hypnogram and heart-rate samples, Nightly Recharge HRV / breathing / heart rate | OAuth2 with HTTP Basic client auth; no refresh (long-lived tokens); user registered once after connecting | No (any Polar Flow user can register a client); last 28 days only | after Polar Flow sync | yes |
| Fitbit / Pixel Watch | — | Fitbit Web API switched off Nov 2026; Google Health API uses Google OAuth | Yes: Google's app verification for health scopes | — | no (use Health Connect) |
| Garmin (Health API) | — | OAuth | Yes: business partner programme only | — | no (Health Connect / Apple Health) |
| Samsung Galaxy Watch | — | no public cloud API | — | — | no (Health Connect) |
| Apple Watch / Apple Health | — | no cloud API; on-device HealthKit only | — | — | no (iPhone app) |

## Generic paths (`cloud/generic.py`, owner only, Studio → Integrations)

1. **Inbound webhook.** Each one has a unique secret address `POST /api/hooks/<id>/<secret>` (no sign-in: the
   address is the key; 256 KB max). JSON body, mapped by `items_path` (where a list of readings is; empty = the body
   is one reading or a list), `value_path`, `time_path` (epoch s/ms or ISO; empty = arrival time), unit (°F for
   temperatures) and a multiplier. Stored under the chosen person and metric.
2. **Poll a JSON address.** The server GETs a URL every N minutes (5–1440) with optional headers (an API key; stored
   on the server, masked in Studio, kept when an edit sends the mask back), same mapping. A 429 backs off 30 min.

Paths: dotted with list indexes, e.g. `data.readings`, `$.items[0].bpm`. Metrics: `hr`, `hrv`, `skin_temp`,
`body_temp`, `resp_rate`, `spo2`, `resting_hr`, `temp_dev`, `readiness`, `weight_kg`, `steps`, `active_kcal`,
`exercise_min`, `caffeine_mg`.

## Adding a provider

Subclass `CloudConnector` in `cloud/<id>.py`: set `id`, `name`, `device`, `auth_url`, `token_url`, `scopes`
(`scope_sep`, `token_auth`, `refreshes` if they differ), `gives`, `docs_url`; implement
`fetch(pid, start, end, now) -> CloudData` (use `self.request()` for calls: it refreshes on a 401 and turns 429/403
into backoff and messages); add it to `PROVIDERS` in `cloud/__init__.py`, the brand table in `web/src/wearables.ts`
(`cloudId`), and a fake-service test in `tests/test_cloud.py`.
