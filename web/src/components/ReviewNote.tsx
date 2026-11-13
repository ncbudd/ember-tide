import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";

interface Proposal { rhythm_id: string; phase_id: string; field: string; from?: number; to: number }
interface Review { answer?: string; proposal?: Proposal | null; model?: string | null; date?: string; dismissed?: boolean }

/** Pure rule for whether the card shows: the review is on, there is a review for last night, it wasn't dismissed, and the
 *  next night hasn't started. */
export function reviewVisible(r: Review | null, on: boolean, state: string, hidden: boolean): boolean {
  return on && !!r?.answer && !r.dismissed && !hidden && state !== "rhythm" && state !== "out_of_bed_hold";
}

/** Should a finished horizontal drag dismiss the card? A drag past 30% of the card's width, or a quick flick. */
export function swipeDismisses(dx: number, width: number, ms: number): boolean {
  const far = Math.abs(dx) >= width * 0.3;
  const flick = Math.abs(dx) >= 40 && Math.abs(dx) / Math.max(ms, 1) >= 0.5;
  return far || flick;
}

/**
 * "Last night's review": the morning AI review on Tonight (when Autopilot's "Every morning" is on). Three lines, with
 * Show more; the suggested change can be applied right here. Swipe it away (left or right) or tap Dismiss: that hides
 * this night's review for this person on every device (kept on the server). Vertical scrolling is never taken over:
 * a drag only counts once it is clearly sideways.
 */
export default function ReviewNote({ person, on, state }: { person: string; on: boolean; state: string }) {
  const { say } = useApp();
  const [r, setR] = useState<Review | null>(null);
  const [open, setOpen] = useState(false);
  const [gone, setGone] = useState(false);
  const [dx, setDx] = useState(0);
  const [leaving, setLeaving] = useState(false);
  const drag = useRef<{ x: number; y: number; t: number; on: boolean; w: number } | null>(null);
  useEffect(() => {
    let live = true;
    setGone(false);
    const load = () => api.get<Review>(`/api/people/${person}/autopilot/ai-review/last`).then(x => { if (live) setR(x); }).catch(() => { /* offline: try later */ });
    void load();
    const t = window.setInterval(load, 10 * 60_000);
    const vis = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", vis);
    return () => { live = false; window.clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, [person]);
  if (!reviewVisible(r, on, state, gone)) return null;
  const dismiss = () => {
    setGone(true);
    api.post(`/api/people/${person}/autopilot/ai-review/dismiss`, { date: r!.date }).catch(() => { /* hidden here anyway */ });
  };
  const still = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const finish = (dir: number) => {
    if (still) { dismiss(); return; }
    setLeaving(true); setDx(dir * 600);
    window.setTimeout(dismiss, 220);
  };
  const down = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "mouse") return;                       // mouse: the Dismiss button
    drag.current = { x: e.clientX, y: e.clientY, t: e.timeStamp, on: false, w: e.currentTarget.offsetWidth };
  };
  const move = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const mx = e.clientX - d.x, my = e.clientY - d.y;
    if (!d.on) {
      if (Math.abs(my) > 12 && Math.abs(my) > Math.abs(mx)) { drag.current = null; return; }       // it's a scroll
      if (Math.abs(mx) < 12 || Math.abs(mx) < Math.abs(my) * 1.5) return;
      d.on = true;
      try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* fine without */ }
    }
    setDx(mx);
  };
  const up = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    drag.current = null;
    if (!d?.on) return;
    if (swipeDismisses(dx, d.w, e.timeStamp - d.t)) finish(dx < 0 ? -1 : 1);
    else setDx(0);
  };
  const apply = async () => {
    try { const x = await api.post<{ applied: string }>(`/api/people/${person}/insights/apply`, { proposal: r!.proposal, source: "autopilot" }); say(`Applied: ${x.applied}`); dismiss(); }
    catch (e) { say(`Couldn't apply: ${(e as Error).message}`); }
  };
  const long = (r!.answer ?? "").length > 150;
  return (
    <div className="card revnote" role="region" aria-label="Last night's review"
      style={{ transform: dx ? `translateX(${dx}px)` : undefined, opacity: leaving ? 0 : dx ? Math.max(0.35, 1 - Math.abs(dx) / 500) : undefined,
        transition: drag.current?.on ? "none" : still ? "none" : "transform .2s ease, opacity .2s ease" }}
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={() => { drag.current = null; setDx(0); }}>
      <div className="row"><div className="k">Last night's review</div>
        <button className="icon-btn" aria-label="Dismiss last night's review" onClick={dismiss}>Dismiss</button></div>
      <p className={`revtext${open ? " open" : ""}`}>{r!.answer}</p>
      {long && <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(v => !v)}>{open ? "Show less" : "Show more"}</button>}
      {r!.proposal && <div className="row" style={{ marginTop: 8 }}>
        <span style={{ fontSize: 13 }}>Suggested: {r!.proposal.phase_id} to {Math.round(r!.proposal.to * 10) / 10}°C</span>
        <button className="btn pri" style={{ minHeight: 44 }} onClick={apply}>Apply</button></div>}
      {r!.model && <small className="faint" style={{ display: "block", marginTop: 6, fontSize: 11.5 }}>Written by {r!.model}</small>}
    </div>
  );
}
