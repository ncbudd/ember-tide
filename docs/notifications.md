# Notifications and questions · inventory and rules

Everything Ember & Tide can send or ask, who gets it, when, how often, and how it's worded. Code: `core/embertide/notify.py`
(triggers, caps, quiet hours, delivery), `core/embertide/voice.py` (wording and tones). Lab: `core/tests/notify_week.py`
(a normal and a messy week on the simulator), budgets in `core/tests/test_notify_week.py`.

## Channels

One message goes out on **one** channel per person: the app first (FCM, UnifiedPush, or Web Push for the browser / iPhone
Home Screen app), and that person's ntfy / Home Assistant targets only if no phone took it. Household messages (bed
trouble) follow the same rule: the phones of people who want them (`problems` on), and the household ntfy / HA target
only as the fallback. Before 2026-11-08 a household message went to the household target **and** every phone.

## Inventory

Pushes (from the server):

| Type (setting) | Trigger | Who | When | Repeats at most | Buttons |
|---|---|---|---|---|---|
| Morning summary (`morning`) | the night is done, its end time passed (up to 4 h after) and there was a night in bed | the sleeper | after wake | 1 a day | none; tap opens the app to check in |
| Pre-condition, not home (`away_precool`) | pre-condition time while the person is away (phone / GPS / manual) | the sleeper | evening, at pre-condition | 1 a night | Wait an hour · Off tonight · Start anyway |
| Pre-conditioned but empty (`empty_side`) | the side waited its whole window and nobody got in (state "missed") | the sleeper | evening; after midnight held for the morning | 1 a night | Off tonight |
| Left on by hand (`left_on`) | turned on by hand (nap, timer), nobody in it for 20 min | the sleeper | any time; after midnight held | 1 per session, 2 a day | Turn it off |
| Side hasn't started (`not_started`) | lying in it ~40 min, bed radar still not backing it, no pet suspected, not away | the sleeper | any time except the last hour before wake and after it; not held by quiet hours | 1 a night | Start now |
| Pet question (`pet_question`, and `pet_questions` for asking at all) | something lay on the side and looked like a pet, or lay there with no night running | that side's sleeper | 8 AM–9 PM; a question from the night rides in the morning summary | 1 per question, 1 a day | none; tap opens the question |
| Can't reach the bed (`problems`) | no word from the bed for 5 min | household | at once in the day; held while anyone sleeps | 1 per outage, 3 household problems a day | none |
| Bed sensor stopped updating (`problems`) | one side's readings frozen 10 min during a night (not while the whole bed is offline) | household | as above | 1 a night per side | none |
| Bed hub needs a look (`hub`) | the hub reports a fault, or the water needs checking, or its safety check fails | owner's phones | at once in the day; held in quiet hours (morning summary / "While you slept"), except a failing safety check while a night runs | 1 per problem per day, 3 a day | none |
| Care reminder (`maintenance`) | a care job (purification tablets, a hub check) is due; low water is not here, the hub message covers it | owner and sleeper phones with the switch on (not away, not view-only) | 9 AM to 7 PM only; never while anyone's night runs or in anyone's quiet hours (skipped, not held) | 1 a day in all; one follow-up after 7 days, then silence until done or turned back on | It's done · Remind me next week (7 days) |
| Away check (`away_check`, no switch of its own) | the day after a night they clearly weren't in their side: "Were you away?"; or marked away but their ring shows them asleep in their side: "Welcome back?" | the person it is about | 7 AM to 9 PM, checked every 10 min; held like other messages while they're in bed | 1 a night per person (2 a day cap) | Away a few days · Just that night · I was home; or Yes, I'm back · Still away |
| Bed ignoring changes (`problems`) | the bed is online but one side hasn't confirmed the last changes for 20 min | household (phones with `problems` on) | as bed trouble above | 1 a night per side | none |
| Side isn't updating (`problems`) | one person's update has failed ~2 min of ticks in a row | household | as bed trouble above | 1 a night per person | none |
| Account signed out (`account`) | a connected account (Oura, Withings, WHOOP, Polar) was refused and needs signing in again | the person whose account it is | 9 AM to 7 PM only, never while anyone's night runs or in anyone's quiet hours (skipped, not held) | 1 a day, 4 reminders, then quiet until fixed (starts over if it signs out again) | none; the message says where (Settings), a web tap opens Settings |
| A phone stopped getting notifications (`reach`) | 3 messages in a row reached a person on no channel at all (every phone and target failed); a new phone clears it | owner phones, not the person who can't be reached (the household target if no one else) | as above | 1 a day per person, 4 reminders | none; a web tap opens Studio, Notifications |
| Home Assistant link down (`bridge`) | the Home Assistant link is set up and hasn't been connected for 3 hours | owner phones | as above | 1 a day, 4 reminders | none; a web tap opens Studio, Integrations |
| Server nearly out of space (`storage`) | under 1 GB free, or under 5% of the disk, where the data is kept | owner phones | as above | 1 a day, 4 reminders | none; a web tap opens Studio, Data |
| Watch or ring quiet (`wearable`) | a watch, ring or cloud account supplied night heart rate in the last 14 days, and the last two nights in bed (in a row, not away; the newest ended 5 h ago, so the phone had time to sync) have none from any wearable | that person | 9 AM to 7 PM only, never while anyone's night runs or in quiet hours (skipped, not held); looked at hourly | 1 a day, 4 reminders, then quiet until a night comes in | none; says "charged and worn, then open the app"; a web tap opens Settings, Watch or ring |
| Bed's sensors silent all night (`bed_silent`) | a night ran for a person with no usable presence reading from the bed or any other sensor for 90% or more of at least 3 h running (time the bed was offline does not count as silent) | that sleeper (each side that ran blind) | after wake, once the night is done | 1 a night per person, 4 nights in a row, then quiet until a night with working sensors | none; a web tap opens Studio, Hub (owner) or Settings, Bed hub |
| Presence sensor flipping (`sensor`) | a presence sensor was set aside as erratic for flipping in and out, and has been for 2 h; not for a paused, removed or "trust it" sensor | owner phones | 9 AM to 7 PM only, skipped not held | 1 a day per sensor, 4 reminders | none; a web tap opens Studio, Devices |
| Presence sensor stuck (`sensor`, same switch) | a radar or motion sensor has said "someone is here" without a break for over 24 h while the bed's own strip showed that side empty for 4 h or more in those 24 h; never for a bed scale or pad (they hold what lies on them), custom, paused, removed or "trust it" sensors | owner phones | as above | 1 a week per sensor, 4 reminders | none; a web tap opens Studio, Devices |
| Side not reaching its temperature (`target`) | night running, target steady (within 0.3 °C) for 40 min, pad more than 4 °F (2.2 °C) from the setpoint for those 40 min and not 0.3 °C closer than 20 min ago; hub online, confirming commands, no hub fault or low water; no flash; not a cold room with a warm target (or warm room, cool target) | the sleeper and owners | held for the morning (summary or "While you slept"); more than 9 °F (5 °C) off for over an hour also goes at once to owners only (the sleeper if no owner is linked) | 1 a night per side, plus 1 at-once escalation; 4 nights, then quiet until a night without it | none; a web tap opens Studio, Hub (owner) or Settings, Bed hub |
| While you slept (digest) | something was held overnight and no morning summary is coming for this person | each person, once | after wake | 1 a day | none |
| Button done (`action_done`) | a ntfy / HA / confirm-page button was used | the person | right after | not capped (asked for) | none |
| Test | Test button in Studio or Settings | that target | on demand | not capped | none |

In the app or on devices, never pushed (listed so nothing is missed):

| What | Where | Wording |
|---|---|---|
| "How does it feel?" check-in | Tonight (any time), keyboard F 1/2/3, bedside dial "How does it feel" page, HA `sensor.<p>_checkin` = due for 5 h after the night | in-app: "How does it feel right now?"; Sleep screen: `checkin_app` variants |
| Morning check-in | Sleep screen card ("How was the temperature?" + rested 1–5 + note) | `checkin_app` variants per tone |
| Autopilot suggestions | Sleep screen "Suggestion" card, Studio Autopilot; at most one per cadence period (day / week / month), "Not now" rests it 14 nights | `manual_*` and `pattern_*` variants |
| Morning AI review | Studio / Sleep page, runs in the background after the night | the model's own words |
| Tuning guide (late events, bedtime drift, lights) | Tonight's Today card, Studio | plain, fixed |
| Guest mode | nothing is sent about a guest or the guest's side | n/a |
| Flash on / ending | Android ongoing flash notification (local, not a push); HA `sensor.<p>_flash_end`; Simple screen countdown | phone: "Hot flash · then back to the rhythm"; `flash_end` variants for the app |
| "Starting your night" | not a notification: the story line "In bed · night started" | fixed |
| Night story lines | Sleep screen "The night" | pet and late-start lines vary (`story_pet`, `story_pet_with`, `story_fitted`); "Active, not sleeping yet" stays neutral in every tone; the cold-room note ("The room is cold (65°), so your side is running a little below 80°") stays plain |
| Power cut resumed | story / events; one notification per bed outage at most (the offline alert above) | plain |

## Problem → notification map (read from the code, 2026-11-11)

One row per situation a person may need to know about or act on. "Held" means quiet hours keep it for the morning summary or
the "While you slept" note; "skipped" means it is dropped, not delayed. Every row is simulated end to end in
`core/tests/test_sim_notify_*.py` (clock, fake providers; see "Simulations" below).

| Situation | Kind | Who | When it fires | Buttons (what each does) | Repeats | Quiet hours |
|---|---|---|---|---|---|---|
| Away at pre-condition time | `away_precool` | the sleeper | at pre-condition time, state is "away" (not marked away by hand) | Wait an hour (pause 60 min) · Off tonight (skip) · Start anyway (start now) | once a night | sent in the evening; held from midnight to wake, and dropped (not told late) |
| Pre-conditioned but nobody got in | `empty_side` | the sleeper | the side waited its whole window and nobody got in | Off tonight | once a night | evening yes; held from midnight; dropped by the morning |
| Side left on by hand, empty | `left_on` | the sleeper | on by hand (nap, timer), nobody in it for 20 min | Turn it off | once per by-hand session, 2 a day | held from midnight; told later only if still on and empty |
| Night not started | `not_started` | the sleeper | lying in it ~40 min, bed radar not backing it, no pet suspected, not away; never in the last hour before wake | Start now | once a night | not held (it is about getting into bed) |
| Morning summary | `morning` | the sleeper | night done, up to 4 h after its end, and a night in bed | check-in: Too cold · Just right · Too warm (calibration week only) | once a day | n/a (after wake); carries anything held overnight and an open pet question |
| Morning away check | `away_check` | the person it is about | day after a night they weren't in their side (or "back" when marked away) | see inventory | once a night | held while in bed; never for a guest |
| Pet question | `pet_question` | that side's sleeper | open question, 8 AM to 9 PM, not while a morning summary is pending, not for someone marked away | Yes, it was (pet) · No, it was me · Not sure | once per question, 1 a day | n/a (daytime) |
| Bed hub offline | `problem` | household (people with `problems`) | no word from the bed for 5 min | none | once per outage, 3 household a day | held while anyone sleeps |
| Stale sensor | `problem` | household | one side's readings frozen 10 min during a night, bed online | none | once a night per side | held while anyone sleeps |
| Bed ignoring commands | `problem` | household | online but unconfirmed for 20 min | none | once a night per side | held |
| Side not updating | `problem` | household | person's update failed 120 ticks in a row | none | once a night per person | held |
| Hub fault, low water, safety check | `hub` | owner phones with `hub` on | the hub reports it | none | once per problem per day, 3 a day | held; a failing safety check while a night runs goes at once |
| Care reminders | `maintenance` | owners and sleepers with `maintenance` on, not away | a job is due, 9 AM to 7 PM | It's done · Remind me next week | 1 a day, one follow-up after 7 days | skipped, never held |
| Held overnight, no summary coming | `digest` | each person once | after wake | none | 1 a day | after wake only; each person's own switches still apply |
| Guest mode left on | none | n/a | nothing is sent about a guest or the guest's side (no reminders, pet questions, morning summary, away or sensor messages) | n/a | n/a | n/a |
| Autopilot suggestions | none (in-app only) | n/a | shown on Sleep and in Studio, never pushed | n/a | n/a | n/a |
| Connected account signed out | `account` | that person | see inventory | none (says where) | 1 a day, 4 reminders | daytime only |
| Phone not receiving notifications | `reach` | owner | 3 undeliverable messages in a row | none | 1 a day, 4 reminders | daytime only |
| Home Assistant link down | `bridge` | owner | 3 hours without a connection | none | 1 a day, 4 reminders | daytime only |
| Disk nearly full | `storage` | owner | under 1 GB or 5% free | none | 1 a day, 4 reminders | daytime only |
| Watch or ring silent two nights | `wearable` | that person | see inventory | none (says where) | 1 a day, 4 reminders | daytime only |
| Bed's sensors reported nothing all night | `bed_silent` | the sleeper of each side | after wake, night done | none | 1 a night, 4 nights | after wake only |
| Presence sensor flipping | `sensor` | owner | set aside for 2 h | none | 1 a day per sensor, 4 reminders | daytime only |
| Presence sensor stuck on | `sensor` | owner | on over 24 h, strip says empty 4 h+ | none | 1 a week per sensor, 4 reminders | daytime only |
| Side not reaching its temperature | `target` | sleeper and owners | 40 min steady, over 4 °F off, not moving | none | 1 a night per side (+1 escalation), 4 nights | held; over 9 °F for an hour goes at once to owners |
| A button was used | `action_done` | the person | right after a ntfy / Home Assistant link button | none | not capped | the app and web page show their own "done", so no second ping there |

Buttons: each ntfy / Home Assistant / web button is a random single-use code for one person and one thing. It works once (a
second tap says "This button has expired or was already used"), expires after 12 hours, and "Start anyway", "Off tonight"
and "Wait an hour" also stop working when the night they were about ends (tapped the next morning they would otherwise start
a night in the day or cancel one the message was never about). The app's own buttons call the phone's device key (works
signed out; own side only; answers are idempotent).

### Simulations

- `test_sim_notify_triggers.py`: each row above (trigger, recipient, buttons, repeats, switches, guest, marked away, partner).
- `test_sim_notify_delivery.py`: every provider (Android push, iPhone web push, UnifiedPush, ntfy, Home Assistant incl. the
  one-tap listener), no phone, two phones, rejected or expired tokens (pruned, next channel used), providers down or hanging,
  quiet hours (what is held, what is not, urgent), caps, de-duplication, partner privacy, restarts.
- `test_sim_notify_actions.py`: every button once, twice, expired, other side, stale, the quiet "Done" and the phone key.
- `test_sim_notify_tones.py`: every kind in calm, friendly and cheeky; hygiene (no placeholders, length, plain words).
- `test_sim_notify_gaps.py`: the four newer kinds, including "never at night, never held".
- `test_sim_notify_more.py`: `wearable`, `bed_silent`, `sensor`, `target` (and the push `expires`); whole-night versions in
  `test_sim_night_gaps.py` and `test_sim_night_time.py`.

What the simulations found and fixed: a pet question went to someone marked away; a held hub fault or bed trouble was told
in the morning to people who are not owners or had that switch off; held evening notes ("your side is empty", "you're not
home") were told the next morning when no longer true; FCM tokens Google calls invalid were never pruned (every message
tried them again); a button tapped the next morning acted on the wrong night; in-app buttons logged a false "undelivered";
the not-started and away-check messages ignored the person's tone.

### Not built (proposals; see the owner's report)

Health Connect permission withdrawn (app side); phone not checked in for days; server restarted during a night; calendar
feed failing.

Night-bound buttons ("Start anyway", "Off tonight", "Wait an hour") also carry an `expires` time (the night's end) in the
app push, and Android removes the notification then (12 hours when the field is absent). A message's `link` is used by
the web / iPhone app; Android opens the app's front page.

## From the user's side (2026-11-08 audit)

Lab weeks (`python tests/notify_week.py`, simulator, Autopilot off). A ping = a notification that actually went out;
household messages count for both people.

| Week | Before: pings (most in a day, at night 23:00–06:30) | After |
|---|---|---|
| Normal, side A | 7 (1 a day, 0 at night) | 7 (1 a day, 0 at night) |
| Normal, side B | 7 (1, 0) | 7 (1, 0) |
| Messy, side A | 12 (3, **2 at night**) | 10 (3, 0) |
| Messy, side B | 10 (3, **3 at night**) | 6 (1, 0) |

The messy week: out late on a weeknight (not home at pre-condition), the cat on the bed at 2:40 AM, the bed offline 25 min
at 2 AM plus a frozen sensor at 3 AM, a guest on one side, an afternoon nap left on by hand, and a night one person
never came to bed.

What we found:
- **Woke people at night**: the bed-offline and frozen-sensor alerts went to every phone at 2 and 3 AM (and to the
  household ntfy as well), and "your side is pre-conditioned but empty" went out at 3 AM to someone who wasn't in bed.
- **Duplicated**: household alerts went to the household target and every phone; a ntfy/HA button sent a "done" ping
  to the app too, although the app shows its own.
- **Three pings where one would do**: a pet question from the night arrived at 8 AM, separately from the morning summary.
- **Unused setting**: `problems` existed but nothing read it; `pet_question` had no switch in Settings or Studio.
- **Not bad**: a normal week is one morning summary a day. Buttons are few and clear; only "Pre-condition, not home" has
  three, because it really is a choice.
- A night with nobody in bed still produced a "Didn't get up" summary; now there's no summary without a night in bed.

## Rules (now)

- **Quiet hours, on by default** (`quiet_hours`): nothing non-urgent while you're in bed, or between bedtime and
  wake unless you're known to be out or up. Held messages are told once, inside the morning summary ("Overnight:
  2:06 AM: Can't reach the bed."), or as one "While you slept" note once you're up when no summary is coming.
  Messages about being away or out of bed (`away_precool`, `empty_side`, `left_on`) still go out in the evening,
  but from midnight to wake they're held too (and dropped if a night in bed follows: the morning summary doesn't
  bring up a "your side is empty" from before you got in). Nothing is currently urgent enough to wake anyone: the bed keeps its
  last temperature when it can't be reached.
- **Caps per day** (`notify.CAPS`): morning 1, digest 1, pet question 1, pre-condition 1, empty side 1, side-hasn't-started 1, left on 2,
  household problems 3, hub 3, care 1, account 1, reach 2, bridge 1, storage 1, wearable 1, bed_silent 1, sensor 3 (household), target 2; and at most 4 capped pings per person per day all together. Tests and button confirmations
  are not capped.
- **De-duplication**: the same message (person, type, title) within 6 hours is dropped, and every message uses one
  channel per person (above). Skipped and held messages are logged as `notification_skipped` / `notification_held`
  events, so the log still shows them.
- **Batching**: the morning summary carries the night in one line, anything held overnight, an open pet question,
  and a single action (tap to check in).
- **Every type has a switch** in phone Settings → Notifications and Studio → Your settings → Notifications
  (`components/NotifyPrefs.tsx`), plus quiet hours and the tone.

## Wording and tone

`PersonSettings.tone`: `calm` (default, the original plain style), `friendly`, `cheeky`. Each message type in
`voice.PHRASES` has at least three variants per tone with exactly the same slots, so names, phases, times and numbers
never change between variants. The pick is deterministic per person, type and day (no flicker on reload), and never
repeats the previous pick back to back (kv `voice_last:<pid>` for notifications; a per-person stride for in-app lines).
Cheeky is playful but never rude, never sexual, never about weight or bodies, never at anyone's expense, and alerts
(`ALERTS`: bed offline, frozen sensor, overnight lines) are plain in every tone.

| Message | Calm | Friendly | Cheeky |
|---|---|---|---|
| Autopilot idea (warm Deep) | Deep has felt warm on 3 of the last 7 nights. Autopilot and your own taps point the same way. Make Deep 1 °F cooler? | Deep has been warm for you: 3 of the last 7 nights. Your taps and Autopilot agree. Shall we make Deep 1 °F cooler? | Deep ran warm on 3 of the last 7 nights. Shall we make Deep 1 °F cooler before you start sleeping on top of the covers like a starfish? |
| Pet question | Was Biscuit on the bed around 2:40 AM–3:10 AM? | Was Biscuit on the bed around 2:40 AM–3:10 AM? Quick one: tap to answer so it learns your pet from you. | Was Biscuit hogging your side again around 2:40 AM–3:10 AM? |
| Left on | Alex's side is on, but nobody's in it | Alex, your side is still on | Alex's side is on for nobody |
| Check-in | How did it feel? Tap to check in. | How did it feel? One tap helps it learn. | Goldilocks check: too hot, too cold or just right? Tap to say. |
| Story: pet joined | Looked like Biscuit joined you | Biscuit seemed to snuggle in with you | Biscuit crashed your side, by the look of it |
| Bed offline | Can't reach the bed | Can't reach the bed | Can't reach the bed |

Adding a message type: add it to `voice.PHRASES` (every message a person reads, including not-started and the away check, is there) (3+ variants per tone, same slots; add it to `ALERTS` if it's about
something going wrong), give it a cap in `notify.CAPS`, a switch in `NotifyPrefs` + `components/NotifyPrefs.tsx`,
a row in the table above, and the tests in `test_voice.py` pick it up by themselves.
