import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import BedSidePicker, { peopleHalves, type Side, SideCheck, sideTitle, useBedFromFoot } from "../components/BedSidePicker";
import Fold, { FoldLock } from "../components/Fold";
import { NOTIFY_TYPES, NotifyToggles, notifyOnCount, TonePicker } from "../components/NotifyPrefs";
import { AppearancePicker, appearanceShort, ColorSwatches, PERSONA_TIP, PersonaPicker, SharingRows, sharingSummary, SimpleScreenRows, TempUnits, WeightUnits } from "../components/PersonPrefs";
import { FlashBlock, flashSummary } from "../components/FlashPrefs";
import Tip from "../components/Tip";
import WorkDays, { workDaysText } from "../components/WorkDays";
import { useApp } from "../ctx";
import { colorKey, COLORS, personaColor, weightUnit } from "../format";
import { SleepDataRows, sleepDataSummary } from "../screens/SettingsSleep";
import type { PersonSettings, PersonView } from "../types";
import { BRANDS } from "../wearables";
import { agoText } from "./PhoneRows";

/**
 * Studio → Your settings: the signed-in person's own settings on a big screen (the same ones as the phone's Settings,
 * reusing its pieces). Sign-in off: pick whose settings. The owner can pick the other person too, clearly labelled.
 * Every control saves on change: PATCH /api/people/{pid}/settings with just that change, PUT …/profile for name,
 * Ember / Tide and side. The server checks who may change what (auth.authorize).
 */

const DAY = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const PHONE_LABEL: Record<string, string> = {
  location: "home or away", wifi: "home Wi-Fi", calendar: "calendar times", alarm: "next alarm", charging: "charging and bedtime mode",
  watch: "watch connected", dial: "bedside dial nearby", watch_hr: "watch heart rate at night",
};
const cap = (x: string) => x[0].toUpperCase() + x.slice(1);

export default function Me() {
  const { snap, me: viewer, say } = useApp();
  const auth = useAuth();
  useBedFromFoot();                                    // left/right as you lie (bed orientation)
  const canPick = !auth.state.enabled || auth.isOwner;
  const own = auth.state.enabled ? auth.me?.person ?? null : viewer?.id ?? null;
  const [pid, setPid] = useState<string | null>(null);
  const person = snap?.people.find(p => p.id === (pid ?? own)) ?? snap?.people.find(p => p.id === own) ?? snap?.people[0];
  const readOnly = auth.state.enabled && auth.me?.role === "viewer";

  // what you just changed, shown at once while the live snapshot catches up (cleared a few seconds after saving)
  const [over, setOver] = useState<Partial<PersonSettings>>({});
  useEffect(() => setOver({}), [person?.id]);
  const save = async (patch: Partial<PersonSettings>) => {
    if (!person) return;
    setOver(o => ({ ...o, ...patch }));
    try {
      await api.patch(`/api/people/${person.id}/settings`, patch);
      say("Saved");
      window.setTimeout(() => setOver(o => {
        const n = { ...o };
        for (const k of Object.keys(patch) as (keyof PersonSettings)[]) if (n[k] === patch[k]) delete n[k];
        return n;
      }), 4000);
    } catch (e) {
      say(`Couldn't save: ${(e as Error).message}`);
      setOver(o => { const n = { ...o }; for (const k of Object.keys(patch) as (keyof PersonSettings)[]) delete n[k]; return n; });
    }
  };
  const profile = async (body: { name?: string; persona?: string; zone?: string }, done = "Saved") => {
    if (!person) return;
    try { await api.put(`/api/people/${person.id}/profile`, body); say(done); } catch (e) { say(`Couldn't save: ${(e as Error).message}`); }
  };

  if (!snap || !person) return null;
  const s: PersonSettings = { ...person.settings, ...over };
  const partner = snap.people.find(p => p.id !== person.id);
  const mine = person.id === own;
  return (
    <>
      <div className="row" style={{ flexWrap: "wrap", alignItems: "flex-end" }}>
        <div><div className="k">You</div><h1 style={{ fontSize: 34 }}>{mine || !canPick ? "Your settings" : `${person.name}'s settings`}</h1></div>
        {canPick && snap.people.length > 1 && <div style={{ display: "grid", gap: 4, justifyItems: "end" }}>
          <span className="mute" style={{ fontSize: 12.5 }}>{auth.state.enabled ? "Settings for" : "Whose settings?"}</span>
          <div className="seg" role="group" aria-label="Whose settings">
            {snap.people.map(p => <button key={p.id} type="button" aria-pressed={p.id === person.id} onClick={() => setPid(p.id)}>
              {p.name}{auth.state.enabled && p.id === own ? " (you)" : ""}</button>)}
          </div></div>}
      </div>
      {auth.state.enabled && !mine && canPick && <div className="box" role="note" style={{ borderColor: "var(--warn)", background: "color-mix(in srgb, var(--warn) 10%, var(--surf))" }}>
        <span><b style={{ fontWeight: 500 }}>Changing {person.name}'s settings as the owner.</b> <span className="mute">They'll see these changes on their phone.{own ? <> <button type="button" className="link" onClick={() => setPid(own)}>Back to yours</button></> : null}</span></span>
      </div>}
      {readOnly && <div className="box mute" role="note">This account can look but not change anything.</div>}
      <FoldLock.Provider value={readOnly}>
        <div className="grid2 even">
          <div style={{ display: "grid", gap: 16, alignContent: "start", minWidth: 0 }}>
            <YouBox key={person.id} p={person} s={s} partner={partner} people={snap.people} save={save} profile={profile} say={say} />
            <Fold id="studio.me.units" className="box" title="Units" keepSummary defaultOpen summary={`°${s.units} · ${weightUnit(s)}`}>
              <div className="list">
                <div className="row"><span>Temperature</span><TempUnits s={s} onChange={u => save({ units: u })} /></div>
                <div className="row"><span>Weight<br /><small className="mute">Pets, bed scales and body weight</small></span><WeightUnits s={s} onChange={w => save({ weight_units: w })} /></div>
                <div className="item mute" style={{ fontSize: 12.5 }}>
                  {(s.weight_units ?? "auto") === "auto"
                    ? `Follows the temperature: ${s.units === "F" ? "pounds with °F" : "kilograms with °C"}. Pick one to keep it either way.`
                    : <>Kept in {s.weight_units === "lb" ? "pounds" : "kilograms"} whatever the temperature. <button type="button" className="link" onClick={() => save({ weight_units: "auto" })}>Follow the temperature instead</button></>}
                </div>
              </div>
            </Fold>
            <Fold id="studio.me.week" className="box" title="Your week" keepSummary defaultOpen summary={workDaysText(s.work_days ?? [0, 1, 2, 3, 4])}>
              <span className="mute" style={{ fontSize: 13 }}>Work days are the days you get up for something fixed: work, school, an early start.</span>
              <WorkDays value={s.work_days ?? [0, 1, 2, 3, 4]} onChange={v => save({ work_days: v })} />
              <span className="faint" style={{ fontSize: 12.5 }}>A rhythm for "nights before a work day" or "nights before a day off" follows these days by itself. Set their times in the <Link to="/studio/rhythm">Rhythm editor</Link>.</span>
            </Fold>
            <Fold id="studio.me.app" className="box" title="App" keepSummary defaultOpen
              summary={`${(s.app_mode ?? "full") === "simple" ? "Simple" : "Full"} · ${appearanceShort(s.appearance).toLowerCase()}`}>
              <div className="list">
                <div className="row"><span>Phone app<Tip label="Simple or Full" text="Simple shows just the temperature, hot and cold flashes and an easy schedule. Everything else stays here in Studio." /></span>
                  <div className="seg" style={{ width: 170 }} role="group" aria-label="Phone app">
                    {(["full", "simple"] as const).map(m => <button key={m} type="button" aria-pressed={(s.app_mode ?? "full") === m} onClick={() => save({ app_mode: m })}>{m === "full" ? "Full" : "Simple"}</button>)}
                  </div></div>
                {(s.app_mode ?? "full") === "full" && <div className="row"><span>Tonight opens on</span>
                  <div className="seg" style={{ width: 210 }} role="group" aria-label="Tonight opens on">
                    {(["me", "both", "partner"] as const).map(k => <button key={k} type="button" aria-pressed={s.default_view === k} onClick={() => save({ default_view: k })}>
                      {k === "me" ? person.name : k === "both" ? "Both" : partner?.name ?? "Partner"}</button>)}
                  </div></div>}
                <div className="item" style={{ display: "grid", gap: 8 }}>
                  <span>Light or dark</span>
                  <AppearancePicker s={s} onChange={save} />
                </div>
                {(s.app_mode ?? "full") === "full" && <SimpleScreenRows s={s} onChange={save} />}
              </div>
            </Fold>
          </div>
          <div style={{ display: "grid", gap: 16, alignContent: "start", minWidth: 0 }}>
            <SleepBox s={s} save={save} />
            <Fold id="studio.me.flash" className="box" title="Hot and cold flashes" keepSummary summary={flashSummary(s)}>
              <span className="mute" style={{ fontSize: 13 }}>What the flash buttons do on your side: on the phone, the bedside dial, the watch and Home Assistant.</span>
              <FlashBlock hot label="❄ Hot flash" color="var(--cool)" f={s.hot_flash} units={s.units} set={f => save({ hot_flash: f })} />
              <FlashBlock hot={false} label="☼ Cold flash" color="var(--warm)" f={s.cold_flash} units={s.units} set={f => save({ cold_flash: f })} />
            </Fold>
            <Fold id="studio.me.sharing" className="box" title={`Sharing with ${partner?.name ?? "your partner"}`} keepSummary defaultOpen
              summary={sharingSummary(s).toLowerCase()}>
              <div className="list">
                <SharingRows s={s} partnerName={partner?.name} whose={mine ? "your" : `${person.name}'s`} onChange={save} />
              </div>
            </Fold>
            <NotifyBox s={s} name={person.name} save={save} />
            <PhoneBox p={person} s={s} />
          </div>
        </div>
      </FoldLock.Provider>
    </>
  );
}

/** Name, Ember / Tide, colour, side of the bed. */
function YouBox({ p, s, partner, people, save, profile, say }: {
  p: PersonView; s: PersonSettings; partner?: PersonView; people: PersonView[];
  save: (x: Partial<PersonSettings>) => void; profile: (b: { name?: string; persona?: string; zone?: string }, done?: string) => Promise<void>; say: (m: string) => void;
}) {
  const [name, setName] = useState(p.name);
  const [persona, setPersona] = useState(p.persona);
  const [swapAsk, setSwapAsk] = useState(false);
  const [checking, setChecking] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => { if (document.activeElement?.id !== "me-name") setName(p.name); }, [p.name]);
  useEffect(() => setPersona(p.persona), [p.persona]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const sendName = (v: string) => { window.clearTimeout(timer.current); const t = v.trim(); if (t && t !== p.name) void profile({ name: t }); };
  const colorName = COLORS.find(c => c.key === colorKey({ zone: p.zone, settings: s }))?.name ?? "";
  return (
    <Fold id="studio.me.you" className="box" title="You" keepSummary defaultOpen
      summary={<><span style={{ color: personaColor({ zone: p.zone, settings: s }) }}>●</span> {p.name} · {sideTitle(p.zone as Side).toLowerCase()}</>}>
      <div className="list">
        <div className="row"><label htmlFor="me-name">Name</label>
          <input id="me-name" type="text" maxLength={40} value={name} style={{ width: 200, maxWidth: "55%" }}
            onChange={e => { setName(e.target.value); window.clearTimeout(timer.current); const v = e.target.value; timer.current = window.setTimeout(() => sendName(v), 900); }}
            onBlur={() => sendName(name)} onKeyDown={e => e.key === "Enter" && sendName(name)} /></div>
        <div className="item" style={{ display: "grid", gap: 8 }}>
          <span>How you sleep<Tip label="Ember and Tide" text={PERSONA_TIP} /></span>
          <PersonaPicker value={persona} onChange={k => { setPersona(k); void profile({ persona: k }); }} />
        </div>
        <div className="item" style={{ display: "grid", gap: 10 }}>
          <span>Colour <span className="mute" style={{ fontSize: 12.5 }}>· {colorName}</span><br /><small className="mute">Your name, chart line and side everywhere, including the bedside dials.</small></span>
          <ColorSwatches zone={p.zone} s={s} partner={partner} onPick={c => save({ color: c })} />
        </div>
        <div className="item" style={{ display: "grid", gap: 10 }}>
          <span>Side of the bed</span>
          <div style={{ maxWidth: 360 }}>
            <BedSidePicker value={p.zone as Side} halves={peopleHalves(people)} label="Side of the bed"
              caption={<span className="mute" style={{ fontSize: 13 }}>As you lie in bed. Tap the other half to change it.</span>}
              onChange={z => {
                if (z === "both" || z === p.zone) return;
                if (partner && partner.zone === z) { setSwapAsk(true); return; }
                void profile({ zone: z }, `Now on the ${sideTitle(z).toLowerCase()}`);
              }} />
          </div>
          {swapAsk && partner
            ? <div role="alertdialog" aria-label="Swap sides" style={{ display: "grid", gap: 8 }}>
                <span>Swap sides with {partner.name}?</span>
                <span className="mute" style={{ fontSize: 12.5 }}>Rhythms, settings and sleep history move with each of you, and the bed changes both sides right away. Watches, phones and dials go with their owner; sensors in the bed stay put.</span>
                <div className="pills"><button type="button" className="btn" onClick={() => setSwapAsk(false)}>Cancel</button>
                  <button type="button" className="btn pri" onClick={async () => {
                    try { await api.post("/api/household/swap-sides"); say("Sides swapped"); } catch (e) { say(`Couldn't swap: ${(e as Error).message}`); }
                    setSwapAsk(false);
                  }}>Swap sides</button></div>
              </div>
            : <div className="pills"><button type="button" className="pill" onClick={() => setChecking(c => !c)} aria-expanded={checking}>Check sides with the bed</button>
                {partner && <button type="button" className="pill" onClick={() => setSwapAsk(true)}>Swap sides</button>}</div>}
          {checking && <SideCheck personId={p.id} onDone={() => setChecking(false)} />}
        </div>
      </div>
    </Fold>
  );
}

/** Autopilot in brief, which to trust, in / out of bed timing, pet questions. */
function SleepBox({ s, save }: { s: PersonSettings; save: (x: Partial<PersonSettings>) => void }) {
  const ap = s.autopilot;
  const cadence = ap.suggest_cadence === "morning" ? "ideas each morning" : ap.suggest_cadence === "weekly" ? `ideas on ${DAY[ap.suggest_weekday]}s`
    : ap.suggest_cadence === "monthly" ? `ideas on the first ${DAY[ap.suggest_weekday]} of the month` : "no ideas on the phone";
  return (
    <Fold id="studio.me.sleep" className="box" title="Sleep data and Autopilot" keepSummary defaultOpen summary={`Autopilot ${ap.mode} · ${sleepDataSummary(s).toLowerCase()}`}>
      <div className="list">
        <div className="item" style={{ display: "grid", gap: 8 }}>
          <span className="row"><span>Autopilot<Tip label="What Autopilot does" text="Off never changes anything. Suggest saves ideas for your morning report. Act makes small changes overnight within your limits and explains them in the morning." /></span>
            <Link to="/studio/autopilot" style={{ fontSize: 13 }}>Limits, learning and history →</Link></span>
          <div className="seg" role="group" aria-label="Autopilot">
            {(["off", "suggest", "act"] as const).map(m => <button key={m} type="button" aria-pressed={ap.mode === m} onClick={() => save({ autopilot: { ...ap, mode: m } })}>{cap(m)}</button>)}
          </div>
          {ap.mode !== "off" && <>
            <div className="seg" role="group" aria-label="How often ideas show">
              {([["morning", "Daily"], ["weekly", "Weekly"], ["monthly", "Monthly"], ["off", "Off"]] as const).map(([k, l]) =>
                <button key={k} type="button" aria-pressed={ap.suggest_cadence === k} onClick={() => save({ autopilot: { ...ap, suggest_cadence: k } })}>{l}</button>)}
            </div>
            <span className="mute" style={{ fontSize: 12.5 }}>{ap.mode === "act" ? "Small changes overnight" : "Nothing changes overnight"}; {cadence}. At most ±{s.units === "F" ? `${Math.round(ap.max_delta_c * 9 / 5)}°` : `${ap.max_delta_c.toFixed(1)}°`} and {ap.max_actions} change{ap.max_actions === 1 ? "" : "s"} a night.</span>
          </>}
        </div>
        <SleepDataRows s={s} upd={save} />
      </div>
    </Fold>
  );
}

/** Which notifications, quiet hours, and the tone (Calm / Friendly / Cheeky) with a preview. Where they're sent is in
 *  Studio → Notifications (owner). */
function NotifyBox({ s, name, save }: { s: PersonSettings; name: string; save: (x: Partial<PersonSettings>) => void }) {
  return (
    <Fold id="studio.me.notify" className="box" title="Notifications" keepSummary defaultOpen
      summary={`${notifyOnCount(s)} of ${NOTIFY_TYPES.length} on · ${s.tone ?? "calm"}`}>
      <div className="list">
        <NotifyToggles s={s} upd={save} />
        <TonePicker s={s} name={name} upd={save} />
      </div>
      <span className="faint" style={{ fontSize: 12.5 }}>Where they're sent (the app, ntfy or Home Assistant) is set up in <Link to="/studio/notifications">Notifications</Link>.</span>
    </Fold>
  );
}

/** What this person's phone and watch send. Read-only: those switches live in the phone app. */
function PhoneBox({ p, s }: { p: PersonView; s: PersonSettings }) {
  const [phone, setPhone] = useState<{ device?: string | null; sharing?: string[]; seen?: number | null } | null>(null);
  const [watch, setWatch] = useState<{ seen: number | null; wanted: boolean } | null>(null);
  useEffect(() => {
    let live = true;
    const f = () => {
      api.get<typeof phone>(`/api/people/${p.id}/phone-context`).then(r => live && setPhone(r ?? {})).catch(() => live && setPhone({}));
      api.get<{ seen: number | null; wanted: boolean }>(`/api/people/${p.id}/watch-hr`).then(r => live && setWatch(r)).catch(() => live && setWatch(null));
    };
    f(); const t = window.setInterval(f, 30000);
    return () => { live = false; window.clearInterval(t); };
  }, [p.id]);
  const now = Date.now() / 1000;
  const sharing = (phone?.sharing ?? []).filter(x => x !== "watch_hr");
  const hrOn = (phone?.sharing ?? []).includes("watch_hr");
  const brand = BRANDS.find(b => b.id === s.wearable)?.name;
  return (
    <Fold id="studio.me.phone" className="box" title="Phone and watch" keepSummary defaultOpen
      summary={phone?.seen ? `phone seen ${agoText(now - phone.seen)}` : phone ? "no phone yet" : "…"}>
      <div className="list">
        <div className="row"><span>Phone</span><span className="mute" style={{ textAlign: "right" }}>{!phone ? "Checking…" : phone.seen ? `${phone.device || "Phone"} · last seen ${agoText(now - phone.seen)}` : "Hasn't reported yet"}</span></div>
        <div className="row"><span>Shares</span><span className="mute" style={{ textAlign: "right" }}>{sharing.length ? sharing.map(x => PHONE_LABEL[x] ?? x).join(", ") : "nothing"}</span></div>
        <div className="row"><span>Watch heart rate at night</span><span className="mute" style={{ textAlign: "right" }}>{!hrOn ? "Off" : watch?.seen ? `On · last batch ${agoText(now - watch.seen)}` : "On · nothing yet"}</span></div>
        <div className="row"><span>Watch or ring</span><span className="mute">{brand ?? "None picked"}</span></div>
      </div>
      <span className="faint" style={{ fontSize: 12.5 }}>Change these in the phone app: Settings → From this phone, and Watch or ring. Devices that report through Home Assistant are in <Link to="/studio/devices">Devices</Link>.</span>
    </Fold>
  );
}
