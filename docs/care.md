# Care and maintenance

A short list of jobs for the bed, with a reminder when one is due. It lives on Studio → Bed hub (full cards), in the
phone's Settings → Care (a compact list), and as one calm line on Tonight when something is due.

## How it works

- The bed's hardware profile (`core/embertide/drivers/profiles.py`) supplies the default jobs and their wording. The
  generic code (`care.py`, `care_api.py`) knows nothing about any maker. A driver says which profile it wears with
  `profile = ...`; the simulator wears a small generic one.
- Each job has a title, a short "why", ordered steps, an optional supplies note, an interval in days (the household can
  change it), and a source label: "From the maker's instructions" or "Suggested default" (or "Added by you").
- Due dates count from "last done". A brand-new install counts from the first day the list is opened, so it asks
  nothing for a whole interval unless the owner enters "I last did this on…". That date can be set in Studio or by the
  API (`PUT /api/care/<id> {"last_done": "YYYY-MM-DD"}`); nothing personal is stored in the code.
- State (interval, on/off, last done, snooze) is kept per household in the store (kv `care`) and survives restarts.
- "Add water" has no schedule. It shows as due whenever the hub says its water needs a look (the same status the hub
  notification already uses), with the maker's steps, and it never sends a second, separate message.
- Wording of status: "Due in 12 days", "Due tomorrow", "Due today", "3 days overdue"; water: "Needs water now" / "Fine for now".

## Who can do what

| | owner | sleeper | view-only |
|---|---|---|---|
| read the list (`GET /api/care`) | yes | yes | yes |
| mark done (`POST /api/care/<id>/done {on?}`), put off (`POST /api/care/<id>/snooze {days?}`, default 7) | yes | yes | no |
| change interval, turn on/off, set last done (`PUT /api/care/<id>`), add (`POST /api/care`), delete your own (`DELETE /api/care/<id>`) | yes | no | no |

A sleeper can already work other household-level things (swap sides, a guest's side), and "I just added the tablets" is
the same kind of thing, so they may mark a job done or put it off; changing the schedule stays with the owner.

## The notification (`maintenance`)

One calm message to each owner or sleeper who has their own "Care reminders" switch on (not view-only accounts, not
anyone marked away) when a job becomes due ("Time to add purification tablets", one line of why, buttons It's done and
Remind me next week). In the Android app the buttons work from the notification with the phone's device key
(`POST /api/care/<id>/done` and `/snooze`, the only care calls a phone key may make; the account's role still applies,
so a view-only key can't) and the notification becomes "Marked done. Next reminder in about 6 months." or "I'll remind
you next week." When one person acts, the job is done or snoozed for everyone, so no follow-up goes to anyone; the other
person's notification is not recalled (there is no server-to-phone clear), and a stale tap is harmless. Daytime only (9 AM to 7 PM, household time), never while anyone's night is running or
inside anyone's quiet hours, at most one maintenance message a day (a second due job waits for tomorrow, and the
message says how many others are due), one follow-up after 7 days if still not done, then silence until it is marked
done or turned back on. Remind me next week puts the job off for 7 days. See docs/notifications.md.

## Home Assistant

With the MQTT bridge, each job gets `sensor.embertide_care_<id>_days` (days until due, negative when overdue; a water job
shows 0 while the hub says it needs water) and `button.embertide_care_<id>_done`. Jobs you add later appear the next
time the bridge announces (a restart or Home Assistant coming back).

## Default jobs and where they come from

Orion profile. Checked against the maker's help centre, November 2026.

| Job | Interval | Source |
|---|---|---|
| Purification tablets: add 2 (the maker calls them cleaning tablets), not at every top-up; also add 2 after any drain and refill | 180 days. The maker says every 6 to 8 months | Maker: https://help.orionsleep.com/en/articles/13232796-filling-and-priming-your-orion-system and https://help.orionsleep.com/en/articles/16443047-draining-and-moving-your-orion |
| Add water: distilled water only, only when the hub asks, slowly, up to the bottom of the funnel opening, then follow the prompts (priming 5 to 10 minutes) | when the hub says the water is low | Maker: https://help.orionsleep.com/en/articles/13232796-filling-and-priming-your-orion-system and https://help.orionsleep.com/en/articles/14743059-my-orion-is-stuck-in-needs-water-mode |
| Check the hub, hose and vents: wipe and dust the vents, 3 to 5 inches from the wall, hose clicked in at both ends with slack, look for water on the floor | 90 days (suggested). The maker lists these checks but gives no schedule | Maker for the checks: https://help.orionsleep.com/en/articles/16418436-noise-and-vibration and https://help.orionsleep.com/en/articles/15527057-device-not-cooling-or-heating-adequately. The interval is our suggestion, in line with other water-based beds that ask for a monthly to three-monthly wipe and check |

Not given by the maker, so not shipped as jobs: a regular water change (the maker says none is needed in normal use),
washing the Smart Cover, and a leak-check schedule. Draining is only for long storage or a long-distance move, and for
a long trip away the maker points to Away mode rather than draining.

Other water-based beds, for comparison only: Sleepme asks for a cleaner packet monthly and a drain, wash and dry about
twice a year; Eight Sleep asks for an occasional refill and, on newer models, a maintenance insert about every 6 months.

Generic profile (simulator): Add water (hub says), Check the hub, hoses and vents (90 days), Treat or refresh the water
(180 days), all suggested defaults.

## The hub's light colours

The Orion profile also carries the maker's own guide to the hub's light colours, shown folded on the Studio Bed hub
status card and the phone's hub card (blue pairing, orange powering on, red updating, purple an error, white online,
gently pulsing white low water, no light: check the brightness). A colour chosen by hand from the same page is not a
status, and the note says so.
