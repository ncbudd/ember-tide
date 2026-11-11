import { useEffect, useState } from "react";
import { api } from "../api";
import { clock } from "../format";
import Sheet from "./Sheet";

/** What GET /api/people/{pid}/compare returns (engine/compare.py). */
export interface Compare {
  date: string; available: boolean; trust?: string; has_wearable?: boolean; corrected?: boolean; wearable_name?: string | null;
  lines?: string[]; disagree?: string[]; using?: string[]; stage_agreement?: number | null;
  times?: Record<string, { ts: number; source: string }>;
  fix?: Fix | null; offset?: { nights: number; in?: number; out?: number };
}
interface Span { start: number; end: number }
interface Stretch extends Span { who: "pet" | "me"; pet?: string | null }
interface Fix { in_bed_at?: number | null; asleep_at?: number | null; woke_at?: number | null; out_at?: number | null; ups_wrong?: Span[]; ups_added?: Span[]; stretches?: Stretch[] }

const TIMES: [keyof Fix & string, string][] = [["in_bed_at", "Got into bed"], ["asleep_at", "Fell asleep"], ["woke_at", "Woke up"], ["out_at", "Got up"]];

/** Unix seconds for a wall-clock time in the household's time zone (not the phone's: travelling doesn't shift fixes). */
function zonedTs(y: number, mo: number, d: number, h: number, mi: number, tz: string): number {
  const guess = Date.UTC(y, mo - 1, d, h, mi) / 1000;
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .formatToParts(new Date(guess * 1000)).map(x => [x.type, x.value]));
  const back = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute) / 1000;
  return guess - (back - guess);
}

/** "HH:MM" on the night of `date` (the evening's date), in the household's zone. Morning-ish fields (woke, got up)
 *  count as the next day until 6 PM, so a lie-in to 12:30 PM still lands after the night; others flip at noon. */
function toTs(date: string, hhmm: string, tz: string, morning = false): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm);
  const dm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!m || !dm) return null;
  const h = Number(m[1]);
  const next = h < (morning ? 18 : 12);
  const day = new Date(Date.UTC(+dm[1], +dm[2] - 1, +dm[3] + (next ? 1 : 0)));
  return zonedTs(day.getUTCFullYear(), day.getUTCMonth() + 1, day.getUTCDate(), h, Number(m[2]), tz);
}
function toHm(ts: number | null | undefined, tz: string): string {
  if (!ts) return "";
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: tz, hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .formatToParts(new Date(ts * 1000)).map(x => [x.type, x.value]));
  return `${p.hour}:${p.minute}`;
}
const MORNING = new Set(["woke_at", "out_at"]);

/** The bed next to the watch or ring for one night, plus "Fix this night". Sleep screen and Studio's night view. */
export default function NightFix({ person, date, tz, ups, pets, onChanged }: {
  person: string; date: string; tz: string; ups: { ts: number; minutes?: number }[]; pets: { id: string; name: string }[]; onChanged?: () => void;
}) {
  const [cmp, setCmp] = useState<Compare | null>(null);
  const [open, setOpen] = useState(false);
  const load = () => api.get<Compare>(`/api/people/${person}/compare?date=${date}`).then(setCmp).catch(() => setCmp(null));
  useEffect(() => { void load(); }, [person, date]);
  if (!cmp?.available) return null;
  return (
    <div className="card nightfix">
      <div className="row" style={{ alignItems: "flex-start", borderBottom: 0, padding: 0 }}>
        <div style={{ display: "grid", gap: 3 }}>
          <span className="k">{cmp.has_wearable ? `Bed and ${cmp.wearable_name?.toLowerCase() ?? "watch"}` : "Your night's times"}</span>
          {(cmp.lines ?? []).map(l => <span key={l} style={{ fontSize: 14 }}>{l}</span>)}
          {cmp.stage_agreement != null && <span style={{ fontSize: 14 }}>Asleep vs awake agree {cmp.stage_agreement}% of the time</span>}
          {!cmp.has_wearable && <span className="mute" style={{ fontSize: 13 }}>No watch or ring data for this night.</span>}
          {(cmp.disagree ?? []).length > 0 && <span className="mute" style={{ fontSize: 13 }}>Where they differ: {cmp.disagree!.join(" · ")}</span>}
          {(cmp.using ?? []).length > 0 && <span className="faint" style={{ fontSize: 12.5 }}>{cmp.using!.join(" · ")}</span>}
          {cmp.corrected && <span style={{ fontSize: 12.5 }}>✎ You corrected this night</span>}
        </div>
      </div>
      <button className="btn" style={{ minHeight: 48, justifySelf: "end" }} onClick={() => setOpen(true)}>{cmp.corrected ? "Change my fixes" : "Fix this night"}</button>
      {open && <FixSheet person={person} date={date} tz={tz} cmp={cmp} ups={ups} pets={pets}
        onClose={() => setOpen(false)} onSaved={() => { setOpen(false); void load(); onChanged?.(); }} />}
    </div>
  );
}

function FixSheet({ person, date, tz, cmp, ups, pets, onClose, onSaved }: {
  person: string; date: string; tz: string; cmp: Compare; ups: { ts: number; minutes?: number }[]; pets: { id: string; name: string }[];
  onClose: () => void; onSaved: () => void;
}) {
  const f = cmp.fix ?? {};
  const [times, setTimes] = useState<Record<string, string>>(() => Object.fromEntries(TIMES.map(([k]) =>
    [k, toHm((f[k] as number | null | undefined) ?? cmp.times?.[k]?.ts, tz)])));
  const [wrong, setWrong] = useState<Span[]>(f.ups_wrong ?? []);
  const [added, setAdded] = useState<Span[]>(f.ups_added ?? []);
  const [stretches, setStretches] = useState<Stretch[]>(f.stretches ?? []);
  const [draft, setDraft] = useState({ from: "", to: "", kind: "up" as "up" | "pet" | "me", pet: pets[0]?.id ?? "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isWrong = (ts: number) => wrong.some(w => w.start - 300 <= ts && ts <= w.end + 300);
  const addDraft = () => {
    const a = toTs(date, draft.from, tz), b = toTs(date, draft.to, tz);
    if (a == null || b == null || b <= a) { setErr("Pick a start and an end after it."); return; }
    setErr(null);
    if (draft.kind === "up") setAdded([...added, { start: a, end: b }]);
    else setStretches([...stretches, { start: a, end: b, who: draft.kind, pet: draft.kind === "pet" ? draft.pet || null : null }]);
    setDraft({ ...draft, from: "", to: "" });
  };
  const save = async () => {
    setBusy(true); setErr(null);
    const body: Record<string, unknown> = { date, ups_wrong: wrong, ups_added: added, stretches };
    for (const [k] of TIMES) {
      const orig = toHm(cmp.times?.[k]?.ts, tz);
      if (times[k] && (times[k] !== orig || f[k] != null)) body[k] = toTs(date, times[k], tz, MORNING.has(k));
    }
    try { await api.put(`/api/people/${person}/night-fix`, body); onSaved(); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const clear = async () => {
    setBusy(true);
    try { await api.del(`/api/people/${person}/night-fix?date=${date}`); onSaved(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const label = (s: Span) => `${clock(s.start, tz)}–${clock(s.end, tz)}`;
  return (
    <Sheet onClose={onClose} locked={busy} form role="dialog" aria-label="Fix this night">
      <h2 style={{ fontSize: 20 }}>Fix this night</h2>
      <p className="mute" style={{ fontSize: 13 }}>Nothing is erased: your fixes sit alongside what the bed and watch recorded, and your summaries and Autopilot use your fixes.</p>
      <div className="card list">
        {TIMES.map(([k, l]) => (
          <div className="row" key={k}><span>{l}{cmp.times?.[k] && <><br /><small className="mute">from {cmp.times[k].source === "you" ? "your fix" : cmp.times[k].source}</small></>}</span>
            <input type="time" aria-label={l} value={times[k]} onChange={e => setTimes({ ...times, [k]: e.target.value })} style={{ minHeight: 44 }} /></div>
        ))}
      </div>
      {(ups.length > 0 || wrong.some(w => !ups.some(u => w.start - 300 <= u.ts && u.ts <= w.end + 300))) && <div className="card list">
        <span className="k" style={{ paddingTop: 8 }}>Times you were up</span>
        {/* saved "I wasn't up" marks: those moments are gone from the night, so list them here to undo one by one */}
        {wrong.filter(w => !ups.some(u => w.start - 300 <= u.ts && u.ts <= w.end + 300)).map(w => (
          <div className="row" key={`w${w.start}`} style={{ minHeight: 52 }}><span>{clock(w.start, tz)} · {Math.max(1, Math.round((w.end - w.start) / 60))} min<br /><small className="mute">You said you weren't up</small></span>
            <button type="button" className="pill" onClick={() => setWrong(wrong.filter(x => x !== w))}>I was up after all</button></div>
        ))}
        {ups.map(u => (
          <label className="row" key={u.ts} style={{ minHeight: 52 }}><span>{clock(u.ts, tz)} · {u.minutes} min</span>
            <span style={{ display: "flex", gap: 8, alignItems: "center" }}><small className="mute">I wasn't up</small>
              <input type="checkbox" className="toggle" checked={isWrong(u.ts)} onChange={e => setWrong(e.target.checked
                ? [...wrong, { start: u.ts, end: u.ts + (u.minutes ?? 1) * 60 }] : wrong.filter(w => !(w.start - 300 <= u.ts && u.ts <= w.end + 300)))} /></span></label>
        ))}
      </div>}
      <div className="card list">
        <span className="k" style={{ paddingTop: 8 }}>Add a stretch of time</span>
        <div className="seg" role="group" aria-label="What happened">
          <button aria-pressed={draft.kind === "up"} onClick={() => setDraft({ ...draft, kind: "up" })}>I was up</button>
          {pets.length > 0 && <button aria-pressed={draft.kind === "pet"} onClick={() => setDraft({ ...draft, kind: "pet" })}>That was the pet</button>}
          <button aria-pressed={draft.kind === "me"} onClick={() => setDraft({ ...draft, kind: "me" })}>That was me</button>
        </div>
        {draft.kind === "pet" && pets.length > 1 && <div className="row"><span>Which pet</span>
          <select value={draft.pet} onChange={e => setDraft({ ...draft, pet: e.target.value })}>{pets.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>}
        <div className="row"><span>From</span><input type="time" aria-label="From" value={draft.from} onChange={e => setDraft({ ...draft, from: e.target.value })} style={{ minHeight: 44 }} /></div>
        <div className="row"><span>To</span><input type="time" aria-label="To" value={draft.to} onChange={e => setDraft({ ...draft, to: e.target.value })} style={{ minHeight: 44 }} /></div>
        <button className="btn" style={{ minHeight: 48 }} onClick={addDraft}>Add</button>
        {[...added.map(s => ({ s, t: "Up", rm: () => setAdded(added.filter(x => x !== s)) })),
          ...stretches.map(s => ({ s, t: s.who === "pet" ? (pets.find(p => p.id === s.pet)?.name ?? "The pet") : "Me", rm: () => setStretches(stretches.filter(x => x !== s)) }))].map(({ s, t, rm }) => (
          <div className="row" key={`${t}${s.start}`}><span>{t} · {label(s)}</span><button className="pill" onClick={rm}>Remove</button></div>
        ))}
      </div>
      {err && <p role="alert" style={{ fontSize: 13 }}>{err}</p>}
      {cmp.corrected && <button className="link" style={{ justifySelf: "start", minHeight: 44 }} disabled={busy} onClick={clear}>Undo all my fixes for this night</button>}
      <div className="sheet-actions">
        <button className="btn" style={{ minHeight: 48 }} disabled={busy} onClick={onClose}>Cancel</button>
        <button className="btn pri" style={{ minHeight: 48 }} disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
      </div>
    </Sheet>
  );
}
