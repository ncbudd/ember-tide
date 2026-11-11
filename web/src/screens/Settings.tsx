import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../ctx";
import { useAuth } from "../auth";
import { GuestStart } from "../components/GuestCard";
import Account from "./Account";
import Sheet from "../components/Sheet";
import BedSidePicker, { BedOrientation, peopleHalves, type Side, SideCheck, sideLower, sideTitle, useBedFromFoot } from "../components/BedSidePicker";
import Fold from "../components/Fold";
import { NOTIFY_TYPES, NotifyToggles, notifyOnCount, TonePicker } from "../components/NotifyPrefs";
import SettingsSleep from "./SettingsSleep";
import { HubCard } from "../components/HubControls";
import { CareFold } from "../components/Care";
import Tip from "../components/Tip";
import LeftOutNights from "../components/LeftOutNights";
import PetsEditor from "../components/PetsEditor";
import { AppearancePicker, appearanceShort, ColorSwatches, PERSONA_TIP, PersonaPicker, SharingRows, sharingSummary, SimpleScreenRows, TempUnits, WeightUnits } from "../components/PersonPrefs";
import { FlashBlock, flashSummary, minFmt, Steps, TempSteps } from "../components/FlashPrefs";
import HealthConnect from "./HealthConnect";
import PhoneNotifications from "./PhoneNotifications";
import PhoneSources from "./PhoneSources";
import { SettingsWorkDays, SettingsWearable, SettingsWebPush, SetupAgain } from "./SettingsExtras";
import { isApp } from "../api";
import { colorKey, COLORS, cToF, personaColor, weightUnit } from "../format";
import { compactPref, type CompactPref, setCompactPref } from "../keyboard";
import type { Learning, PersonSettings, Snapshot } from "../types";

const AP_PHASES = [["wind_down", "Wind-down"], ["fall_asleep", "Fall asleep"], ["deep", "Deep"], ["late", "Late night"], ["wake", "Warm wake"]] as const;
const AP_REC = ["fall_asleep", "deep", "late"];

export default function Settings() {
  const { me, partner, setMe, snap } = useApp();
  const auth = useAuth();
  useBedFromFoot();                                    // left/right as you lie (bed orientation)
  const [s, setS] = useState<PersonSettings | null>(null);
  const [profile, setProfile] = useState<{ name: string; persona: string } | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [confirmSwap, setConfirmSwap] = useState(false);
  const [swapping, setSwapping] = useState(false);
  const [checking, setChecking] = useState(false);
  const [petsOpen, setPetsOpen] = useState(false);
  const [petCount, setPetCount] = useState<number | null>(null);
  useEffect(() => { api.get<{ pets: unknown[] }>("/api/pets").then(r => setPetCount(r.pets.length)).catch(() => {}); }, []);
  const [learning, setLearning] = useState<Learning | null>(null);
  const [recal, setRecal] = useState(false);
  // Every change saves itself and applies right away: toggles almost at once, typing once you pause.
  const pending = useRef<{ pid: string; s?: PersonSettings; profile?: { name: string; persona: string } }>({ pid: "" });
  const timer = useRef<number | undefined>(undefined);
  const flush = async () => {
    window.clearTimeout(timer.current);
    const p = pending.current;
    pending.current = { pid: p.pid };
    if (!p.pid || (!p.s && !p.profile)) return;
    setSaved("Saving…");
    try {
      if (p.s) await api.put<Snapshot>(`/api/people/${p.pid}/settings`, p.s);
      if (p.profile) await api.put<Snapshot>(`/api/people/${p.pid}/profile`, p.profile);
      setSaved("Saved");
      window.setTimeout(() => setSaved(x => (x === "Saved" ? null : x)), 1500);
    } catch (e) { setSaved(`Couldn't save: ${(e as Error).message}`); }
  };
  const queue = (next: { s?: PersonSettings; profile?: { name: string; persona: string } }, delay = 350) => {
    if (!me) return;
    if (pending.current.pid && pending.current.pid !== me.id) void flush();
    pending.current = { ...pending.current, ...next, pid: me.id };
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void flush(), delay);
  };
  useEffect(() => () => { void flush(); }, []);
  useEffect(() => {
    if (!me) return;
    api.get<{ learning?: Learning }>(`/api/people/${me.id}/autopilot`).then(r => setLearning(r.learning ?? null)).catch(() => setLearning(null));
  }, [me?.id, s?.autopilot.learn_skip, s?.autopilot.learn_nights]);                   // leaving the screen never loses a change
  useEffect(() => { if (me) { void flush(); setS(structuredClone(me.settings)); setProfile({ name: me.name, persona: me.persona }); } }, [me?.id]);
  // changed somewhere else (Studio, the other phone, first-time setup): follow it, unless a change of ours is still waiting to save
  const live = me ? JSON.stringify(me.settings) : "";
  useEffect(() => { if (me && !pending.current.s && saved !== "Saving…") setS(structuredClone(me.settings)); }, [live]);
  if (!me || !s || !profile || !snap) return null;
  const u = s.units;
  const upd = (patch: Partial<PersonSettings>) => { const next = { ...s, ...patch }; setS(next); queue({ s: next }); };
  const updProfile = (next: { name: string; persona: string }, delay = 350) => {
    setProfile(next);
    if (next.name.trim()) queue({ profile: { ...next, name: next.name.trim() } }, delay);
  };
  const ap = s.autopilot;
  // one-line summaries for the collapsed sections
  const sumTonight = `Opens on ${s.default_view === "me" ? "me" : s.default_view === "both" ? "both" : partner?.name ?? "partner"} · °${u} · ${weightUnit(s)}`;
  const sumLook = `${appearanceShort(s.appearance)} · ${COLORS.find(c => c.key === colorKey({ zone: me.zone, settings: s }))?.name ?? ""}`;
  const sumFlash = flashSummary(s);
  const sumAp = `${ap.mode === "off" ? "Off" : ap.mode === "suggest" ? "Suggests only" : "Makes small changes"}${ap.mode !== "off" && learning && !learning.done ? ` · learning ${learning.have} of ${learning.need}${learning.paused ? " (paused, away)" : ""}` : ""}`;
  const notifyOn = notifyOnCount(s);
  const guests = Object.entries(snap.guest ?? {}).filter(([, g]) => g?.on).map(([z]) => sideLower(z));
  const sumShare = sharingSummary(s);

  return (
    <div className="screen settings">
      <div className="row" style={{ alignItems: "flex-end" }}><div><div className="k">More</div><h2 style={{ fontSize: 28 }}>Settings</h2>
        <span className="set-for"><i style={{ background: personaColor(me) }} />For {me.name}{partner ? ` · ${sideLower(me.zone)}` : ""}</span></div>
        <span className="mute" role="status" aria-live="polite" style={{ fontSize: 12.5, paddingBottom: 6 }}>{saved}</span></div>
      <div className="settings-flow">

      <div className="card list">
        <div className="row"><span>Name</span><input type="text" value={profile.name} style={{ width: 150 }} onChange={e => updProfile({ ...profile, name: e.target.value }, 900)} onBlur={() => void flush()} /></div>
        <div style={{ display: "grid", gap: 8, padding: "11px 0", borderBottom: "1px solid var(--line)" }}><span>How I sleep<Tip label="Ember and Tide" text={PERSONA_TIP} /></span>
          <PersonaPicker value={profile.persona} onChange={k => updProfile({ ...profile, persona: k })} /></div>
        {(!auth.state.enabled || auth.isOwner) && <div className="row"><span>{auth.state.enabled ? "Showing the app as" : "This phone belongs to"}</span>
          <select value={me.id} onChange={e => setMe(e.target.value)}>{snap.people.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>}
      </div>

      <Fold id="settings.flash" title="Flash relief" summary={sumFlash}>
      <FlashBlock hot label="❄ Hot flash" color="var(--cool)" f={s.hot_flash} units={u} set={f => upd({ hot_flash: f })} />
      <FlashBlock hot={false} label="☼ Cold flash" color="var(--warm)" f={s.cold_flash} units={u} set={f => upd({ cold_flash: f })} />
      </Fold>

      {isApp() ? <PhoneNotifications /> : <SettingsWebPush person={me.id} />}
      <Fold id="settings.notify" title="Notifications" summary={`${notifyOn === NOTIFY_TYPES.length ? "All on" : notifyOn === 0 ? "All off" : `${notifyOn} of ${NOTIFY_TYPES.length} on`} · ${s.tone ?? "calm"} words`}>
      <div className="card list">
        <NotifyToggles s={s} upd={upd} />
        <TonePicker s={s} name={me.name} upd={upd} />
        <p className="faint" style={{ fontSize: 12, padding: "8px 0 2px" }}>{isApp() ? "They can also go to the ntfy or Home Assistant app, as a backup. Set that up" : "Choose where they're sent"} in <Link to="/studio/notifications">Studio → Notifications</Link>.</p>
      </div>
      </Fold>

      <Fold id="settings.autopilot" title="Autopilot" summary={sumAp}>
      <div className="card list">
        <div className="seg">{(["off", "suggest", "act"] as const).map(m => <button key={m} aria-pressed={ap.mode === m} onClick={() => upd({ autopilot: { ...ap, mode: m } })}>{m[0].toUpperCase() + m.slice(1)}</button>)}</div>
        <p className="mute" style={{ fontSize: 12.5, padding: "6px 0" }}>{ap.mode === "off" ? "Never changes or suggests anything." : ap.mode === "suggest" ? "Ideas show up on the Sleep tab. Nothing changes overnight." : "Makes small changes overnight within the limits below, and tells you about them in the morning."}</p>
        {ap.mode !== "off" && learning && !learning.done && <div className="row">
          <span>Learning your nights<Tip label="What are learning nights?" text="For the first nights Autopilot only watches, to learn what a normal night looks like for you. It changes and suggests nothing until then." /><br /><span className="mute" style={{ fontSize: 12 }}>{learning.have} of {learning.need} nights so far. Start now to skip the wait.</span></span>
          <button className="pill" onClick={() => upd({ autopilot: { ...ap, learn_skip: true } })}>Start now</button></div>}
        {ap.mode !== "off" && learning && !learning.done && <LeftOutNights learning={learning} />}
        {ap.mode !== "off" && <>
          <div className="row"><span>Suggestions<Tip label="How often suggestions show" text="Ideas are saved up and shown in the Sleep tab on this schedule. A suggestion only appears once a pattern shows on several nights." /></span></div>
          <div className="seg">{([["morning", "Daily"], ["weekly", "Weekly"], ["monthly", "Monthly"], ["off", "Off"]] as const).map(([k, l]) =>
            <button key={k} aria-pressed={ap.suggest_cadence === k} onClick={() => upd({ autopilot: { ...ap, suggest_cadence: k } })}>{l}</button>)}</div>
          {(ap.suggest_cadence === "weekly" || ap.suggest_cadence === "monthly") && <div className="chips" style={{ padding: "8px 0" }} aria-label="Which morning">
            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d, i) => <button key={d} className="chip" style={{ minWidth: 44, minHeight: 36 }} aria-pressed={ap.suggest_weekday === i} onClick={() => upd({ autopilot: { ...ap, suggest_weekday: i } })}>{d}</button>)}</div>}
          <p className="mute" style={{ fontSize: 12, padding: "6px 0" }}>{ap.suggest_cadence === "morning" ? "Ideas wait in your Sleep tab each morning." : ap.suggest_cadence === "weekly" ? `Saved up and shown once a week, on ${["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][ap.suggest_weekday]}.` : ap.suggest_cadence === "monthly" ? `Saved up and shown on the first ${["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][ap.suggest_weekday]} of each month.` : "No suggestions on the phone. Studio still shows them."}</p>
        </>}
        <div className="row"><span>Biggest change from your rhythm</span>{u === "F"
          ? <Steps label="change" value={Math.round(ap.max_delta_c * 9 / 5)} min={1} max={6} step={1} fmt={v => `±${v}°`} onChange={v => upd({ autopilot: { ...ap, max_delta_c: v * 5 / 9 } })} />
          : <Steps label="change" value={Math.round(ap.max_delta_c * 2) / 2} min={0.5} max={3.5} step={0.5} fmt={v => `±${v.toFixed(1)}°`} onChange={v => upd({ autopilot: { ...ap, max_delta_c: v } })} />}</div>
        <div className="row"><span>Never below</span><TempSteps label="floor" c={ap.floor_c} units={u} minF={50} maxF={Math.round(cToF(ap.ceiling_c)) - 2} onChange={c => upd({ autopilot: { ...ap, floor_c: c } })} /></div>
        <div className="row"><span>Never above</span><TempSteps label="ceiling" c={ap.ceiling_c} units={u} minF={Math.round(cToF(ap.floor_c)) + 2} maxF={104} onChange={c => upd({ autopilot: { ...ap, ceiling_c: c } })} /></div>
        <div className="row"><span>Changes per night, at most</span><Steps label="changes" value={ap.max_actions} min={1} max={10} step={1} fmt={v => `${v}`} onChange={v => upd({ autopilot: { ...ap, max_actions: v } })} /></div>
        <div className="row"><span className="k" style={{ margin: 0 }}>Parts of the night it can adjust<Tip label="Why only some parts" text="Autopilot only touches the parts switched on here. Recommended leaves wind-down and the warm wake alone, so bedtime and waking feel the same every night." /></span><button className="pill" onClick={() => upd({ autopilot: { ...ap, phases: [...AP_REC] } })}>Use recommended</button></div>
        {AP_PHASES.map(([id, name]) => (
          <div className="row" key={id}><span>{name}{AP_REC.includes(id) ? <span className="mute" style={{ fontSize: 11 }}> · recommended</span> : null}</span>
            <input type="checkbox" className="toggle" checked={ap.phases.includes(id)} onChange={e => upd({ autopilot: { ...ap, phases: e.target.checked ? [...ap.phases, id] : ap.phases.filter(x => x !== id) } })} /></div>
        ))}
        <div className="row"><span>No changes this close to wake</span><Steps label="quiet minutes" value={ap.quiet_before_wake_min} min={0} max={120} step={15} fmt={minFmt} onChange={v => upd({ autopilot: { ...ap, quiet_before_wake_min: v } })} /></div>
        {ap.mode !== "off" && (!recal
          ? <div className="row"><span>Learn again from scratch<Tip label="What does this do?" text="Autopilot forgets what it has learned about your nights, and its past suggestions and changes, then watches for a week again. Your sleep history, rhythm and settings stay." /></span>
            <button className="pill" onClick={() => setRecal(true)}>Start over</button></div>
          : <div role="alertdialog" aria-label="Start over" style={{ display: "grid", gap: 8, padding: "11px 0" }}>
            <span style={{ fontSize: 14 }}>Start Autopilot over for you?</span>
            <span className="mute" style={{ fontSize: 12.5 }}>It forgets what it has learned about your nights and its past suggestions and changes, then watches for {ap.learn_nights || 7} nights before suggesting anything. Your sleep history, rhythm and other settings stay, and so does everything of {partner?.name ?? "your partner"}'s.</span>
            <div className="row" style={{ justifyContent: "flex-end", gap: 8, border: 0, padding: 0 }}>
              <button className="btn" onClick={() => setRecal(false)}>Cancel</button>
              <button className="btn pri" onClick={async () => {
                try {
                  await flush();
                  const r = await api.post<{ policy: PersonSettings["autopilot"]; learning: Learning }>(`/api/people/${me.id}/autopilot/recalibrate`);
                  setS(x => x ? { ...x, autopilot: r.policy } : x); setLearning(r.learning); setSaved("Starting over");
                } catch (e) { setSaved(`Couldn't start over: ${(e as Error).message}`); }
                setRecal(false);
              }}>Yes, start over</button></div></div>)}
      </div>
      </Fold>

      <Fold id="settings.tonight" title="Tonight screen" summary={sumTonight}>
      <div className="card list">
        <div className="row"><span>Open on</span>
          <div className="seg" style={{ width: 210 }}>
            {(["me", "both", "partner"] as const).map(k => <button key={k} aria-pressed={s.default_view === k} onClick={() => upd({ default_view: k })}>{k === "me" ? "Me" : k === "both" ? "Both" : partner?.name ?? "Partner"}</button>)}
          </div></div>
        <SimpleScreenRows s={s} onChange={upd} />
        <div className="row"><span>Units</span><TempUnits s={s} onChange={k => upd({ units: k })} /></div>
        <div className="row"><span>Weights<br /><span className="mute" style={{ fontSize: 12 }}>Pets and bed scales</span></span><WeightUnits s={s} onChange={k => upd({ weight_units: k })} /></div>
      </div>
      </Fold>

      <Fold id="settings.sharing" title="Sharing" summary={sumShare}>
      <div className="card list">
        <SharingRows s={s} partnerName={partner?.name} onChange={upd} />
      </div>
      </Fold>

      <Fold id="settings.side" title="My side of the bed" summary={sideTitle(me.zone as Side)}>
      <div className="card list">
        <div className="settings-side">
          <BedSidePicker value={me.zone as Side} halves={peopleHalves(snap.people)} caption={<span className="mute" style={{ fontSize: 13 }}>As you lie in bed. Tap the other half to change it.</span>}
            onChange={async z => {
              if (z === "both" || z === me.zone) return;
              if (partner) { setConfirmSwap(true); return; }
              try { await api.put(`/api/people/${me.id}/profile`, { zone: z }); setSaved(`Now on the ${sideLower(z)}`); window.setTimeout(() => setSaved(null), 2500); }
              catch (e) { setSaved(`Couldn't change it: ${(e as Error).message}`); }
            }} />
          <div className="pills"><button className="pill" onClick={() => setChecking(true)}>Check sides with the bed</button>
            {partner && <button className="pill" onClick={() => setConfirmSwap(true)}>Swap sides</button>}</div>
          {auth.isOwner && <BedOrientation onSaid={m => { setSaved(m); window.setTimeout(() => setSaved(null), 2500); }} />}</div>
      </div>
      </Fold>

      <SettingsWorkDays value={s.work_days ?? [0, 1, 2, 3, 4]} onChange={v => upd({ work_days: v })} />

      <Fold id="settings.appearance" title="Appearance" summary={sumLook}>
      <div className="card list">
        <KeyboardLayout />
        <div className="item" style={{ display: "grid", gap: 10 }}>
          <span>Your colour<br /><span className="mute" style={{ fontSize: 12 }}>For your name, chart and side everywhere, including the bedside dials</span></span>
          <ColorSwatches zone={me.zone} s={s} partner={partner} onPick={c => upd({ color: c })} />
        </div>
        <div className="item" style={{ display: "grid", gap: 8 }}><span>Light or dark</span><AppearancePicker s={s} onChange={upd} /></div>
      </div>
      </Fold>

      {isApp() && <PhoneSources />}
      {isApp() && <HealthConnect />}
      <SettingsWearable person={me.id} value={s.wearable ?? ""} onChange={v => upd({ wearable: v })} />
      <SettingsSleep s={s} upd={upd} />

      <div className="card list">
        <div className="row"><span>Simple app<br /><span className="mute" style={{ fontSize: 12 }}>Just temperature, flashes and an easy schedule. Everything else stays in Studio.</span></span>
          <input type="checkbox" className="toggle" checked={s.app_mode === "simple"} onChange={e => upd({ app_mode: e.target.checked ? "simple" : "full" })} aria-label="Simple app" /></div>
        {(!auth.state.enabled || auth.isOwner) && <button type="button" className="row rowbtn" onClick={() => setPetsOpen(true)}>
          <span>Pets on the bed<br /><span className="mute" style={{ fontSize: 12 }}>So a cat or dog isn't mistaken for someone in bed.</span></span>
          <span className="mute" style={{ whiteSpace: "nowrap" }}>{petCount == null ? "Set up ›" : petCount === 0 ? "None ›" : `${petCount} pet${petCount === 1 ? "" : "s"} ›`}</span></button>}
      </div>
      {petsOpen && <Sheet onClose={() => setPetsOpen(false)} role="dialog" aria-label="Pets on the bed">
        <div className="k">Pets on the bed</div>
        <p className="mute" style={{ fontSize: 13 }}>Their readings are recognised and set aside, so a pet won't start your night or sway Autopilot.</p>
        <PetsEditor weightUnit={weightUnit(s)} showIgnore compact learned onCount={setPetCount} />
      </Sheet>}

      {auth.isOwner && <Fold id="settings.guest" title="Guest mode" summary={guests.length ? `On · ${guests.join(", ")}` : "Off"}>
        <div className="card">
          {Object.entries(snap.guest ?? {}).map(([z, g]) => g && (
            <div className="row" key={z}><span>{sideTitle(z as Side)} · guest{g.on ? "" : " (off)"}</span>
              <button className="pill" onClick={() => api.post(`/api/guest/${z}/stop`, {})}>End</button></div>
          ))}
          <GuestStart />
        </div>
      </Fold>}
      {(snap.system.hub || auth.isOwner) && <Fold id="settings.hub" anchor="hub" title="Bed hub" summary={snap.system.hub?.line ?? "Light and fan"} attention={!!snap.system.hub && !snap.system.hub.ok}>
        <HubCard snap={snap} owner={auth.isOwner} />
        {auth.isOwner && <p className="faint" style={{ fontSize: 12, padding: "6px 2px 0" }}>Restart, adding water and more detail are in <Link to="/studio/hub">Studio → Bed hub</Link>.</p>}
      </Fold>}

      <CareFold owner={auth.isOwner} />
      <Account />
      <SetupAgain person={me.id} />
      </div>
      <p className="mute" style={{ fontSize: 12, textAlign: "center" }}>Devices, exporting your data and more are in <Link to="/studio">Studio</Link>.</p>
      {checking && <Sheet onClose={() => setChecking(false)} aria-label="Check sides with the bed">
          <h2 style={{ fontSize: 20 }}>Check sides with the bed</h2>
          <SideCheck personId={me.id} onDone={() => setChecking(false)} />
      </Sheet>}
      {confirmSwap && partner && <Sheet onClose={() => setConfirmSwap(false)} locked={swapping} role="alertdialog" aria-labelledby="swap-title" aria-describedby="swap-desc">
          <h2 id="swap-title" style={{ fontSize: 20 }}>Swap sides?</h2>
          <p id="swap-desc" className="mute" style={{ fontSize: 14 }}>{me.name} moves to the {sideLower(partner.zone)} and {partner.name} to the {sideLower(me.zone)}. Rhythms, settings and sleep history move with each of you, and the bed changes both sides right away.</p>
          <p className="faint" style={{ fontSize: 12.5 }}>Your watch, phone and bedside dial go with you, and so does tonight's night if one of you is already in bed. Sensors in the bed (radar, scale) keep watching the same spot.</p>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 6 }}>
            <button className="btn" disabled={swapping} onClick={() => setConfirmSwap(false)}>Cancel</button>
            <button className="btn pri" disabled={swapping} onClick={async () => {
              setSwapping(true);
              try { await api.post("/api/household/swap-sides"); setSaved("Sides swapped"); window.setTimeout(() => setSaved(null), 2500); }
              catch (e) { setSaved(`Couldn't swap: ${(e as Error).message}`); }
              setSwapping(false); setConfirmSwap(false);
            }}>{swapping ? "Swapping…" : "Swap sides"}</button>
          </div>
      </Sheet>}
    </div>
  );
}


/** This phone only: the compact keyboard layout (Minimal Phone 2 and other phones with a hardware keyboard). */
function KeyboardLayout() {
  const [v, setV] = useState<CompactPref>(compactPref);
  const pick = (x: CompactPref) => { setV(x); setCompactPref(x); };
  return (
    <div className="item" style={{ display: "grid", gap: 8 }}>
      <span>Keyboard layout<br /><span className="mute" style={{ fontSize: 12 }}>For phones with a keyboard: everything on one screen, with key hints instead of tabs. Press ? to see the keys. This phone only.</span></span>
      <div className="seg">{(["auto", "on", "off"] as const).map(x => <button key={x} aria-pressed={v === x} onClick={() => pick(x)}>{x === "auto" ? "Automatic" : x === "on" ? "On" : "Off"}</button>)}</div>
    </div>
  );
}
