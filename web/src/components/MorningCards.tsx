import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../ctx";
import { hm12, temp } from "../format";
import { useSwipeAway } from "./useSwipeAway";

interface Proposal { rhythm_id: string; phase_id?: string; field?: string; schedule?: string; from?: number | string; to: number | string }
interface Sugg { id: string; source: "suggestion" | "guide"; kind?: string; title: string; detail: string; proposal: Proposal | null }
interface Card {
  id: string; kind: "night" | "review" | "sugg";
  line?: string; in_bed_min?: number; settle_min?: number | null; times_up?: number; feel?: string | null;
  state?: "ready" | "working"; answer?: string; model?: string | null; proposal?: Proposal | null;
  items?: Sugg[];
}
interface Feed { night: string; cards: Card[] }

/** A phase's own name ("Fall asleep"), not its id ("fall_asleep"). */
export function phaseLabel(id: string, phases: { id: string; name: string }[]): string {
  return phases.find(p => p.id === id)?.name ?? (id.charAt(0).toUpperCase() + id.slice(1)).replace(/_/g, " ");
}

const FEELS: [string, string][] = [["cold", "Too cold"], ["right", "Just right"], ["warm", "Too warm"]];
const THANKS: Record<string, string> = { cold: "Noted: too cold. It tunes Autopilot.", right: "Noted: just right.", warm: "Noted: too warm. It tunes Autopilot." };

/** The morning cards show between getting up and the next night starting. */
export function morningVisible(state: string): boolean {
  return state !== "rhythm" && state !== "out_of_bed_hold";
}

const hm = (min?: number | null) => (min == null ? "—" : `${Math.floor(min / 60)} h ${String(Math.round(min % 60)).padStart(2, "0")}`);

/**
 * "This morning" on Tonight: last night in brief with the check-in, its review (shown at once as "Reviewing your
 * night…" and filled in when ready), and tonight's suggestions with their choices right on the card. Each card goes
 * away with a swipe or its × button, on every device (the server keeps it). All cards show together, unlike the one
 * calm note below them.
 */
export default function MorningCards({ person, name, state, units, phases }: {
  person: string; name: string; state: string; units: "F" | "C"; phases: { id: string; name: string }[];
}) {
  const [feed, setFeed] = useState<Feed | null>(null);
  const working = !!feed?.cards.some(c => c.kind === "review" && c.state === "working");
  useEffect(() => {
    let live = true;
    const load = () => api.get<Feed>(`/api/people/${person}/morning`).then(f => { if (live) setFeed(f); }).catch(() => { /* offline: try later */ });
    void load();
    const t = window.setInterval(load, working ? 30_000 : 120_000);
    const vis = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", vis);
    return () => { live = false; window.clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, [person, working]);
  if (!feed || !morningVisible(state) || feed.cards.length === 0) return null;
  const away = (id: string) => {
    setFeed(f => (f ? { ...f, cards: f.cards.filter(c => c.id !== id) } : f));
    api.post(`/api/people/${person}/morning/dismiss`, { id }).catch(() => { /* hidden here anyway */ });
  };
  const patch = (id: string, p: Partial<Card>) => setFeed(f => (f ? { ...f, cards: f.cards.map(c => (c.id === id ? { ...c, ...p } : c)) } : f));
  return (
    <section className="mcards" aria-label="This morning">
      <div className="mcards-head"><span className="mcards-t">This morning</span><span className="faint">Swipe a card away when you're done</span></div>
      {feed.cards.map(c => (
        <MCard key={c.id} card={c} onAway={() => away(c.id)}>
          {c.kind === "night" && <NightBody c={c} name={name} person={person} night={feed.night} onFeel={f => patch(c.id, { feel: f })} onDone={() => away(c.id)} />}
          {c.kind === "review" && <ReviewBody c={c} person={person} units={units} phases={phases} />}
          {c.kind === "sugg" && <SuggBody c={c} person={person} units={units} onLeft={items => (items.length ? patch(c.id, { items }) : away(c.id))} />}
        </MCard>
      ))}
    </section>
  );
}

const KICK: Record<Card["kind"], [string, string]> = { night: ["Last night", "mc-dot ember"], review: ["Review", "mc-dot tide"], sugg: ["Suggestion", "mc-dot good"] };

function MCard({ card, onAway, children }: { card: Card; onAway: () => void; children: React.ReactNode }) {
  const swipe = useSwipeAway(onAway);
  const [label, dot] = KICK[card.kind];
  const n = card.kind === "sugg" && (card.items?.length ?? 0) > 1 ? ` · ${card.items!.length}` : "";
  return (
    <div className="card mcard" role="region" aria-label={label} style={swipe.style} {...swipe.handlers}>
      <div className="mc-kick"><span className={dot} aria-hidden="true" /><span className="k">{label}{n}</span>
        <button className="mc-x" aria-label={`Put away: ${label}`} onClick={onAway}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button></div>
      {children}
    </div>
  );
}

function NightBody({ c, name, person, night, onFeel, onDone }: { c: Card; name: string; person: string; night: string; onFeel: (f: string) => void; onDone: () => void }) {
  const { say } = useApp();
  const feel = async (f: string) => {
    onFeel(f);
    try { await api.post(`/api/people/${person}/feedback`, { date: night, temp: f }); }
    catch { say("Couldn't save that"); return; }       // the card stays so it can be tried again
    setTimeout(onDone, 1500);                          // a moment to read the thanks, then the card goes away (Sleep, Review keeps it)
  };
  return <>
    <h2 className="mc-h">Good morning, {name}</h2>
    <div className="mc-stats">
      <div><b>{hm(c.in_bed_min)}</b><span>in bed</span></div>
      <div><b>{c.settle_min == null ? "—" : `${c.settle_min} min`}</b><span>to settle</span></div>
      <div><b>{c.times_up ?? 0}</b><span>times up</span></div>
    </div>
    <span className={c.feel ? "mc-ok" : "faint"} style={{ fontSize: 13 }}>{c.feel ? THANKS[c.feel] : "How did the temperature feel?"}</span>
    <div className="mc-feels" role="group" aria-label="How did the temperature feel?">
      {FEELS.map(([id, label]) => <button key={id} className={`mc-feel${c.feel === id ? " on" : ""}`} aria-pressed={c.feel === id} onClick={() => feel(id)}>{label}</button>)}
    </div>
    <Link className="mc-more" to={`/sleep?night=${night}`}>See the whole night ›</Link>
  </>;
}

function ReviewBody({ c, person, units, phases }: { c: Card; person: string; units: "F" | "C"; phases: { id: string; name: string }[] }) {
  const { say } = useApp();
  if (c.state === "working") return <>
    <h2 className="mc-h">Reviewing your night…</h2>
    <div className="mc-shimmer" aria-hidden="true"><i style={{ width: "92%" }} /><i style={{ width: "78%" }} /><i style={{ width: "55%" }} /></div>
    <span className="faint" style={{ fontSize: 13 }}>Usually ready a minute or two after you get up. This card fills in by itself.</span>
  </>;
  const apply = async () => {
    try { const x = await api.post<{ applied: string }>(`/api/people/${person}/insights/apply`, { proposal: c.proposal, source: "autopilot" }); say(`Applied: ${x.applied}`); }
    catch (e) { say(`Couldn't apply: ${(e as Error).message}`); }
  };
  return <>
    <h2 className="mc-h">Your night, reviewed</h2>
    <p className="revtext">{c.answer}</p>
    {c.proposal?.phase_id && typeof c.proposal.to === "number" && <div className="row">
      <span style={{ fontSize: 13 }}>Suggested: {phaseLabel(c.proposal.phase_id, phases)} at {temp(c.proposal.to, units, units === "C" ? 1 : 0)}</span>
      <button className="btn pri" style={{ minHeight: 44 }} onClick={apply}>Apply</button></div>}
    <Link className="mc-more" to="/sleep#review">Read the review and why ›</Link>
  </>;
}

function suggLine(s: Sugg, units: "F" | "C"): string | null {
  const p = s.proposal;
  if (!p) return null;
  if (p.schedule) return `${p.schedule === "bedtime" ? "Bedtime" : "Wake"} ${p.from ? `${hm12(String(p.from))} → ` : ""}${hm12(String(p.to))}`;
  if (typeof p.to === "number") return `${typeof p.from === "number" ? `${temp(p.from, units)} → ` : ""}${temp(p.to, units)}`;
  return null;
}

function SuggBody({ c, person, units, onLeft }: { c: Card; person: string; units: "F" | "C"; onLeft: (items: Sugg[]) => void }) {
  const { say } = useApp();
  const [done, setDone] = useState<Record<string, string>>({});
  const items = c.items ?? [];
  const apply = async (s: Sugg) => {
    try { await api.post(`/api/people/${person}/insights/apply`, { proposal: s.proposal, insight_id: s.id, source: s.source }); setDone(d => ({ ...d, [s.id]: "Added to your rhythm. Change it any time under Rhythm." })); }
    catch (e) { say(`Couldn't do that: ${(e as Error).message}`); }
  };
  const later = (s: Sugg) => {
    onLeft(items.filter(x => x.id !== s.id));
    (s.source === "guide"
      ? api.post(`/api/people/${person}/insights/dismiss`, { id: s.id, sig: String(s.proposal?.to ?? "") })
      : api.post(`/api/people/${person}/autopilot/suggestions/${s.id}/dismiss`, { forever: false })).catch(() => { /* hidden here anyway */ });
  };
  return <>
    {items.map(s => { const line = suggLine(s, units); return (
      <div key={s.id} className="mc-sugg">
        <h2 className="mc-h">{s.title}</h2>
        {line && <span className="mc-line">{line}</span>}
        <p className="mc-p">{s.detail}</p>
        {done[s.id] ? <span className="mc-ok">{done[s.id]}</span> : <div className="mc-acts">
          <button className="btn pri" style={{ minHeight: 44 }} onClick={() => apply(s)}>Apply</button>
          <button className="btn" style={{ minHeight: 44 }} onClick={() => later(s)}>Not now</button>
          <Link className="mc-more" to="/sleep#review">Why? ›</Link>
        </div>}
      </div>); })}
  </>;
}
