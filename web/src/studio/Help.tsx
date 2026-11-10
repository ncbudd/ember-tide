import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { api } from "../api";

/**
 * Studio → Help: what everything means, in plain words. Each section has a stable id for deep links
 * (/studio/help#autopilot), and the filter box searches titles and text.
 */

interface Item { term: string; text: string }
interface Section { id: string; title: string; intro?: string; items: Item[] }

const SECTIONS: Section[] = [
  {
    id: "night", title: "The night",
    intro: "Each side of the bed follows its own plan through the night: a rhythm made of a few parts, each with its own temperature.",
    items: [
      { term: "Rhythm", text: "Your nightly plan. You can have one for the nights before a work day and another for the nights before a day off (set your work days in Settings). Change it on the phone (Rhythm) or in Studio → Rhythm editor." },
      { term: "Wind-down", text: "Before bedtime, the side cools (or warms) toward your first temperature so it's ready when you get in." },
      { term: "Fall asleep", text: "The first part after you get into bed. Usually a little cooler, which helps you drift off." },
      { term: "Deep", text: "The middle of the night, when deep sleep happens most. Hot sleepers usually want this coolest." },
      { term: "Late night", text: "The early-morning hours. Many people like it a touch warmer than deep." },
      { term: "Warm wake", text: "The last half hour before your wake time: it warms gently so waking up feels easier." },
      { term: "Bedtime vs in bed", text: "Bedtime is when you usually go to bed; it times the pre-cool. The night itself starts when you actually get into bed (the bed's sensor sees you), so late nights just work." },
      { term: "Pre-cool", text: "The side gets to your first temperature before bedtime, so the bed is ready. If your phone shows you're out, it waits." },
      { term: "Only start between", text: "Getting into bed only starts the night between Earliest and Latest, so an afternoon nap on the bed doesn't. Getting in later still works, up to an hour before wake." },
      { term: "Out of bed", text: "Get up in the night and your side holds its temperature for a while (30 minutes by default), then turns off until you're back." },
      { term: "No sensor data", text: "If the bed can't tell whether you're in bed, the night starts on schedule 10 minutes after bedtime, unless your calendar says you're still out." },
    ],
  },
  {
    id: "tonight", title: "Tonight controls",
    items: [
      { term: "Warmer / Cooler", text: "Nudges your side by 1° each press. By default it lasts until the next part of the night; from the Tonight menu you can make it last all night or change your rhythm for every night." },
      { term: "Hot flash / Cold flash", text: "A quick blast of cold or warmth, 15 minutes by default, then it eases back to the plan. Press again for 5 more minutes, or stop it early. Change the defaults in Settings." },
      { term: "Pause", text: "Turns your side off for an hour, then it carries on." },
      { term: "Off tonight", text: "Turns your side off until tomorrow evening. Pressing + or − brings it back." },
      { term: "Turn on now", text: "Outside the night (a nap, an early night) you can turn your side on by hand with a timer. It turns off at the timer or when your rhythm starts." },
      { term: "Wake up earlier tonight", text: "Shows when your calendar has an early start tomorrow: moves tonight's warm wake so you wake up in time." },
      { term: "Simple screen", text: "A big, dark, one-temperature screen for the middle of the night. It can switch on by itself once you're in bed." },
    ],
  },
  {
    id: "autopilot", title: "Autopilot",
    intro: "Autopilot notices when you seem too warm or too cold and says why. It only makes small changes, inside limits you set.",
    items: [
      { term: "Modes", text: "Off: never changes anything. Suggest: notes what it would do and offers changes in the morning. Act: makes small changes during the night." },
      { term: "Learning week", text: "For the first nights (7 by default) it only watches, to learn what a normal night looks like for you: how much you move, your settled heart rate, your skin temperature. It suggests nothing until then. You can start it early in Studio → Autopilot." },
      { term: "Signals", text: "What it looks at: your own too hot / too cold taps (strongest), skin temperature from a watch or ring, restlessness, heart rate, recent flashes, the bed running warmer than planned, and the bedroom's temperature and humidity." },
      { term: "Your tap wins", text: "If you tap too hot and the watch disagrees, it goes with you. Restlessness on its own never changes anything; something else has to agree." },
      { term: "Trust", text: "How much each source counts: the bed's own sensors, your watch or ring, and the room sensor. 0 ignores it, 1 is normal, 2 counts double. Turn down a source you know is unreliable." },
      { term: "Limits", text: "Never below / never above a temperature, the most it can change in one night, how many changes, the gap between them, quiet time before you wake, and which parts of the night it may touch. Your own change always wins for a while (30 minutes by default)." },
      { term: "Suggestions", text: "Every morning, once a week, once a month, or never. Suggestions only appear when a pattern shows on several nights. Not now hides one until new nights add evidence; Never hides that kind for good." },
    ],
  },
  {
    id: "guide", title: "Tuning guide and insights",
    items: [
      { term: "Tuning guide", text: "Plain suggestions from how your nights actually went: you usually get into bed later than your set bedtime, you get up before your wake time, workouts or late caffeine slow your settling, lights go off long after you get in. Ordinary nights only: late events and nights away are left out." },
      { term: "Apply", text: "Most suggestions can be applied with one press; they're small and easy to undo in the Rhythm editor." },
      { term: "Early days", text: "Insights need a few finished nights. In the first week you'll mostly see the learning progress." },
    ],
  },
  {
    id: "devices", title: "Devices: how they work and how to set them up",
    intro: "Anything beyond the bed itself. None are required; each one answers a question the bed can't. Add them in Studio → Devices: pick the type, connect it (Home Assistant, MQTT, a calendar link, or simulated to try things out), say whose or which side it is, and you're done. Every type starts at recommended settings.",
    items: [
      { term: "Bed sensor strip", text: "Built into the topper: in bed or not, plus heart rate and breathing. It can mistake one person for two (both sides reacting to one sleeper), which is why a second opinion per side helps." },
      { term: "In-bed vote", text: "Each side counts as occupied when the weighted vote of its devices reaches 50%. Recommended weights: bed strip 0.50, radar 0.35, bed scale 0.60, pad 0.40, motion 0.15. A device with nothing to say (offline, or quiet longer than its limit) is left out, so the others decide." },
      { term: "Presence radar", text: "Used for: the in-bed vote. Sees people lying still (mmWave). To check it: lie still on your side; it should say someone there. If it drops out while you're still, raise 'keep counting as occupied for'; if someone leaning over sets it off, raise 'only count once detected for'." },
      { term: "Radar for both sides", text: "Two ways. One radar with a zone per side (Aqara FP2, an LD2450 board, Everything Presence): pick each side's zone. Or two single-zone sensors, one per side (for example two Aqara FP1E): place each low by its nightstand, aimed across its own side, and pick each one's occupancy entity. Either way it's one device in the list, so Edit and Remove act on both sides. Two separate sensors often both notice the partner turning over; when the bed strip or a scale says a side is empty, that side's sensor isn't allowed to mark it occupied on its own." },
      { term: "Bed scale / load cells", text: "Used for: the most reliable in-bed vote, and telling the two of you apart. Calibrate: with that side empty press Zero, then enter each person's body weight (Health Connect weight fills it in)." },
      { term: "Occupancy pad / motion sensor", text: "A pad under the mattress votes like a radar. A motion sensor only sees movement, so keep its vote low." },
      { term: "Room temperature / humidity", text: "Used for: Autopilot's room signal (a warm or muggy room is a reason to cool) and the tuning guide. Calibrate against a trusted thermometer with the offset." },
      { term: "Wearable", text: "Used for: Autopilot's skin temperature and heart rate. For a phone, use Health Connect in the Android app instead (it imports everything automatically)." },
      { term: "Bedroom light", text: "Used for: when you really went to bed (lights off), gets-up in the night (light on), the tuning guide's bedtime advice and the night explorer's Context lane. It doesn't vote on in bed and doesn't change temperatures by itself; nothing else to set up. If on shows as off, turn on 'reading is reversed'." },
      { term: "Phone on home Wi-Fi", text: "Used for: home or away. While away, that side's pre-cool waits. A phone that just goes quiet (flat battery) counts as unknown, never away. If a sleeping phone looks away at night, raise 'count as away after'." },
      { term: "Phone location", text: "Used for: away, and on the way home (pre-cool starts so the bed is ready when you arrive). From Home Assistant's Proximity integration (distance from home)." },
      { term: "From this phone", text: "The Android app can report by itself, no Studio or Home Assistant setup: in the app, Settings → From this phone. Each switch asks for its permission only when turned on. Location sends only the distance to home (never where you are), the calendar sends only times (never titles). It shows in Devices as an automatic \"This phone\" row. A device in Studio that says home still counts; a phone that goes quiet counts as unknown after a while, never away." },
      { term: "Bedside dial nearby", text: "Each bedside dial sends a small Bluetooth beacon. At night (from evening until morning), and only while the phone is charging, in bedtime mode or with its screen off, the app listens for a few seconds every 15 minutes. A phone lying right by your own side's dial counts a little (0.15, against the bed strip's 0.50) toward you being in bed, and means you're home. It can never outvote an empty bed strip or scale, and not hearing the dial never counts as \"not in bed\"." },
      { term: "Calendar feed (Fastmail, iCloud, Google)", text: "Used for: evening events (the start-on-schedule fallback waits until you're home) and early starts tomorrow (Tonight offers an earlier wake). Fastmail: Settings → Calendars → click the calendar → Publish → turn on Free/busy information (private: busy times only, all that's needed) or Full event details → copy the link → Save. iCloud: Calendar → share the calendar → Public Calendar → copy link. Google: calendar Settings → 'Secret address in iCal format'. Paste it as a Calendar device with the Calendar feed connection. Anyone with the address can read the calendar, so Studio keeps it private and only shows where it points; Fastmail's Reset URL makes a new one if it ever leaks." },
      { term: "Connecting from Home Assistant", text: "You need the Home Assistant address (e.g. http://homeassistant.local:8123), a long-lived access token (your HA profile → Security → Create token) and the entity ID (e.g. binary_sensor.bed_left_occupancy, sensor.bedroom_temperature, light.bedroom). Press Test before Next to see its current state." },
      { term: "Tuning", text: "Every setting shows what it does, what changing it does, and the recommended value; a changed value is marked. Back to defaults restores the recommended values. Most homes never need to change them." },
      { term: "Device health (set aside)", text: "A device that acts up (impossible readings, sudden jumps, flapping on and off, frozen values) is set aside: it stops voting and stops feeding Autopilot until it behaves for half an hour. The badge says why. Trust it anyway turns this off for one device." },
      { term: "Agreement", text: "How often each device agrees with the combined in-bed answer. A device that often disagrees is placed badly or needs tuning (for a radar, a longer hold usually fixes it)." },
    ],
  },
  {
    id: "health", title: "Health Connect (Android)",
    items: [
      { term: "For the night", text: "Heart rate, skin temperature, sleep stages, heart rate variability, breathing rate, blood oxygen, resting heart rate, body temperature. On by default." },
      { term: "Your day", text: "Exercise, caffeine, steps, active calories, weight. Off by default; used by the tuning guide (e.g. late workouts) and weight helps a bed scale tell sleepers apart." },
      { term: "Duplicates and clocks", text: "The same reading from two apps (say Samsung Health and Health Sync) is stored once. A watch whose clock is off is lined up with the bed's own heart rate automatically." },
    ],
  },
  {
    id: "wearables", title: "Watches, rings and iPhone",
    items: [
      { term: "Which way for which phone", text: "Android: your watch or ring's app writes to Health Connect and Ember & Tide reads it. iPhone: Apple Health, once the iPhone app is out. Oura can also connect straight through Oura's cloud, which works from any phone, including the iPhone web app." },
      { term: "Connect Oura", text: "Settings → Watch or ring (or the first-time setup) → Connect Oura → sign in at Oura and allow sleep and heart rate. It looks for new nights after you wake, then every hour, never during the night. Brings sleep stages, heart rate and HRV through the night, temperature compared with your normal, readiness and sleep score, and naps (each nap is shown on that day, with whether it was on the bed or away). Readings that also arrive through Health Connect are counted once." },
      { term: "Setting up Oura (owner, once)", text: "At developer.ouraring.com create an application, add the redirect URI shown in Studio → Integrations → Oura ring (it ends in /api/oura/callback), allow the daily and heartrate scopes, then paste its client id and secret into that card. Oura lets a new app connect up to 10 people, plenty for a home." },
      { term: "Other cloud accounts", text: "Withings (Sleep Analyzer, ScanWatch, scales), WHOOP and Polar connect the same way as Oura: the owner sets each up once in Studio → Integrations → Cloud accounts, then each person taps Connect on the phone. Fitbit, Garmin and Samsung don't offer this to individuals; they come through Health Connect on Android. For anything else with an API, Studio can take a webhook or read a JSON address." },
      { term: "Oura through Home Assistant instead", text: "If Oura is already in Home Assistant (the Oura integration, HACS), its readiness and temperature sensors can be added as devices in Studio → Devices. It keeps daily numbers rather than the night's detail, so the direct connection is better for Autopilot." },
      { term: "iPhone web app", text: "Add it to the Home Screen (Safari → Share → Add to Home Screen) and open it from that icon: then notifications work (iOS 16.4 or newer). A web app can't read Apple Health, use location in the background or find the bedside dial over Bluetooth; the bed's own sensors, an Oura ring and a calendar link cover most of that." },
      { term: "Work days", text: "Settings → Work days. Your work-night rhythm runs on the nights before a work day and your day-off rhythm on the nights before a day off, so someone who works weekends gets the right one. Picking days by hand in the Rhythm editor stops that rhythm following work days." },
    ],
  },
  {
    id: "ha", title: "Home Assistant and dials",
    items: [
      { term: "Entities in", text: "Any Home Assistant entity can be a device (radar, room sensor, phone tracker, calendar, light)." },
      { term: "MQTT discovery out", text: "Ember & Tide appears in Home Assistant as a climate entity per person, with flash buttons, the current phase, next change, in bed, and per-phase temperatures. Set it up in Studio → Integrations." },
      { term: "Bedside dials", text: "ESPHome dials use those entities: turn for temperature, swipe for flashes, tonight and rhythm. Each dial starts on its owner's side." },
    ],
  },
  {
    id: "notifications", title: "Notifications",
    items: [
      { term: "What they're for", text: "Only useful things: your side is on and nobody's in bed, you're away at pre-cool time, the bed stopped reporting, a sensor went quiet, the morning summary. Many have one-tap buttons (Turn it off, Wait an hour)." },
      { term: "How they arrive", text: "In the Android app (Google push by default, or UnifiedPush/ntfy without Google), in the iPhone Home Screen web app or a browser (Web Push), or through ntfy / Home Assistant. Messages are encrypted for each phone." },
    ],
  },
  {
    id: "people", title: "People and access",
    items: [
      { term: "Owner", text: "Sets everything up: devices, integrations, people. Can change anyone's settings." },
      { term: "Sleeper", text: "Has their own side, rhythm and settings." },
      { term: "Viewer", text: "Can look, not change." },
      { term: "Sharing", text: "Each person decides whether their partner may start flashes or change their temperature (Settings → Sharing)." },
      { term: "Simple or Full app", text: "Simple is just temperature, Hot flash / Cold flash and an easy schedule; Full has charts, the night's story and all settings. Anyone can switch in their own Settings; the owner can also set it in Studio → People & access." },
      { term: "Invites", text: "Studio → People & access makes a one-time link for the partner to set their own password." },
    ],
  },
  {
    id: "guest", title: "Guest mode",
    intro: "Lend one side, or the whole bed, to a guest. Start it from the app (Tonight ⋯ → Guest mode, or Settings) or Studio → Overview.",
    items: [
      { term: "What the guest gets", text: "A plain on / off and a temperature, on the app and on that side's bedside dial (no swiping between screens). No schedule, no flashes, no Autopilot." },
      { term: "Morning off", text: "Between 5 AM and 2 PM, once the side has been empty for 30, 40 or 45 minutes (you choose), it turns itself off. It stays in guest mode until you end it. If the bed can't tell whether anyone is there, it never counts as empty." },
      { term: "Nothing recorded", text: "While a side is in guest mode nothing about that side is stored: no temperatures, heart rate, presence or devices on that side. Only 'guest mode started / turned off / ended' is noted, with no names." },
      { term: "The usual sleeper", text: "The person whose side it is takes a break: no pre-cool, no night, and the guest's night doesn't count towards their Autopilot learning." },
      { term: "Who can change it", text: "The owner starts and ends guest mode. Anyone signed in (not viewers) can turn the guest's side on or off and change its temperature. Swapping sides waits until guest mode ends." },
    ],
  },
  {
    id: "privacy", title: "Data and privacy",
    items: [
      { term: "Local first", text: "Everything runs on your own server at home and talks to the bed directly. Nothing is sent anywhere unless you set it up (push notifications carry only encrypted messages)." },
      { term: "Export", text: "Studio → Data export downloads your raw nights, events and device readings." },
      { term: "Housekeeping", text: "Readings older than 30 days are kept as 5-minute averages and bed data older than 60 days as one row a minute, so the database stays small. Night summaries are kept." },
    ],
  },
  {
    id: "trouble", title: "Troubleshooting",
    items: [
      { term: "Can't reach your server", text: "Check the server is running and the phone is on the home network (or the public address works). In the app, the server link on the sign-in screen lets you change the address." },
      { term: "My side didn't start", text: "The night starts when the bed sees you in bed during 'only start between'. Check Studio → Devices for a sensor that's offline or set aside, and the night's story for what happened. Start now in the Tonight menu always works." },
      { term: "A sensor shows offline", text: "Open it in Studio → Devices for the reason (wrong token, entity gone, server down). It retries by itself and comes back when the device does." },
      { term: "The bed isn't reporting", text: "The topper's connection to the server dropped. It keeps its last temperature; it reconnects by itself when the network comes back." },
    ],
  },
];

function highlight(text: string, q: string): ReactNode {
  if (!q) return text;
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return text;
  return <>{text.slice(0, i)}<mark>{text.slice(i, i + q.length)}</mark>{text.slice(i + q.length)}</>;
}

export default function Help() {
  const [q, setQ] = useState("");
  const [all, setAll] = useState(false);                 // every answer open (searching opens the matches too)
  const loc = useLocation();
  useEffect(() => {
    if (!loc.hash) return;
    const el = document.getElementById(loc.hash.slice(1));
    if (el) window.setTimeout(() => el.scrollIntoView({ block: "start" }), 50);
  }, [loc.hash]);
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return SECTIONS;
    return SECTIONS.map(sec => ({
      ...sec,
      items: sec.title.toLowerCase().includes(s) ? sec.items
        : sec.items.filter(it => it.term.toLowerCase().includes(s) || it.text.toLowerCase().includes(s)),
    })).filter(sec => sec.items.length);
  }, [q]);

  return (
    <div className="help">
      <div className="row help-head">
        <div><span className="k">Studio</span><h1 style={{ fontSize: 26 }}>Help</h1></div>
        <input type="search" className="help-search" placeholder="Search help…" aria-label="Search help" value={q} onChange={e => setQ(e.target.value)} />
      </div>
      <div className="row help-intro"><p className="mute">What everything means, in plain words. Tap a question to see the answer.</p>
        {!q && <button className="pill" aria-pressed={all} onClick={() => setAll(a => !a)}>{all ? "Close all answers" : "Show all answers"}</button>}</div>
      {!q && <nav className="chips help-toc" aria-label="Help sections">
        {SECTIONS.map(s => <a key={s.id} className="chip" href={`#${s.id}`}>{s.title}</a>)}
      </nav>}
      {shown.length === 0 && <div className="box"><p className="mute">Nothing matches "{q}". Try a shorter word.</p></div>}
      {shown.map(sec => (
        <section key={sec.id} id={sec.id} className="box help-sec">
          <h2>{sec.title}</h2>
          {sec.intro && !q && <p className="mute">{sec.intro}</p>}
          <div className="help-faq">
            {sec.items.map(it => (
              // keyed on search / show-all so those open everything, while a single tap only opens one
              <details key={`${it.term}:${q ? "q" : all ? "a" : ""}`} className="help-q" open={!!q || all || undefined}>
                <summary>{highlight(it.term, q.trim())}</summary>
                <p>{highlight(it.text, q.trim())}</p>
              </details>
            ))}
          </div>
          {sec.id === "trouble" && !q && <RecentErrors />}
        </section>
      ))}
    </div>
  );
}

interface ClientErr { ts: number; kind: string; message: string; stack: string; route: string; version: string; screen: string; agent: string; app: boolean }

/** App errors the phones and Studio reported (owner only; others simply don't see this). */
function RecentErrors() {
  const [items, setItems] = useState<ClientErr[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const load = () => api.get<ClientErr[]>("/api/client-errors").then(setItems).catch(() => setItems(null));
  useEffect(() => { void load(); }, []);
  if (items === null) return null;
  return (
    <div className="help-item" style={{ display: "grid", gap: 8 }}>
      <div className="row"><dt>Recent app errors</dt>
        {items.length > 0 && <button className="link" onClick={async () => { try { await api.del("/api/client-errors"); setItems([]); } catch { /* keep the list */ } }}>Clear</button>}</div>
      {items.length === 0 ? <dd className="mute">None reported. If a screen ever breaks on a phone, it shows up here.</dd> :
        <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 6 }}>
          {items.slice(0, 20).map((e, i) => (
            <li key={i} style={{ fontSize: 13 }}>
              <button className="link" style={{ padding: 0, textAlign: "left" }} onClick={() => setOpen(open === i ? null : i)}>
                {new Date(e.ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} · {e.route || "/"} · {e.message || e.kind}
              </button>
              <span className="mute"> · {e.app ? "app" : "browser"} · {e.screen}</span>
              {open === i && <pre className="codeblock" style={{ whiteSpace: "pre-wrap", maxHeight: 240, overflow: "auto" }}>{[e.stack || "(no details)", e.agent, `build ${e.version}`].join("\n")}</pre>}
            </li>
          ))}
        </ul>}
    </div>
  );
}

/** A small "What's this?" link into Help, for use next to Studio controls. */
export function HelpLink({ to, label = "What's this?" }: { to: string; label?: string }) {
  return <Link className="help-link" to={{ pathname: "/studio/help", hash: `#${to}` }}>{label}</Link>;
}
