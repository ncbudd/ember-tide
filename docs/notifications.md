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
| Pre-cool, not home (`away_precool`) | pre-cool time while the person is away (phone / GPS / manual) | the sleeper | evening, at pre-cool | 1 a night | Wait an hour · Off tonight · Start anyway |
| Pre-cooled but empty (`empty_side`) | the side waited its whole window and nobody got in (state "missed") | the sleeper | evening; after midnight held for the morning | 1 a night | Turn it off tonight |
| Left on by hand (`left_on`) | turned on by hand (nap, timer), nobody in it for 20 min | the sleeper | any time; after midnight held | 1 per session, 2 a day | Turn it off |
| Side hasn't started (`not_started`) | lying in it ~40 min, bed radar still not backing it, no pet suspected, not away | the sleeper | any time except the last hour before wake and after it; not held by quiet hours | 1 a night | Start now |
| Pet question (`pet_question`, and `pet_questions` for asking at all) | something lay on the side and looked like a pet, or lay there with no night running | that side's sleeper | 8 AM–9 PM; a question from the night rides in the morning summary | 1 per question, 1 a day | none; tap opens the question |
| Can't reach the bed (`problems`) | no word from the bed for 5 min | household | at once in the day; held while anyone sleeps | 1 per outage, 3 household problems a day | none |
| Bed sensor stopped updating (`problems`) | one side's readings frozen 10 min during a night (not while the whole bed is offline) | household | as above | 1 a night per side | none |
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

## From the user's side (2026-11-08 audit)

Lab weeks (`python tests/notify_week.py`, simulator, Autopilot off). A ping = a notification that actually went out;
household messages count for both people.

| Week | Before: pings (most in a day, at night 23:00–06:30) | After |
|---|---|---|
| Normal, side A | 7 (1 a day, 0 at night) | 7 (1 a day, 0 at night) |
| Normal, side B | 7 (1, 0) | 7 (1, 0) |
| Messy, side A | 12 (3, **2 at night**) | 10 (3, 0) |
| Messy, side B | 10 (3, **3 at night**) | 6 (1, 0) |

The messy week: out late on a weeknight (not home at pre-cool), the cat on the bed at 2:40 AM, the bed offline 25 min
at 2 AM plus a frozen sensor at 3 AM, a guest on one side, an afternoon nap left on by hand, and a night one person
never came to bed.

What we found:
- **Woke people at night**: the bed-offline and frozen-sensor alerts went to every phone at 2 and 3 AM (and to the
  household ntfy as well), and "your side is pre-cooled but empty" went out at 3 AM to someone who wasn't in bed.
- **Duplicated**: household alerts went to the household target and every phone; a ntfy/HA button sent a "done" ping
  to the app too, although the app shows its own.
- **Three pings where one would do**: a pet question from the night arrived at 8 AM, separately from the morning summary.
- **Unused setting**: `problems` existed but nothing read it; `pet_question` had no switch in Settings or Studio.
- **Not bad**: a normal week is one morning summary a day. Buttons are few and clear; only "Pre-cool, not home" has
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
- **Caps per day** (`notify.CAPS`): morning 1, digest 1, pet question 1, pre-cool 1, empty side 1, side-hasn't-started 1, left on 2,
  household problems 3; and at most 4 capped pings per person per day all together. Tests and button confirmations
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

Adding a message type: add it to `voice.PHRASES` (3+ variants per tone, same slots; add it to `ALERTS` if it's about
something going wrong), give it a cap in `notify.CAPS`, a switch in `NotifyPrefs` + `components/NotifyPrefs.tsx`,
a row in the table above, and the tests in `test_voice.py` pick it up by themselves.
