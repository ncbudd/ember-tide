# Talk to your bed

Ember & Tide has no voice assistant of its own and no Apple, Google or Amazon account behind it. You can still use your voice,
in three ways: through **Home Assistant** (Siri, Google Assistant, Alexa and Home Assistant's own Assist), through **Apple
Shortcuts** on an iPhone (Siri, no Home Assistant needed), or by opening the **Android app** by voice. This page shows each one, with
the phrases that work and the limits to expect.

The examples use two made-up sleepers, Alex (left side) and Sam (right side), and a server at `http://192.168.1.20:8790`.

## Which way is for me?

| I use... | Use this | What you get |
|---|---|---|
| **Home Assistant** and **Apple Home / Siri** | [Home Assistant → HomeKit Bridge](#apple-home-and-siri-through-home-assistant) | Each side as a thermostat in the Home app; flashes as switches |
| **Home Assistant** and **Google Home / Google Assistant** | [Home Assistant Cloud or the manual Google Assistant integration](#google-assistant-through-home-assistant) | Each side as a thermostat; flashes as scenes |
| **Home Assistant** and **Alexa** | [Home Assistant Cloud or the manual Alexa integration](#alexa-through-home-assistant) | Each side as a thermostat; flashes as scenes |
| **Home Assistant** and its own voice (**Assist**, a Voice PE, the HA app) | [Assist](#assist-home-assistants-own-voice) | The same entities, by voice, fully local |
| An **iPhone**, no Home Assistant | [Siri with Apple Shortcuts](#siri-with-apple-shortcuts-no-home-assistant) | "Hey Siri, bed hot flash", "Hey Siri, bed warmer", "How's my bed?" |
| An **Android phone**, no Home Assistant | [Android](#android) | Open the app by voice; one tap from the widget, the tile or the watch |

## Through Home Assistant

### What Ember & Tide puts in Home Assistant

Connect Home Assistant first ([install guide, Home Assistant](install.md#home-assistant)). Each person's side then appears as one
device, "Ember & Tide · Alex", with these entities (the IDs use the person's name the first time the side was published, and never change):

| Entity | What it is | What it does |
|---|---|---|
| `climate.embertide_alex` | A thermostat with two modes, **off** and **heat/cool** | Current temperature = the pad. Setting a temperature = warmer or cooler **until the next phase** (like + / − in the app). **Off** = **Off tonight** (the side stays off until tomorrow evening). **Heat/cool** = turn the side back on: the night resumes, or, when nothing is planned right now, the side turns on by hand at the bed's last temperature until the next phase. |
| `button.embertide_alex_hot_flash` | Hot flash | Cools the side for the flash time in Alex's settings, then eases back |
| `button.embertide_alex_cold_flash` | Cold flash | Warms the side the same way |
| `button.embertide_alex_stop_flash`, `button.embertide_alex_extend_flash` | Stop flash, Flash +5 min | |
| `button.embertide_alex_pause_hour` | Pause for an hour | The side turns off, then back on by itself |
| `button.embertide_alex_feel_too_hot`, `_feel_too_cold`, `_feel_just_right` | How it feels | Changes nothing now; Autopilot learns from it (in the morning it answers the check-in) |
| `binary_sensor.embertide_alex_in_bed` | In bed | Occupancy |
| `sensor.embertide_alex_phase`, `_next_change`, `_flash_end`, `_plan`, `_tonight` | What the side is doing | Phase name, next change, flash end, the rhythm's temperature now, tonight's plan |
| `number.embertide_alex_<phase>_temp` | Each phase's temperature | Changes the rhythm itself (every night). These are "configuration" entities, so voice assistants leave them out unless you add them |

The thermostat uses the person's own units (°F or °C) from their settings; Apple, Google and Amazon convert to yours.
A side lent to a guest is a plain on/off thermostat with a temperature, and its flash buttons do nothing.

### Give things names you can say

Voice assistants use the entity's name. "Ember & Tide · Alex Hot flash" is hard to say, so give each one a short name or an alias first:
in Home Assistant open **Settings → Voice assistants → Expose**, pick the entity, and add an **alias** such as "Alex's side",
"Alex's hot flash", "Alex's cold flash". Assist uses aliases, and so does Google Assistant through Home Assistant Cloud. If Alexa or the
Home app shows a long name, rename the device in the Alexa app or the Home app (or change the entity's name in Home Assistant).

Expose only what you will use by voice: the thermostat and the two flash buttons per person are usually enough. Fewer entities means fewer
mix-ups ("turn off Alex" should not find six things).

### Apple Home and Siri through Home Assistant

Uses Home Assistant's built-in **HomeKit Bridge** integration. Everything stays on your home network.

1. In Home Assistant: **Settings → Devices & services → Add integration → HomeKit Bridge**.
2. When it asks which domains to include, tick **Climate** and **Button**. Then open the bridge's **Configure** and choose
   **Include** mode with just the entities you want (`climate.embertide_alex`, `button.embertide_alex_hot_flash`, and so on).
   One bridge for everything is fine.
3. A notification in Home Assistant shows a QR code and an 8-digit code. On the iPhone open **Home → + → Add Accessory**, scan it,
   and tap **Add Anyway** when it says the accessory is not certified (Home Assistant's bridge never is; that is normal).
4. Put the accessories in the Bedroom and rename them: "Alex's side", "Alex's hot flash", "Alex's cold flash".

How they show up: each side is a **Thermostat** with Off and Auto (Auto is Ember & Tide's heat/cool). Each button is a **Switch** that turns
itself off again after a moment. The in-bed sensor, if you include it, is an **Occupancy sensor** you can use in Home automations.

Say:
- "Hey Siri, set Alex's side to 68."
- "Hey Siri, turn on Alex's hot flash." (it starts the flash; the switch flips back off by itself)
- "Hey Siri, turn off Alex's side." (Off tonight)
- "Hey Siri, turn on Alex's side." or "Set Alex's side to Auto." (back on)
- "Hey Siri, what's the temperature of Alex's side?" (the pad's temperature)

### Google Assistant through Home Assistant

Two ways, both through Home Assistant:

- **Home Assistant Cloud** (Nabu Casa; a paid subscription with a free trial, and it supports Home Assistant's development). The easy way:
  1. **Settings → Home Assistant Cloud**, sign in, turn on **Google Assistant**.
  2. **Settings → Voice assistants → Expose**: expose the thermostat and flash buttons, with aliases.
  3. In the **Google Home** app: **+ → Device → Works with Google Home**, search for **Home Assistant Cloud by Nabu Casa** and sign in.
- **Manual Google Assistant integration** (free, more work). It needs a project in the Google Home Developer Console, a service account
  and the HomeGraph API, and Home Assistant must be reachable from the internet over HTTPS. Follow Home Assistant's
  [Google Assistant integration](https://www.home-assistant.io/integrations/google_assistant/) page step by step; Google changes these
  screens from time to time.

How they show up: each side is a **thermostat** (modes off and heat/cool); each button is a **scene**. The in-bed sensor is not shown (Google
takes only door, window, smoke and similar binary sensors).

Say:
- "Hey Google, set Alex's side to 68."
- "Hey Google, activate Alex's hot flash." (scenes are activated, not turned on)
- "Hey Google, turn off Alex's side." (Off tonight) · "Hey Google, turn on Alex's side." (back on)
- "Hey Google, what's Alex's side set to?"

### Alexa through Home Assistant

- **Home Assistant Cloud**: **Settings → Home Assistant Cloud**, turn on **Alexa**, expose the entities (with aliases), then in the Alexa
  app enable the **Home Assistant** skill and sign in. Ask Alexa to discover devices.
- **Manual Alexa Smart Home integration**: free, but it needs an Amazon developer account, an AWS account, a Lambda function and your
  own Smart Home skill, and Home Assistant reachable from the internet over HTTPS. See Home Assistant's
  [Alexa Smart Home](https://www.home-assistant.io/integrations/alexa.smart_home/) page.

How they show up: each side is a **thermostat**; each button is a **scene**. The in-bed sensor is not shown (Alexa does not take occupancy
sensors).

Say:
- "Alexa, set Alex's side to 68."
- "Alexa, turn on Alex's hot flash." (Alexa runs a scene with "turn on")
- "Alexa, turn off Alex's side." (Off tonight) · "Alexa, turn on Alex's side." (back on)

### Assist: Home Assistant's own voice

Assist works in the Home Assistant app, on a Voice PE or ESPHome satellite, and on a watch, and it can run fully at home. Expose the
entities to Assist (**Settings → Voice assistants → Expose**) with aliases, then:

- "Set Alex's side to 68."
- "Press Alex's hot flash." (or "turn on Alex's hot flash")
- "Turn off Alex's side." · "Turn on Alex's side."
- "Is Alex in bed?" (if the in-bed sensor is exposed)

### Limits to know

- **Off means Off tonight.** Every assistant only knows a thermostat, so "turn off" is the thermostat's off: the side stays off until
  tomorrow evening's rhythm. To get it back tonight, turn it on again ("turn on Alex's side").
- **A temperature lasts until the next phase.** Setting 68 by voice is the same as + / − in the app: the next phase of the rhythm takes over
  again. To change every night, change the rhythm in the app (or the phase-temperature numbers in Home Assistant).
- **Flashes are buttons.** Apple shows them as switches that flip back off by themselves, Google and Alexa as scenes. A flash ends on its own;
  say "activate Alex's stop flash" (or press Stop in the app) to end it early.
- **Partner sharing.** Home Assistant talks to Ember & Tide as the bridge, not as a person, so anyone who can talk to your assistant can change
  either side. Expose only the sides you want, or keep a person's entities out of shared speakers.
- **Over the internet.** HomeKit Bridge and Assist stay at home. Google Assistant and Alexa go through Google's or Amazon's cloud to your Home
  Assistant (directly with the manual setups, or through Home Assistant Cloud). Ember & Tide itself still only talks to Home Assistant.

## Siri with Apple Shortcuts (no Home Assistant)

Shortcuts can call Ember & Tide's API directly, and every shortcut can be run by saying its name to Siri. Each one needs a **key for your
side only**. Never put a "Full control" key in a shortcut: anyone with the phone, or a shared shortcut, would have the whole house.

### 1. Make a key for your side

A side key can do what that person can do on their own side (temperature, flashes, off tonight, their settings), the partner's side only as far as
the partner shares it, and nothing that is the owner's (accounts, keys, devices, the bed console). The partner's heart rate and phone details stay
hidden from it.

Studio's key list offers only "Full control" and "Read only", so a side key is made with one request. The owner does this once per person, signed in to
Studio in a browser on a computer:

1. Find the person's id: open `http://192.168.1.20:8790/api/household` in the same browser and look for `"id"` next to their `"name"`
   (for example `"id": "p1", "name": "Alex"`).
2. Open the browser's developer console (F12 → **Console**) on the Studio page and run, with that id and a name you will recognise:
   ```js
   fetch("/api/auth/tokens", {method: "POST", headers: {"X-Embertide": "1", "Content-Type": "application/json"},
     body: JSON.stringify({name: "Siri (Alex)", role: "sleeper", person: "p1"})}).then(r => r.json()).then(console.log)
   ```
3. Copy the `token` from the answer (it starts with `et_`). It is shown only this once.
4. In **Studio → People & access → Keys for devices and scripts** the key now shows as "Alex's side". Revoke it there at any time.

The same request with `curl` and a temporary "Full control" key also works (make the temporary key in Studio, use it once, then revoke it):

```bash
curl -X POST http://192.168.1.20:8790/api/auth/tokens \
  -H "Authorization: Bearer et_TEMPORARY_FULL_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Siri (Alex)", "role": "sleeper", "person": "p1"}'
```

### 2. Which address

At home, `http://192.168.1.20:8790` (your server's address) works. Away from home you need your HTTPS address, for example
`https://sleep.example.com` (see [HTTPS](install.md#d-https-phones-away-from-home-and-iphone-notifications)). The first time a shortcut runs,
iOS asks whether Shortcuts may connect to that address (and, for a home address, to devices on your local network): allow it.

### 3. The shortcuts

Each shortcut is one **Get Contents of URL** action. In the Shortcuts app: **+**, add **Get Contents of URL**, paste the URL, tap **Show More**, and set
the method, headers and body below. **Headers**: add `Authorization` with the value `Bearer et_…` (your side key). **Request Body**: **JSON**, then add
each field (Text or Number as shown). Name the shortcut with the phrase you will say. In the examples `p1` is Alex's id.

Every request below was checked against a local server with the simulated bed.

| Shortcut name (what you say) | Method and URL | JSON body | What happens |
|---|---|---|---|
| **Bed hot flash** | `POST http://192.168.1.20:8790/api/people/p1/flash` | `kind` = `hot` (Text), `source` = `voice` (Text) | Hot flash with Alex's flash settings |
| **Bed cold flash** | `POST …/api/people/p1/flash` | `kind` = `cold`, `source` = `voice` | Cold flash |
| **Stop the flash** | `POST …/api/people/p1/flash/stop?source=voice` | none | Ends the flash; the side eases back over a few minutes |
| **Bed warmer** | `POST …/api/people/p1/adjust` | `delta_c` = `0.56` (Number), `until` = `next_phase`, `source` = `voice` | 1 °F warmer until the next phase (use `0.5` for 0.5 °C) |
| **Bed cooler** | `POST …/api/people/p1/adjust` | `delta_c` = `-0.56` (Number), `until` = `next_phase`, `source` = `voice` | 1 °F cooler until the next phase |
| **Bed back to normal** | `POST …/api/people/p1/adjust/clear?source=voice` | none | Undoes warmer / cooler: back to the rhythm |
| **Bed off tonight** | `POST …/api/people/p1/tonight` | `action` = `skip`, `source` = `voice` | Off tonight: the side stays off until tomorrow evening |
| **Bed back on** | `POST …/api/people/p1/tonight` | `action` = `resume`, `source` = `voice` | The night resumes |

For "warmer" or "cooler" by more, change the number: one step is 0.56 for °F (1 °F) or 0.5 for °C; the server keeps every change inside the household's
everyday range. Pressing Warmer while the side is off tonight turns the night back on, one step warmer, as on the watch. To set a temperature instead,
send `temp_c` (Number, in °C: 68 °F is 20) instead of `delta_c`.

The same requests with `curl`, for testing from a computer (replace the key):

```bash
K=et_YOUR_SIDE_KEY
S=http://192.168.1.20:8790
curl -X POST "$S/api/people/p1/flash" -H "Authorization: Bearer $K" -H "Content-Type: application/json" -d '{"kind":"hot","source":"voice"}'
curl -X POST "$S/api/people/p1/flash/stop?source=voice" -H "Authorization: Bearer $K"
curl -X POST "$S/api/people/p1/adjust" -H "Authorization: Bearer $K" -H "Content-Type: application/json" -d '{"delta_c":0.56,"until":"next_phase","source":"voice"}'
curl -X POST "$S/api/people/p1/adjust/clear?source=voice" -H "Authorization: Bearer $K"
curl -X POST "$S/api/people/p1/tonight" -H "Authorization: Bearer $K" -H "Content-Type: application/json" -d '{"action":"skip","source":"voice"}'
curl -X POST "$S/api/people/p1/tonight" -H "Authorization: Bearer $K" -H "Content-Type: application/json" -d '{"action":"resume","source":"voice"}'
curl "$S/api/snapshot" -H "Authorization: Bearer $K"
```

Keys sent as `Authorization: Bearer` don't need the `X-Embertide` header (that one is only for the signed-in browser). A wrong or revoked key answers
401; something the key may not do answers 403 with a plain reason (for example "They haven't shared control of their side.").

### "How's my bed?" (Siri reads it out)

1. **Get Contents of URL**: `GET http://192.168.1.20:8790/api/snapshot`, header `Authorization: Bearer et_…`.
2. **Get Dictionary Value**: `people` from Contents of URL.
3. **Repeat with Each** item in that list, and inside it **If** (Dictionary Value `id` of Repeat Item) **is** `p1`:
   - **Get Dictionary Value** `night` → then `target_c` (the temperature the side is set to now, in °C; empty when the side is off) and `phase_name`
     (for example "Deep").
   - **Get Dictionary Value** `bed` → `measured_c` (the pad's temperature now, in °C).
   - For °F: **Calculate** the value × 1.8 + 32, then **Round Number**.
4. After the repeat: **Speak Text** (or **Show Result**), for example "Your side is in Deep, set to 63, the pad is at 64."

Name it "How's my bed" and say "Hey Siri, how's my bed?".

### Tips

- Say the shortcut's exact name. Names starting with "Bed" keep Siri from mixing them up with your other devices.
- Add the shortcuts to the Lock Screen, the Action button or the Apple Watch (in each shortcut's details, **Show on Apple Watch**) for a tap
  instead of a sentence.
- Partners each make their own key and their own shortcuts with their own id. Sharing a shortcut shares the key inside it: delete the header
  before sharing.

## Android

What works today, without Home Assistant:

- **"Hey Google, open Ember & Tide"** opens the app. That is as far as voice goes on its own.
- **App shortcuts** (long-press the app icon): Hot flash, Cold flash, Off tonight, Simple screen. They run with one tap. Google Assistant and Gemini
  cannot press these by voice: speaking an app's shortcuts needs Google's App Actions, which only work for apps installed from the Play Store, and
  Ember & Tide is installed from its own APK. Reports also say Gemini does not run App Actions the way Google Assistant did.
- **One tap without opening the app**: the home-screen **widget**, the **Hot flash** tile in Quick Settings, the buttons in the flash
  notification, and the **Wear OS** app and tile ([Android guide](android.md)).
- **Voice Access** (Android's accessibility app, free from Google) can press any button on screen by its label: open the app by voice, then say
  "tap Hot flash", "tap Warmer" or "tap Cooler". It is slower than a tap but fully hands-free.
- With **Home Assistant**, Google Assistant on the phone controls the bed like any other thermostat ([above](#google-assistant-through-home-assistant)).

## Not available yet

- **No direct Apple Home, Google Home or Alexa connection without Home Assistant.** Ember & Tide does not appear in those apps by itself. A
  Matter bridge (which would put each side in Apple Home, Google Home and Alexa directly, at home) is possible future work. Until then, use Home
  Assistant, or Shortcuts on an iPhone.
- **No native iPhone app**, so no Siri suggestions or App Intents. Shortcuts with a side key (above) cover the same ground.
- **No Alexa skill or Google action of our own.** Both would need a cloud service run by the project, which goes against keeping everything at home.
