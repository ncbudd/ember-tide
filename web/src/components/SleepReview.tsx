import { useEffect, useState } from "react";
import { api, useVisibleInterval } from "../api";
import { hm12, temp } from "../format";

interface Proposal { rhythm_id: string; phase_id?: string; field?: string; schedule?: string; from?: number | string; to: number | string }
interface Sugg { id: string; source: "suggestion" | "guide"; kind?: string; title: string; detail: string; proposal: Proposal | null }
interface Applied { id: string; change: string | null; ts: number | null; can_undo: boolean }
interface Page {
  date: string; last: boolean; has_data: boolean; feel: string | null; review: { answer: string; model?: string | null } | null;
  reviewing: boolean; review_on: boolean; basis: string[]; suggestions: Sugg[]; applied: Applied[];
}

const FEELS: [string, string][] = [["cold", "Too cold"], ["right", "Just right"], ["warm", "Too warm"]];
const SAVED: Record<string, string> = { cold: "Saved: too cold. Tonight runs a touch warmer.", right: "Saved: just right.", warm: "Saved: too warm. Tonight runs a touch cooler." };

function change(s: Sugg, units: "F" | "C"): string | null {
  const p = s.proposal;
  if (!p) return null;
  if (p.schedule) return `${p.schedule === "bedtime" ? "Bedtime" : "Wake"} ${p.from ? `${hm12(String(p.from))} → ` : ""}${hm12(String(p.to))}`;
  if (typeof p.to === "number") return `${typeof p.from === "number" ? `${temp(p.from, units)} → ` : ""}${temp(p.to, units)}`;
  return null;
}

/**
 * Sleep → Review & suggestions for one night: how it felt (changeable), the review with what it's based on, tonight's
 * suggestions with Apply / Not now / Don't suggest this (night that just ended only), and what was applied from it,
 * undoable for a week. Notifications and Tonight's cards link here (/sleep#review).
 */
export default function SleepReview({ pid, date, label, older, newer, onDate, units, say }: {
  pid: string; date: string | null; label: string; older: string | null; newer: string | null; onDate: (d: string) => void;
  units: "F" | "C"; say: (m: string) => void;
}) {
  const [page, setPage] = useState<Page | null>(null);
  const [why, setWhy] = useState(false);
  const [done, setDone] = useState<Record<string, string>>({});
  const load = () => api.get<Page>(`/api/people/${pid}/review${date ? `?date=${date}` : ""}`).then(setPage).catch(() => { /* offline: keep what's shown */ });
  useEffect(() => { setDone({}); void load(); }, [pid, date]);
  useVisibleInterval(load, page?.reviewing ? 30_000 : null, [pid, date]);
  if (!page) return <p className="mute">Loading…</p>;
  const feel = async (f: string) => {
    setPage({ ...page, feel: f });
    try { await api.post(`/api/people/${pid}/feedback`, { date: page.date, temp: f }); } catch { say("Couldn't save that"); }
  };
  const apply = async (s: Sugg) => {
    try { await api.post(`/api/people/${pid}/insights/apply`, { proposal: s.proposal, insight_id: s.id, source: s.source }); setDone(d => ({ ...d, [s.id]: "Applied for tonight" })); void load(); }
    catch (e) { say(`Couldn't do that: ${(e as Error).message}`); }
  };
  const later = async (s: Sugg, forever: boolean) => {
    try {
      if (s.source === "guide") await api.post(`/api/people/${pid}/insights/dismiss`, { id: s.id, sig: String(s.proposal?.to ?? "") });
      else await api.post(`/api/people/${pid}/autopilot/suggestions/${s.id}/dismiss`, { forever });
      setDone(d => ({ ...d, [s.id]: !forever ? "Okay, not tonight" : s.kind === "manual" ? "Won't suggest your own changes again. Turn it back on in Studio → Autopilot." : "Won't suggest this again" }));
    } catch (e) { say(`Couldn't do that: ${(e as Error).message}`); }
  };
  const undo = async (a: Applied) => {
    try {
      const r = await api.post<{ undone: string }>(`/api/people/${pid}/insights/undo`, { insight_id: a.id });
      say(`Undone: ${r.undone}`);
      setDone(d => { const x = { ...d }; delete x[a.id]; return x; });       // the suggestion can come back as new
      void load();
    }
    catch (e) { say((e as Error).message); }
  };
  return (
    <div className="srev">
      <div className="daynav">
        <button aria-label="Previous night" disabled={!older} onClick={() => older && onDate(older)}>‹</button>
        <div style={{ textAlign: "center" }}><h1 style={{ fontSize: 22 }}>{label}</h1><span className="mute" style={{ fontSize: 12.5 }}>night of</span></div>
        <button aria-label="Next night" disabled={!newer} onClick={() => newer && onDate(newer)}>›</button>
      </div>

      {page.has_data && <div className="card">
        <span className="k">How it felt</span>
        <div className="mc-feels" role="group" aria-label="How did the temperature feel?">
          {FEELS.map(([id, l]) => <button key={id} className={`mc-feel${page.feel === id ? " on" : ""}`} aria-pressed={page.feel === id} onClick={() => feel(id)}>{l}</button>)}
        </div>
        <span className={page.feel ? "mc-ok" : "faint"} style={{ fontSize: 13 }}>{page.feel ? SAVED[page.feel] : "Tap how it felt. It tunes Autopilot."}</span>
      </div>}

      <div className="card" id="review">
        <span className="k">Review</span>
        {page.review ? <>
          <p className="srev-text">{page.review.answer}</p>
          {page.basis.length > 0 && <>
            <button type="button" className="link srev-why" aria-expanded={why} onClick={() => setWhy(v => !v)}>{why ? "Hide what this is based on" : "What is this based on?"}</button>
            {why && <ul className="srev-basis">{page.basis.map(b => <li key={b}>{b}</li>)}</ul>}
          </>}
          {page.review.model && <small className="faint" style={{ fontSize: 11.5 }}>Written by {page.review.model}</small>}
        </> : page.reviewing ? <p className="mute">Reviewing your night… This fills in by itself in a minute or two.</p>
          : !page.review_on ? <p className="mute">Morning reviews are off. Turn them on in Studio → Autopilot ("Every morning").</p>
          : <p className="mute">No review for this night.</p>}
      </div>

      {page.last && <>
        <span className="k" style={{ marginTop: 4 }}>Suggestions for tonight</span>
        {page.suggestions.length === 0 && <p className="mute" style={{ margin: 0 }}>{page.applied.length ? "Nothing else to change tonight." : "Nothing to change tonight."}</p>}
        {page.suggestions.map(s => { const line = change(s, units); return (
          <div className="card" key={s.id}>
            <h2 className="mc-h">{s.title}</h2>
            {line && <span className="mc-line">{line}</span>}
            <p className="mc-p">{s.detail}</p>
            {done[s.id] ? <span className="mc-ok">{done[s.id]}</span> : <div className="mc-acts">
              <button className="btn pri" style={{ minHeight: 44 }} onClick={() => apply(s)}>Apply</button>
              <button className="btn" style={{ minHeight: 44 }} onClick={() => later(s, false)}>Not now</button>
              {s.source !== "guide" && <button className="link" style={{ minHeight: 44 }} onClick={() => later(s, true)}>Don't suggest this</button>}
            </div>}
          </div>); })}
      </>}

      {page.applied.length > 0 && <div className="card">
        <span className="k">Changed from this night</span>
        {page.applied.map(a => (
          <div key={a.id} className="srev-applied">
            <span>{a.change ?? "A suggestion was applied"}</span>
            {a.can_undo && <button className="btn" style={{ minHeight: 44 }} onClick={() => undo(a)}>Undo</button>}
          </div>))}
        <span className="faint" style={{ fontSize: 12 }}>{page.applied.some(a => a.can_undo) ? "Changes can be undone here for 7 days, or changed any time under Rhythm." : "Change it any time under Rhythm."}</span>
      </div>}
    </div>
  );
}
