import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";
import { swipeDismisses, useSwipeAway } from "./useSwipeAway";

export { swipeDismisses };

interface Proposal { rhythm_id: string; phase_id: string; field: string; from?: number; to: number }
interface Review { answer?: string; proposal?: Proposal | null; model?: string | null; date?: string; dismissed?: boolean }

/** Pure rule for whether the card shows: the review is on, there is a review for last night, it wasn't dismissed, and the
 *  next night hasn't started. */
export function reviewVisible(r: Review | null, on: boolean, state: string, hidden: boolean): boolean {
  return on && !!r?.answer && !r.dismissed && !hidden && state !== "rhythm" && state !== "out_of_bed_hold";
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
  const dismiss = () => {
    setGone(true);
    api.post(`/api/people/${person}/autopilot/ai-review/dismiss`, { date: r?.date }).catch(() => { /* hidden here anyway */ });
  };
  const swipe = useSwipeAway(dismiss);
  if (!reviewVisible(r, on, state, gone)) return null;
  const apply = async () => {
    try { const x = await api.post<{ applied: string }>(`/api/people/${person}/insights/apply`, { proposal: r!.proposal, source: "autopilot" }); say(`Applied: ${x.applied}`); dismiss(); }
    catch (e) { say(`Couldn't apply: ${(e as Error).message}`); }
  };
  const long = (r!.answer ?? "").length > 150;
  return (
    <div className="card revnote" role="region" aria-label="Last night's review"
      style={swipe.style} {...swipe.handlers}>
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
