import { type ReactNode, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { useApp } from "../ctx";
import { colorKey, COLORS, personaColor, weight, weightUnit } from "../format";
import { askNotifications, pushEnable } from "../native";
import BedSidePicker, { personHalves, type Side, SideCheck, sideLower } from "../components/BedSidePicker";
import PetsEditor, { type Pet } from "../components/PetsEditor";
import WorkDays, { workDaysText } from "../components/WorkDays";
import { BRANDS } from "../wearables";
import type { ColorKey, PersonSettings, PersonView, Rhythm, TrustMore } from "../types";
import PhoneSources from "./PhoneSources";
import WearablePicker, { platform } from "./WearablePicker";
import { TrustPicker } from "./SettingsSleep";
import WebNotifications, { IphoneLimits } from "./WebNotifications";

/**
 * First-time setup: one friendly decision per screen, every one skippable and changeable later in Settings. Works for
 * the owner and for an invited person (household things like pets are shown as already done, and only the owner can
 * change them). Each step saves when you tap Next, through the same APIs Settings uses, so leaving halfway keeps what
 * was chosen. Steps already done (settings.setup_steps) are skipped next time.
 */

export function needsWelcome(me: PersonView | undefined, enabled: boolean, myPerson: string | null | undefined): boolean {
  if (!me || me.settings.welcomed) return false;
  // with sign-in on, only the person themselves sees it (the owner peeking at the partner's view must not use it up)
  return !enabled || myPerson === me.id;
}

type Persona = "ember" | "tide" | "neutral";
type ApMode = "off" | "suggest" | "act";
interface Src { id: string; name: string; role: string; person: string | null; zone: string | null; enabled: boolean }
interface Step { key: string; title: string; body: ReactNode; save?: () => Promise<unknown>; summary?: string; nextLabel?: string }

const NOTIFY: [keyof PersonSettings["notify"], string, string][] = [
  ["left_on", "My side was left on with nobody in it", "With a Turn it off button"],
  ["away_precool", "Pre-cool is due and I'm not home", "Wait an hour, off tonight, or start anyway"],
  ["empty_side", "My side is ready but empty", "Turn it off from anywhere"],
  ["morning", "A short morning check-in", "How did it feel? One tap helps it learn"],
];

const toMin = (hm: string) => { const [h, m] = hm.split(":").map(Number); return h * 60 + m; };
const evening = (hm: string) => (toMin(hm) + 12 * 60) % 1440;              // minutes since noon, so 01:00 sorts after 23:00
const fromEvening = (x: number) => { const t = ((x - 12 * 60) % 1440 + 1440) % 1440; return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`; };
const hm12 = (hm: string) => { const [h, m] = hm.split(":").map(Number); return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`; };

export default function Welcome({ me, onDone }: { me: PersonView; onDone: () => void }) {
  const auth = useAuth();
  const { snap } = useApp();
  const owner = !auth.state.enabled || auth.isOwner;
  const s0 = me.settings;
  const done0 = useRef(new Set(s0.setup_steps ?? []));
  const [i, setI] = useState(0);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // choices
  const [name, setName] = useState(me.name);
  const [units, setUnits] = useState<"F" | "C">(s0.units);
  const [side, setSide] = useState<Side>(me.zone as Side);
  const [color, setColor] = useState<ColorKey>(s0.color || colorKey(me));
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
    // fresh copies: the work-days step may have just moved their days (and names) on the server
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
    off: ["Off", "Your side follows your plan exactly. Nothing changes by itself."],
    suggest: ["Suggest", "Shows small ideas in the morning, like \"1° cooler in the deep part\". You decide."],
    act: ["Act", "Makes small, safe changes during the night and tells you in the morning."],
  };
  const brandName = BRANDS.find(b => b.id === wearable)?.name;

  const all: Step[] = [
    { key: "hi", title: "Welcome", nextLabel: "Let's start", body: <>
      <div className="brandmark wl-mark" aria-hidden><span className="e" /><span className="t" /></div>
      <h1>Hi {me.name}</h1>
      <p className="wl-lead">Your side of the bed follows a gentle temperature plan through the night, starting when you get in.</p>
      <p className="mute">A few quick questions set it up for you. Every one can be skipped and changed later in Settings.</p>
    </> },
    { key: "you", title: "This is you", summary: `${name.trim() || me.name} · ${sideLower(side)} · °${units}`,
      save: async () => {
        if (name.trim() && name.trim() !== me.name) await api.put(`/api/people/${me.id}/profile`, { name: name.trim() });
        await patch({ units, color });
        // tapped the other half: swap with the partner, or (alone) just move to that side
        const now = snap?.people.find(p => p.id === me.id)?.zone ?? me.zone;
        if (side !== now) {
          if (partner && partner.zone === side) await api.post("/api/household/swap-sides");
          else await api.put(`/api/people/${me.id}/profile`, { zone: side });
        }
      },
      body: <>
        <h1>This is you</h1>
        <label className="wl-field"><span>Your name</span><input type="text" value={name} maxLength={40} autoComplete="given-name" onChange={e => setName(e.target.value)} /></label>
        <BedSidePicker value={side} onChange={v => v !== "both" && setSide(v)}
          halves={personHalves(side, { name: name.trim() || me.name, color: `var(--pc-${color})` }, partner ? { name: partner.name, color: personaColor(partner) } : null)} />
        {partner && side !== (snap?.people.find(p => p.id === me.id)?.zone ?? me.zone) && <small className="mute">{partner.name} moves to the other side when you tap Next.</small>}
        <div className="wl-row"><span>Your colour</span>
          <div className="swatches" role="radiogroup" aria-label="Your colour">
            {COLORS.map(c => <button key={c.key} role="radio" aria-checked={color === c.key} aria-label={c.name} title={c.name}
              style={{ background: `var(--pc-${c.key})` }} onClick={() => setColor(c.key)}>{color === c.key ? "✓" : ""}</button>)}
          </div></div>
        <div className="wl-row"><span>Temperatures in</span>
          <div className="seg" style={{ width: 140 }}>{(["F", "C"] as const).map(k => <button key={k} aria-pressed={units === k} onClick={() => setUnits(k)}>°{k}</button>)}</div></div>
      </> },
    { key: "check", title: "Check your side", nextLabel: "Next", body: <>
      <h1>Check it with the bed</h1>
      <p className="mute">Optional: lie on your side for a moment and the bed shows where it sees you. Or skip it; Settings has the same check.</p>
      <SideCheck personId={me.id} />
    </> },
    { key: "style", title: "App style", summary: mode === "simple" ? "Simple app" : "Full app", save: () => patch({ app_mode: mode }), body: <>
      <h1>How do you want the app?</h1>
      <div className="wl-cards" role="radiogroup" aria-label="App style">
        <button role="radio" aria-checked={mode === "simple"} className="wl-card" data-key="1" onClick={() => setMode("simple")}>
          <span className="wl-prev wl-prev-simple" aria-hidden><b>68°</b><i /><i /></span>
          <span className="wl-name">Simple</span>
          <span className="mute">Just your temperature, Hot flash / Cold flash, and your bedtime.</span>
        </button>
        <button role="radio" aria-checked={mode === "full"} className="wl-card" data-key="2" onClick={() => setMode("full")}>
          <span className="wl-prev wl-prev-full" aria-hidden><svg viewBox="0 0 60 24"><path d="M0 8 C12 8 14 18 24 18 S44 14 52 6 L60 4" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" /></svg><i /></span>
          <span className="wl-name">Full</span>
          <span className="mute">Charts, the story of your night, and all the settings.</span>
        </button>
      </div>
    </> },
    { key: "persona", title: "Warm or cool", summary: persona === "ember" ? "Ember, runs cold" : persona === "tide" ? "Tide, runs hot" : "In between",
      save: () => api.put(`/api/people/${me.id}/profile`, { persona, persona_temps: persona !== me.persona }).then(() => {}),
      body: <>
        <h1>How do you usually sleep?</h1>
        <p className="mute">This picks your starting temperatures. It learns from there.</p>
        <div className="wl-cards" role="radiogroup" aria-label="How you sleep">
          {([["ember", "Ember", "I run cold", "Starts warmer, around 72–78°F", "var(--warm)"], ["tide", "Tide", "I run hot", "Starts cooler, around 63–68°F", "var(--cool)"],
             ["neutral", "In between", "It varies", "Starts in the middle, around 68–72°F", "var(--acc)"]] as const).map(([k, n, l, d, c]) => (
            <button key={k} role="radio" aria-checked={persona === k} className="wl-card wl-persona" onClick={() => setPersona(k)}>
              <span className="wl-orb" aria-hidden style={{ background: c }} />
              <span className="wl-name">{n} <span className="mute" style={{ fontWeight: 400 }}>· {l}</span></span>
              <span className="mute">{d}</span>
            </button>))}
        </div>
      </> },
    { key: "workdays", title: "Work days", summary: workDaysText(workDays), save: () => patch({ work_days: workDays }), body: <>
      <h1>Which days do you work?</h1>
      <p className="mute">The days you get up for something fixed: work, school, anything early. The night before one of them can have an earlier bedtime.</p>
      <WorkDays value={workDays} onChange={setWorkDays} />
    </> },
    { key: "times", title: "Bedtime", summary: times ? `Work nights ${hm12(times.work.bedtime)}–${hm12(times.work.wake)} · days off ${hm12(times.off.bedtime)}–${hm12(times.off.wake)}` : undefined,
      save: saveTimes, body: <>
        <h1>When do you sleep?</h1>
        <p className="mute">It cools your side before bedtime and warms it gently before you wake.</p>
        {!times ? <p className="mute">Loading…</p> : <div className="wl-times">
          {(["work", "off"] as const).filter(k => k === "work" ? workDays.length > 0 : workDays.length < 7).map(k => (
            <div key={k} className="card wl-time">
              <b style={{ fontWeight: 500 }}>{k === "work" ? "Before a work day" : "Before a day off"}</b>
              <label><span>Bedtime</span><input type="time" value={times[k].bedtime} onChange={e => e.target.value && setTimes({ ...times, [k]: { ...times[k], bedtime: e.target.value } })} /></label>
              <label><span>Wake up</span><input type="time" value={times[k].wake} onChange={e => e.target.value && setTimes({ ...times, [k]: { ...times[k], wake: e.target.value } })} /></label>
            </div>))}
        </div>}
        <p className="faint" style={{ fontSize: 13 }}>Getting into bed early or late is fine: the night starts when you're actually in bed.</p>
      </> },
    { key: "autopilot", title: "Autopilot", summary: apText[ap][0], save: () => patch({ autopilot: { ...s0.autopilot, mode: ap } }), body: <>
      <h1>Autopilot</h1>
      <p className="wl-lead">For your first 7 nights it just watches, to learn what a normal night looks like for you.</p>
      <p className="mute">After that it stays quiet while you're inside your normal, and only speaks up when something's clearly off.</p>
      <div className="wl-cards" role="radiogroup" aria-label="Autopilot">
        {(["off", "suggest", "act"] as const).map(k => (
          <button key={k} role="radio" aria-checked={ap === k} className="wl-card wl-ap" onClick={() => setAp(k)}>
            <span className="wl-name">{apText[k][0]}{k === "suggest" && <span className="mute" style={{ fontWeight: 400 }}> · recommended</span>}</span>
            <span className="mute">{apText[k][1]}</span>
          </button>))}
      </div>
    </> },
    { key: "notify", title: "Notifications", summary: `${NOTIFY.filter(([k]) => notify?.[k] ?? true).length} of ${NOTIFY.length} on${pushOn ? " · this phone" : ""}`,
      save: () => patch({ notify }), body: <>
        <h1>What should it tell you?</h1>
        <div className="card list">
          {NOTIFY.map(([k, label, sub]) => (
            <div className="row" key={k}><span>{label}<br /><small className="mute">{sub}</small></span>
              <input type="checkbox" className="toggle" checked={notify?.[k] ?? true} onChange={e => setNotify({ ...notify, [k]: e.target.checked })} aria-label={label} /></div>))}
        </div>
        {pf === "android-app" ? <AndroidPush onOn={() => setPushOn(true)} /> : <WebNotifications person={me.id} onState={setPushOn} />}
      </> },
    ...(pf === "android-app" ? [{ key: "phone", title: "This phone", summary: "Optional · Settings → From this phone", body: <>
      <h1>Help from this phone</h1>
      <p className="mute">Each one is optional and asks for its permission when you switch it on. Only times and distances are sent, never where you are or what's in your calendar.</p>
      <PhoneSources />
    </> }] : pf === "ios-web" ? [{ key: "phone", title: "iPhone", summary: "iPhone web app", body: <>
      <h1>On iPhone</h1>
      <IphoneLimits />
    </> }] : []),
    { key: "wearable", title: "Watch or ring", summary: brandName ?? "None", nextLabel: wearable ? "Next" : "Do it later",
      save: () => patch({ wearable: wearable || "", autopilot: { ...s0.autopilot, mode: ap, trust_more: trust } }), body: <>
        <h1>Do you wear a watch or ring at night?</h1>
        <WearablePicker person={me.id} value={wearable} onChange={setWearable} />
        {wearable && wearable !== "none" && <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
          <span>When it and the bed disagree, trust</span>
          <TrustPicker value={trust} onChange={setTrust} />
        </div>}
      </> },
    ...((owner || (pets && pets.length > 0)) ? [{ key: "pets", title: "Pets", summary: pets?.length ? `${pets.length} pet${pets.length > 1 ? "s" : ""}` : "None", body: <>
      <h1>{pets?.length ? "Pets on the bed" : "Any pets on the bed?"}</h1>
      <p className="wl-lead">A cat or dog on the bed can look like a person to the bed's sensors. Known pets are ignored.</p>
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
    <h1>You're set</h1>
    <p className="wl-lead">Sleep well{name.trim() ? `, ${name.trim()}` : ""}.</p>
    <ul className="wl-sum">
      {all.filter(st => st.summary).map(st => {
        const at = steps.findIndex(x => x.key === st.key);
        return <li key={st.key}><span><small className="mute">{st.title}</small><br />{st.summary}</span>
          {at >= 0 ? <button className="link" onClick={() => setI(at)}>Edit</button> : <span className="faint">in Settings</span>}</li>;
      })}
      {(myCalendars.length > 0 || household.length > 0) && <li><span><small className="mute">Already set up in this home</small><br />
        {[myCalendars.length ? `your calendar (${myCalendars.map(x => x.name).join(", ")})` : "", household.length ? `${household.length} device${household.length > 1 ? "s" : ""}` : ""].filter(Boolean).join(" · ")}</span>
        <span className="faint">done</span></li>}
    </ul>
    {owner
      ? <p className="mute">More tools, and a full explanation of everything, are in{" "}
        <a href="/studio/help" onClick={async e => { e.preventDefault(); await finish(); location.href = "/studio/help"; }}>Studio → Help</a>.</p>
      : <p className="mute">Questions? <a href="/studio/help" onClick={async e => { e.preventDefault(); await finish(); location.href = "/studio/help"; }}>Studio → Help</a> explains everything in plain words.</p>}
  </> };
  const flow = [...steps, finishStep];
  const cur = flow[Math.min(i, flow.length - 1)];
  const last = cur.key === "done";

  async function finish() {
    setSaving(true);
    try { await patch({ welcomed: true, setup_steps: [...done.current].slice(0, 30) }); } catch { /* offline: it shows again next time */ }
    (window as unknown as { __etWelcome?: boolean }).__etWelcome = false;
    setSaving(false);
    onDone();
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
    setI(i + 1);
  };
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => { body.current?.scrollTo?.({ top: 0 }); body.current?.focus({ preventScroll: true }); }, [i]);

  return (
    <div className="screen wl" role="dialog" aria-modal="true" aria-label="First-time setup">
      <div className="wl-top">
        <span className="wl-dots" aria-label={`Step ${i + 1} of ${flow.length}: ${cur.title}`}>
          {flow.map((st, k) => <i key={st.key} className={k === i ? "on" : k < i ? "past" : ""} />)}
        </span>
        {!last && <button className="link" data-key="s" disabled={saving} onClick={() => setI(flow.length - 1)}>Skip setup</button>}
      </div>
      <div className="wl-body" ref={body} tabIndex={-1}>
        <section key={cur.key} className={`wl-step wl-${cur.key}`}>{cur.body}
          {err && <p className="warn-text" role="alert">{err}</p>}
        </section>
      </div>
      <div className="wl-nav">
        {i > 0 ? <button className="btn wl-back" data-key="arrowleft" disabled={saving} onClick={() => { setErr(null); setI(i - 1); }}>Back</button> : <span />}
        {!last && cur.key !== "hi" && <button className="link wl-skip" disabled={saving} onClick={() => { setErr(null); setI(i + 1); }}>Skip</button>}
        <button className="btn pri wl-next" data-key="enter arrowright" disabled={saving} onClick={next}>{saving && !last ? "Saving…" : cur.nextLabel ?? "Next"}</button>
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
    <button className="btn pri" disabled={st === "busy" || st === "on"} onClick={go}>{st === "on" ? "Notifications on ✓" : st === "busy" ? "Asking…" : "Turn on notifications"}</button>
    {st === "off" && <p className="mute">Not on yet. You can try again in Settings → Notifications on this phone.</p>}
  </div>;
}
