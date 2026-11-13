import { type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Chevron, useAuth } from "../auth";
import { pushBack } from "../components/back";
import { BrandMark, FinishMark, PersonaArt, PersonaScene, playFinale, type SceneMode, useKeyboardRoom, usePauseWhenHidden } from "../components/FirstRunArt";
import { useApp } from "../ctx";
import { colorKey, localeClock, localeUnits, personaColor, weight, weightUnit } from "../format";
import { askNotifications, pushEnable } from "../native";
import BedSidePicker, { personHalves, type Side, sideLower, sideTitle } from "../components/BedSidePicker";
import PetsEditor, { type Pet } from "../components/PetsEditor";
import WorkDays, { workDaysText } from "../components/WorkDays";
import { BRANDS } from "../wearables";
import type { PersonSettings, PersonView, Rhythm, TrustMore } from "../types";
import PhoneSources from "./PhoneSources";
import WearablePicker, { platform } from "./WearablePicker";
import { TrustPicker } from "./SettingsSleep";
import WebNotifications, { IphoneLimits } from "./WebNotifications";

/**
 * First-time setup: one friendly question per screen, a good answer already picked on each, every one changeable
 * later in Settings. Six core questions ("Step 2 of 6"), then a few optional extras that say so. Works for the owner
 * and for an invited person (household things like pets are shown as already done, and only the owner can change
 * them). Each step saves when you tap Next, through the same APIs Settings uses, so leaving halfway keeps what was
 * chosen. Steps already done (settings.setup_steps) are skipped next time.
 */

export function needsWelcome(me: PersonView | undefined, enabled: boolean, myPerson: string | null | undefined): boolean {
  if (!me || me.settings.welcomed) return false;
  // with sign-in on, only the person themselves sees it (the owner peeking at the partner's view must not use it up)
  return !enabled || myPerson === me.id;
}

type Persona = "ember" | "tide" | "neutral";
type ApMode = "off" | "suggest" | "act";
interface Src { id: string; name: string; role: string; person: string | null; zone: string | null; enabled: boolean }
interface Step { key: string; title: string; body: ReactNode; save?: () => Promise<unknown>; summary?: string; nextLabel?: string; optional?: boolean }

const CORE = ["persona", "you", "style", "times", "autopilot", "notify"];
const CORE_COUNT = CORE.length;

const PERSONAS: { k: Persona; name: string; line: string; temp: string; tempC: string }[] = [
  { k: "ember", name: "Ember", line: "I run cold", temp: "Starts warmer, around 72–78°F", tempC: "Starts warmer, around 22–26°C" },
  { k: "tide", name: "Tide", line: "I run hot", temp: "Starts cooler, around 63–68°F", tempC: "Starts cooler, around 17–20°C" },
  { k: "neutral", name: "In between", line: "It varies", temp: "Starts in the middle, around 68–72°F", tempC: "Starts in the middle, around 20–22°C" },
];
/** "Starts cooler, around 63–68°F" with the range kept on one line */
const tempText = (s: string): ReactNode => { const m = s.match(/^(.*) (\S+–\S+)$/); return m ? <>{m[1]} <span className="nw">{m[2]}</span></> : s; };
const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
/** The names a new household starts with ("Left", "Right"): not someone's name, so don't greet them with it. */
const placeholderName = (n: string) => /^(left|right)$/i.test(n.trim());

const NOTIFY: [keyof PersonSettings["notify"], string, string][] = [
  ["left_on", "My side was left on and nobody's in it", "With a button to turn it off"],
  ["away_precool", "It's about to get my side ready and I'm not home", "Wait, skip tonight, or start anyway"],
  ["empty_side", "My side is ready but I'm not in bed", "Turn it off from anywhere"],
  ["morning", "A short check-in in the morning", "How did it feel? One tap helps it learn"],
];

const toMin = (hm: string) => { const [h, m] = hm.split(":").map(Number); return h * 60 + m; };
const evening = (hm: string) => (toMin(hm) + 12 * 60) % 1440;              // minutes since noon, so 01:00 sorts after 23:00
const fromEvening = (x: number) => { const t = ((x - 12 * 60) % 1440 + 1440) % 1440; return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`; };
const hm12 = (hm: string) => { const [h, m] = hm.split(":").map(Number); return `${((h + 11) % 12) + 1}${m ? `:${String(m).padStart(2, "0")}` : ""} ${h < 12 ? "AM" : "PM"}`; };

export default function Welcome({ me, onDone }: { me: PersonView; onDone: () => void }) {
  const auth = useAuth();
  const { snap } = useApp();
  const owner = !auth.state.enabled || auth.isOwner;
  const s0 = me.settings;
  const done0 = useRef(new Set(s0.setup_steps ?? []));
  const [i, setI] = useState(0);
  const [dir, setDir] = useState<"f" | "b">("f");           // which way the next screen slides in
  const [leaving, setLeaving] = useState(false);             // Start was tapped: the hand-off to Tonight is playing
  const go = (to: number) => { setDir(to < i ? "b" : "f"); setI(to); };
  usePauseWhenHidden();
  useKeyboardRoom();
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // choices (each starts on what's already saved, so Next always works)
  const named = !placeholderName(me.name);
  const [name, setName] = useState(named ? me.name : "");
  const [units, setUnits] = useState<"F" | "C">(s0.welcomed ? s0.units : localeUnits());   // a new person starts on their region's unit
  const [side, setSide] = useState<Side>(me.zone as Side);
  const color = s0.color || colorKey(me);
  const [mode, setMode] = useState<"full" | "simple">(s0.app_mode === "simple" ? "simple" : "full");
  const [persona, setPersona] = useState<Persona>(me.persona);
  const [workDays, setWorkDays] = useState<number[]>(s0.work_days ?? [0, 1, 2, 3, 4]);
  const [rhythms, setRhythms] = useState<Rhythm[] | null>(null);
  const [times, setTimes] = useState<{ work: { bedtime: string; wake: string }; off: { bedtime: string; wake: string } } | null>(null);
  const [ap, setAp] = useState<ApMode>(s0.autopilot?.mode ?? "suggest");
  const [notify, setNotify] = useState(s0.notify);
  const [wearable, setWearable] = useState(s0.wearable ?? "");
  const [trust, setTrust] = useState<TrustMore>(s0.autopilot.trust_more ?? "wearable");
  const [pets, setPets] = useState<Pet[] | null>(null);
  const [sources, setSources] = useState<Src[]>([]);
  const [pushOn, setPushOn] = useState(false);
  const done = useRef(new Set<string>(done0.current));
  const shownName = name.trim() || (named ? me.name : "");

  // hold the Android notification prompt until the notifications step
  useEffect(() => {
    (window as unknown as { __etWelcome?: boolean }).__etWelcome = true;
    return () => { (window as unknown as { __etWelcome?: boolean }).__etWelcome = false; };
  }, []);
  useEffect(() => {
    api.get<Rhythm[]>(`/api/people/${me.id}/rhythms`).then(rs => {
      setRhythms(rs);
      const pick = (k: "work" | "off", fb: number) => rs.find(r => r.nights === k) ?? rs[fb] ?? rs[0];
      const w = pick("work", 0), o = pick("off", 1);
      if (w && o) setTimes({ work: { bedtime: w.schedule.bedtime, wake: w.schedule.wake }, off: { bedtime: o.schedule.bedtime, wake: o.schedule.wake } });
    }).catch(() => setRhythms([]));
    api.get<{ pets: Pet[] }>("/api/pets").then(r => setPets(r.pets)).catch(() => setPets([]));
    api.get<{ sources: Src[] }>("/api/sources").then(r => setSources(r.sources ?? [])).catch(() => setSources([]));
  }, [me.id]);

  const patch = (b: Partial<PersonSettings>) => api.patch(`/api/people/${me.id}/settings`, b);
  const pf = platform();
  const partner = snap?.people.find(p => p.id !== me.id);

  const saveTimes = async () => {
    if (!rhythms || !times) return;
    // fresh copies: saving the work days may have just moved their days (and names) on the server
    const fresh = await api.get<Rhythm[]>(`/api/people/${me.id}/rhythms`);
    for (const k of ["work", "off"] as const) {
      const r = fresh.find(x => x.nights === k) ?? fresh[k === "work" ? 0 : 1];
      if (!r) continue;
      const t = times[k];
      if (t.bedtime === r.schedule.bedtime && t.wake === r.schedule.wake) continue;
      const bed = evening(t.bedtime);
      const sc = { ...r.schedule, bedtime: t.bedtime, wake: t.wake };
      // keep the "getting in bed starts the night" window around the new bedtime
      if (evening(sc.window_start) > bed - 60) sc.window_start = fromEvening(bed - 75);
      if (evening(sc.window_end) < bed + 120) sc.window_end = fromEvening(Math.min(bed + 195, evening(t.wake) - 60));
      await api.put(`/api/people/${me.id}/rhythms/${r.id}`, { ...r, schedule: sc });
    }
  };

  const myCalendars = sources.filter(x => x.role === "calendar" && x.enabled && (x.person === me.id || (!x.person && (!x.zone || x.zone === me.zone))));
  const household = sources.filter(x => x.enabled && !["calendar"].includes(x.role));
  const apText: Record<ApMode, [string, string]> = {
    off: ["Off", "Never changes anything. Your plan runs exactly as set."],
    suggest: ["Suggest", "Leaves a small idea in the morning, like “1° cooler after 3 AM”. You decide."],
    act: ["Act", "Makes small, safe changes during the night and tells you in the morning."],
  };
  const brandName = BRANDS.find(b => b.id === wearable)?.name;
  const hasWearable = !!wearable && wearable !== "none" && wearable !== "other";
  // arrow keys move through a radio group like any radio group (and don't also mean Back / Next)
  const radioKeys = <T extends string>(order: readonly T[], cur: T, set: (v: T) => void) => (e: KeyboardEvent<HTMLDivElement>) => {
    const d = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    e.stopPropagation();
    const next = order[(order.indexOf(cur) + d + order.length) % order.length];
    set(next);
    const group = e.currentTarget;
    window.requestAnimationFrame(() => group.querySelector<HTMLElement>(`[data-v="${next}"]`)?.focus());
  };

  const all: Step[] = [
    { key: "hi", title: "Welcome", nextLabel: "Let's start", body: <>
      <div className="wl-brand"><BrandMark size="lg" /><span className="wl-brandname">Ember &amp; Tide</span></div>
      <h1>{named ? `Hi ${me.name}` : "Hi there"}</h1>
      <p className="wl-lead">Your side of the bed follows a gentle temperature plan through the night, starting when you get in.</p>
      <p className="mute">{CORE_COUNT} quick questions, about two minutes. A good answer is already picked on each, and you can change any of them later.</p>
    </> },
    // the persona comes first: its colours then carry through the rest of setup
    { key: "persona", title: "Warm or cool", summary: persona === "ember" ? "Ember, runs cold" : persona === "tide" ? "Tide, runs hot" : "In between",
      save: () => api.put(`/api/people/${me.id}/profile`, { persona, persona_temps: persona !== me.persona }).then(() => {}),
      body: <>
        <h1>How do you usually sleep?</h1>
        <p className="mute">This sets your starting temperatures. It learns from there.</p>
        <div className="wl-pgroup" role="radiogroup" aria-label="How you usually sleep" onKeyDown={radioKeys(PERSONAS.map(x => x.k), persona, setPersona)}>
          <div className="wl-pgrid">
            {PERSONAS.slice(0, 2).map(x => (
              <button key={x.k} type="button" role="radio" aria-checked={persona === x.k} tabIndex={persona === x.k ? 0 : -1}
                className={`wl-pcard pc-${x.k}`} data-v={x.k} onClick={() => setPersona(x.k)}>
                <PersonaArt kind={x.k} />
                <span className="pc-name">{x.name}<span className="pc-tick" aria-hidden>✓</span></span>
                <span className="pc-line">{x.line}</span>
                <span className="pc-temp">{tempText(units === "C" ? x.tempC : x.temp)}</span>
              </button>))}
          </div>
          <button type="button" role="radio" aria-checked={persona === "neutral"} tabIndex={persona === "neutral" ? 0 : -1}
            className="wl-pmid" data-v="neutral" onClick={() => setPersona("neutral")}>
            <PersonaArt kind="neutral" />
            <span className="pm-text"><span className="pc-name">{PERSONAS[2].name}<span className="mute"> · {PERSONAS[2].line}</span></span>
              <span className="pc-temp">{tempText(units === "C" ? PERSONAS[2].tempC : PERSONAS[2].temp)}</span></span>
            <span className="pc-tick" aria-hidden>✓</span>
          </button>
        </div>
      </> },
    { key: "you", title: "About you", summary: `${shownName ? `${shownName} · ${sideLower(side)}` : sideTitle(side)} · °${units}`,
      save: async () => {
        if (name.trim() && name.trim() !== me.name) await api.put(`/api/people/${me.id}/profile`, { name: name.trim() });
        const clockAuto = !s0.clock || s0.clock === "auto";          // first time: pin the device's own clock style
        if (units !== s0.units || clockAuto) await patch({ units, ...(clockAuto ? { clock: localeClock() } : {}) });
        // tapped the other half: swap with the partner, or (alone) just move to that side
        const now = snap?.people.find(p => p.id === me.id)?.zone ?? me.zone;
        if (side !== now) {
          if (partner && partner.zone === side) await api.post("/api/household/swap-sides");
          else await api.put(`/api/people/${me.id}/profile`, { zone: side });
        }
      },
      body: <>
        <h1>Your name and your side</h1>
        <label className="wl-field"><span>Your first name</span>
          <input type="text" value={name} maxLength={40} placeholder={named ? undefined : "For example Sam"} autoComplete="given-name" autoCapitalize="words"
            autoCorrect="off" spellCheck={false} inputMode="text" enterKeyHint="next" onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); } }} /></label>
        <BedSidePicker value={side} onChange={v => v !== "both" && setSide(v)}
          halves={personHalves(side, { name: shownName || "You", color: `var(--pc-${color})` }, partner ? { name: partner.name, color: personaColor(partner) } : null)} />
        {partner && side !== (snap?.people.find(p => p.id === me.id)?.zone ?? me.zone) && <small className="mute">{partner.name} moves to the other side when you tap Next.</small>}
        <div className="wl-units"><span id="wl-units">Temperatures in</span>
          <div className="seg" role="group" aria-labelledby="wl-units">{(["F", "C"] as const).map(k => <button key={k} type="button" aria-pressed={units === k} onClick={() => setUnits(k)}>°{k}</button>)}</div></div>
      </> },
    { key: "style", title: "App style", summary: mode === "simple" ? "Simple app" : "Full app", save: () => patch({ app_mode: mode }), body: <>
      <h1>How much do you want to see?</h1>
      <p className="mute">You can switch any time in Settings.</p>
      <div className="wl-cards" role="radiogroup" aria-label="App style" onKeyDown={radioKeys(["simple", "full"] as const, mode, setMode)}>
        <button type="button" role="radio" aria-checked={mode === "simple"} tabIndex={mode === "simple" ? 0 : -1} className="wl-card" data-v="simple" data-key="1" onClick={() => setMode("simple")}>
          <span className="wl-prev wl-prev-simple" aria-hidden><b>68°</b><i /><i /></span>
          <span className="wl-name">Simple</span>
          <span className="mute">Your temperature, a hot or cold flash button, and your bedtime. Nothing else.</span>
        </button>
        <button type="button" role="radio" aria-checked={mode === "full"} tabIndex={mode === "full" ? 0 : -1} className="wl-card" data-v="full" data-key="2" onClick={() => setMode("full")}>
          <span className="wl-prev wl-prev-full" aria-hidden><svg viewBox="0 0 60 24"><path d="M0 8 C12 8 14 18 24 18 S44 14 52 6 L60 4" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" /></svg><i /></span>
          <span className="wl-name">Full</span>
          <span className="mute">Also charts, the story of each night, and every setting.</span>
        </button>
      </div>
    </> },
    // bedtimes and work days together: "before a work day" only means something next to the work days
    { key: "times", title: "Bedtime", summary: times ? `${workDaysText(workDays)}: ${hm12(times.work.bedtime)}–${hm12(times.work.wake)} · days off ${hm12(times.off.bedtime)}–${hm12(times.off.wake)}` : workDaysText(workDays),
      save: async () => { await patch({ work_days: workDays }); done.current.add("workdays"); await saveTimes(); }, body: <>
        <h1>When do you usually sleep?</h1>
        <p className="mute">It gets your side ready before bedtime and warms it gently before you wake. Going to bed early or late is fine: the night starts when you get in.</p>
        {!times ? <p className="mute">Loading…</p> : <div className="wl-times">
          {(["work", "off"] as const).filter(k => k === "work" ? workDays.length > 0 : workDays.length < 7).map(k => (
            <div key={k} className="card wl-time" role="group" aria-label={k === "work" ? "Before a work day" : "Before a day off"}>
              <b>{k === "work" ? "Before a work day" : "Before a day off"}</b>
              <label><span>Bedtime</span><input type="time" value={times[k].bedtime} onChange={e => e.target.value && setTimes({ ...times, [k]: { ...times[k], bedtime: e.target.value } })} /></label>
              <label><span>Wake up</span><input type="time" value={times[k].wake} onChange={e => e.target.value && setTimes({ ...times, [k]: { ...times[k], wake: e.target.value } })} /></label>
            </div>))}
        </div>}
        <div className="wl-wd"><span className="wl-sub">Your work days</span>
          <WorkDays value={workDays} onChange={setWorkDays} /></div>
      </> },
    { key: "autopilot", title: "Autopilot", summary: apText[ap][0], save: () => patch({ autopilot: { ...s0.autopilot, mode: ap } }), body: <>
      <h1>Should it fine‑tune your nights?</h1>
      <p className="mute">Autopilot learns what a good night looks like for you. For the first week it only watches.</p>
      <div className="wl-cards" role="radiogroup" aria-label="Autopilot" onKeyDown={radioKeys(["off", "suggest", "act"] as const, ap, setAp)}>
        {(["off", "suggest", "act"] as const).map(k => (
          <button key={k} type="button" role="radio" aria-checked={ap === k} tabIndex={ap === k ? 0 : -1} data-v={k} className="wl-card wl-ap" onClick={() => setAp(k)}>
            <span className="wl-ap-head"><span className="wl-name">{apText[k][0]}</span>{k === "suggest" && <span className="wl-tag">Recommended</span>}
              <span className="wl-radio" aria-hidden /></span>
            <span className="mute">{apText[k][1]}</span>
          </button>))}
      </div>
    </> },
    { key: "notify", title: "Notifications", summary: `${NOTIFY.filter(([k]) => notify?.[k] ?? true).length} of ${NOTIFY.length} on${pushOn ? " · this phone" : ""}`,
      save: () => patch({ notify }), body: <>
        <h1>What should it tell you?</h1>
        <p className="mute">Only these, and only when they happen.</p>
        <div className="card list">
          {NOTIFY.map(([k, label, sub]) => (
            <label className="row" key={k}><span>{label}<br /><small className="mute">{sub}</small></span>
              <input type="checkbox" className="toggle" checked={notify?.[k] ?? true} onChange={e => setNotify({ ...notify, [k]: e.target.checked })} /></label>))}
        </div>
        {pf === "android-app" ? <AndroidPush onOn={() => setPushOn(true)} /> : <WebNotifications person={me.id} onState={setPushOn} />}
      </> },
    ...(pf === "android-app" ? [{ key: "phone", title: "This phone", optional: true, summary: "Settings → From this phone", body: <>
      <h1>Help from this phone</h1>
      <p className="mute">Your phone can give the bed a few hints. Each is off until you switch it on. Only times and distances go to your own server, never where you are or what's in your calendar.</p>
      <PhoneSources inline />
    </> }] : pf === "ios-web" ? [{ key: "phone", title: "iPhone", optional: true, summary: "iPhone web app", body: <>
      <h1>On iPhone</h1>
      <IphoneLimits />
    </> }] : []),
    { key: "wearable", title: "Watch or ring", optional: true, summary: brandName ?? "None",
      save: () => patch({ wearable: wearable || "", autopilot: { ...s0.autopilot, mode: ap, trust_more: trust } }), body: <>
        <h1>Do you wear a watch or ring to bed?</h1>
        <p className="mute">It's not needed: the bed can tell when you're asleep by itself. A watch or ring adds a little more.</p>
        <WearablePicker person={me.id} value={wearable} onChange={setWearable} collapsible />
        {hasWearable && <div className="wl-trust">
          <span className="wl-sub">If it and the bed disagree, trust</span>
          <TrustPicker value={trust} onChange={setTrust} />
        </div>}
      </> },
    ...((owner || (pets && pets.length > 0)) ? [{ key: "pets", title: "Pets", optional: true, summary: pets?.length ? `${pets.length} pet${pets.length > 1 ? "s" : ""}` : "None", body: <>
      <h1>{pets?.length ? "Pets on the bed" : "Any pets on the bed?"}</h1>
      <p className="mute">A cat or dog on the bed can look like a person to the bed. Pets you add here are ignored.</p>
      {owner ? <PetsEditor weightUnit={weightUnit({ units, weight_units: s0.weight_units })} compact onCount={() => api.get<{ pets: Pet[] }>("/api/pets").then(r => setPets(r.pets)).catch(() => {})} />
        : <><p className="mute">Already on the bed:</p>
          <ul className="wl-pets">{pets!.map(p => <li key={p.id ?? p.name} className="card"><b style={{ fontWeight: 500 }}>{p.name}</b>
            <span className="mute"> · {p.kind === "dog" ? "Dog" : p.kind === "cat" ? "Cat" : "Pet"} · {weight(p.weight_kg, weightUnit({ units, weight_units: s0.weight_units }))} · {p.side === "either" ? "either side" : p.side === "both" ? "both sides" : sideLower(p.side)}</span></li>)}</ul>
          <p className="faint" style={{ fontSize: 13 }}>Ask the person who set this up to change them.</p></>}
    </> }] : []),
  ];
  // skip steps already done in an earlier run (the first and last always show)
  const steps = all.filter(st => st.key === "hi" || !done0.current.has(st.key));
  const finishStep: Step = { key: "done", title: "You're set", nextLabel: saving ? "Saving…" : "Start", body: <>
    <FinishMark />
    <h1>You're all set{shownName ? `, ${shownName}` : ""}</h1>
    <p className="wl-lead">Tonight starts by itself when you get into bed.</p>
    <ul className="wl-sum">
      {all.filter(st => st.summary).map(st => {
        const at = steps.findIndex(x => x.key === st.key);
        return <li key={st.key}><span><small className="mute">{st.title}</small><br />{st.summary}</span>
          {at >= 0 ? <button type="button" className="link" onClick={() => go(at)} aria-label={`Change ${st.title.toLowerCase()}`}>Change</button> : <span className="faint">in Settings</span>}</li>;
      })}
      {(myCalendars.length > 0 || household.length > 0) && <li><span><small className="mute">Already set up in this home</small><br />
        {[myCalendars.length ? `your calendar (${myCalendars.map(x => x.name).join(", ")})` : "", household.length ? `${household.length} device${household.length > 1 ? "s" : ""}` : ""].filter(Boolean).join(" · ")}</span>
        <span className="faint">done</span></li>}
    </ul>
    <p className="mute wl-later"><b>Whenever you like, in Settings:</b> pick your colour, and check with the bed that it sees you on your side.</p>
    {owner
      ? <p className="mute">More tools, and a full explanation of everything, are in{" "}
        <a href="/studio/help" onClick={async e => { e.preventDefault(); await finish(false); location.href = "/studio/help"; }}>Studio → Help</a>.</p>
      : <p className="mute">Questions? <a href="/studio/help" onClick={async e => { e.preventDefault(); await finish(false); location.href = "/studio/help"; }}>Studio → Help</a> explains everything in plain words.</p>}
  </> };
  const flow = [...steps, finishStep];
  const cur = flow[Math.min(i, flow.length - 1)];
  const last = cur.key === "done";
  const coreSteps = flow.filter(st => CORE.includes(st.key));
  const extraSteps = flow.filter(st => st.optional);

  async function finish(fade = true) {
    setSaving(true);
    try { await patch({ welcomed: true, setup_steps: [...done.current].slice(0, 30) }); } catch { /* offline: it shows again next time */ }
    (window as unknown as { __etWelcome?: boolean }).__etWelcome = false;
    setSaving(false);
    if (!fade) { onDone(); return; }
    setLeaving(true);
    if (reducedMotion()) { window.setTimeout(onDone, 220); return; }        // a plain cross-fade (the .leaving class)
    // the hand-off in their colours: Tonight is swapped in underneath once the screen is covered. Never blocks:
    // playFinale resolves straight away if the page is hidden or anything goes wrong, and on a timer otherwise.
    let handed = false;
    const hand = () => { if (!handed) { handed = true; onDone(); } };
    const guard = window.setTimeout(hand, 1600);
    try { await playFinale(persona, shownName); } catch { /* go straight on */ }
    window.clearTimeout(guard);
    hand();
  }
  const next = async () => {
    if (last) return finish();
    setErr(null);
    if (cur.save) {
      setSaving(true);
      try { await cur.save(); done.current.add(cur.key); }
      catch (e) { setSaving(false); setErr(`Couldn't save that: ${(e as Error).message}. You can skip it and change it later in Settings.`); return; }
      setSaving(false);
    } else if (cur.key !== "hi") done.current.add(cur.key);
    go(i + 1);
  };
  const back = () => { setErr(null); go(i - 1); };
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => { body.current?.scrollTo?.({ top: 0 }); body.current?.focus({ preventScroll: true }); }, [i]);
  // Android back (and Backspace / Esc on a keyboard): one screen back at a time, nothing while saving
  useEffect(() => (i > 0 && !saving && !leaving ? pushBack(back) : undefined), [i, saving, leaving]);   // eslint-disable-line react-hooks/exhaustive-deps

  // "more below": a soft fade and a small hint while there's more to scroll to (the step's content can change size)
  const [more, setMore] = useState(false);
  useEffect(() => {
    const el = body.current;
    if (!el) return;
    const check = () => setMore(el.scrollHeight - el.clientHeight - el.scrollTop > 24);
    check();
    el.addEventListener("scroll", check, { passive: true });
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(check) : null;
    ro?.observe(el);
    if (el.firstElementChild) ro?.observe(el.firstElementChild);
    const t = window.setTimeout(check, 700);                    // after the step's entrance animation
    return () => { el.removeEventListener("scroll", check); ro?.disconnect(); clearTimeout(t); };
  }, [i]);

  // the scene: both colours on the first screen, then the chosen person's, moving only where it matters
  const sceneMode: SceneMode = cur.key === "hi" ? "brand" : cur.key === "persona" ? "hero" : last ? "done" : "quiet";
  const coreAt = coreSteps.findIndex(st => st.key === cur.key);
  const extraAt = extraSteps.findIndex(st => st.key === cur.key);
  const where = coreAt >= 0 ? `Step ${coreAt + 1} of ${coreSteps.length}` : extraAt >= 0 ? `Optional · ${extraAt + 1} of ${extraSteps.length}` : last ? "Done" : "";
  return (
    <div className={`screen wl${leaving ? " leaving" : ""}`} data-pp={persona} role="dialog" aria-modal="true" aria-label="First-time setup">
      <PersonaScene persona={cur.key === "hi" ? "neutral" : persona} mode={sceneMode} />
      <div className="wl-top">
        {cur.key !== "hi" && <div className="wl-prog" role="group" aria-label={where ? `${where}: ${cur.title}` : cur.title}>
          <span className="wl-dots" aria-hidden>
            {coreSteps.map(st => { const k = flow.indexOf(st); return <i key={st.key} className={k === i ? "on" : k < i ? "past" : ""} />; })}
            {extraSteps.length > 0 && <span className="wl-dots-gap" />}
            {extraSteps.map(st => { const k = flow.indexOf(st); return <i key={st.key} className={`opt${k === i ? " on" : k < i ? " past" : ""}`} />; })}
          </span>
          <span className="wl-count" aria-hidden>{where}</span>
        </div>}
        {!last && <button type="button" className="link wl-skipall" data-key="s" disabled={saving} onClick={() => { setErr(null); go(flow.length - 1); }}>Skip setup</button>}
      </div>
      <div className={`wl-mid${more ? " more" : ""}`}>
        <div className="wl-body" ref={body} tabIndex={-1}>
          <section key={cur.key} className={`wl-step wl-${cur.key}`} data-dir={dir}>
            {cur.optional && <p className="wl-eyebrow">Optional · you can do this later</p>}
            {cur.body}
            {err && <p className="warn-text" role="alert">{err}</p>}
          </section>
        </div>
        <button type="button" className="wl-morehint" tabIndex={-1} aria-hidden onClick={() => body.current?.scrollBy({ top: body.current.clientHeight * 0.7, behavior: reducedMotion() ? "auto" : "smooth" })}>
          More below<svg viewBox="0 0 24 24" aria-hidden><path d="M6 9.5 12 15.5 18 9.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
      </div>
      <div className="wl-nav">
        {i > 0 ? <button type="button" className="btn wl-back" data-key="arrowleft" disabled={saving || leaving} onClick={back}><Chevron />Back</button> : <span />}
        {cur.optional && <button type="button" className="link wl-skip" disabled={saving || leaving} onClick={() => { setErr(null); go(i + 1); }}>Skip</button>}
        <button type="button" className="btn pri wl-next" data-key="enter arrowright" disabled={saving || leaving} onClick={next}>{saving && !last ? "Saving…" : cur.nextLabel ?? "Next"}</button>
      </div>
    </div>
  );
}

/** Android app: turn on its native notifications (Google push, or ntfy) right from setup. */
function AndroidPush({ onOn }: { onOn: () => void }) {
  const [st, setSt] = useState<"idle" | "busy" | "on" | "off">("idle");
  const go = async () => {
    setSt("busy");
    try {
      await askNotifications();
      try { localStorage.setItem("et-asked-notify", "1"); } catch { /* private mode */ }
      const r = await pushEnable("auto");
      if (r.registered || r.allowed) { setSt("on"); onOn(); } else setSt("off");
    } catch { setSt("off"); }
  };
  return <div className="webpush">
    <button type="button" className="btn pri" disabled={st === "busy" || st === "on"} onClick={go}>{st === "on" ? "Notifications on ✓" : st === "busy" ? "Asking…" : "Turn on notifications"}</button>
    {st === "off" && <p className="mute">Not on yet. You can try again in Settings → Notifications on this phone.</p>}
  </div>;
}
