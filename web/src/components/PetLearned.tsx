import { useState } from "react";
import { mins } from "../format";
import { sideTitle } from "./BedSidePicker";
import ShowMore from "./ShowMore";

/** What the system has learned about one pet, read-only (server: GET /api/pets/learned), shown under the pet in the
 *  Pets section. Owner can forget it (DELETE /api/pets/{id}/learned) after a confirm. */
export interface PetReport {
  id: string; name: string; distinct: boolean | null; partial: boolean;
  learned: { hr: number | null; hr_sd: number | null; br: number | null; br_sd: number | null; readings: number; minutes: number; stays: number | null; updated: number | null } | null;
  visits: { start: number; end: number; zone: string; seconds: number }[];
  waiting: { count: number; next_at: number | null };
}

const DAY = new Intl.DateTimeFormat([], { weekday: "long" });
const DAY_SHORT = new Intl.DateTimeFormat([], { weekday: "short" });

/** "Mon night" for an evening or small-hours visit (small hours belong to the evening before), else "Mon daytime". */
export function visitWhen(ts: number): string {
  const d = new Date(ts * 1000), h = d.getHours();
  if (h < 6) d.setDate(d.getDate() - 1);
  return `${DAY_SHORT.format(d)} ${h < 6 || h >= 18 ? "night" : "daytime"}`;
}
/** "today", "yesterday" or the weekday: when it was last updated. */
export function whenText(ts: number, now = Date.now() / 1000): string {
  const a = new Date(ts * 1000), b = new Date(now * 1000);
  const days = Math.round((new Date(b.getFullYear(), b.getMonth(), b.getDate()).getTime() - new Date(a.getFullYear(), a.getMonth(), a.getDate()).getTime()) / 86400000);
  return days <= 0 ? "today" : days === 1 ? "yesterday" : days < 7 ? DAY.format(a) : a.toLocaleDateString([], { month: "short", day: "numeric" });
}
const clockText = (ts: number) => new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

export default function PetLearned({ r, canForget, busy, onForget }: { r: PetReport; canForget: boolean; busy: boolean; onForget: () => void }) {
  const [ask, setAsk] = useState(false);
  const l = r.learned, w = r.waiting;
  const readings = l && [l.hr != null ? `Heart rate around ${l.hr}` : null, l.br != null ? `breathing around ${l.br}` : null].filter(Boolean).join(", ");
  const basis = l && (l.stays ? `learned from ${l.stays} stay${l.stays === 1 ? "" : "s"}` : `learned from about ${Math.max(1, l.minutes)} minutes of readings`);
  return (
    <div className="pet-learned">
      {l
        ? <p className="pet-l-main">{readings} · {basis}{l.updated ? ` · last updated ${whenText(l.updated)}` : ""}</p>
        : <p className="mute pet-l-main">Nothing learned yet. This fills in after a few stays you confirm were {r.name}.</p>}
      {l && r.distinct === false && <p className="pet-l-note">{r.name}'s readings look a lot like an adult's, so the bed leans on the radar and your watch to tell you apart.</p>}
      {r.visits.length > 0 && <>
        <span className="k pet-l-k">Recent visits you confirmed</span>
        <ShowMore items={r.visits} limit={3} noun="visits" render={shown => (
          <ul className="pet-l-visits">{shown.map(v => <li key={v.start}>{visitWhen(v.start)} · {v.zone === "left" || v.zone === "right" ? sideTitle(v.zone).toLowerCase() : "whole bed"} · {mins(v.seconds)}</li>)}</ul>)} />
      </>}
      {w.count > 0 && <p className="pet-l-wait">{w.count} stay{w.count === 1 ? "" : "s"} waiting to be learned{w.next_at ? ` · the first ${w.next_at * 1000 <= Date.now() ? "is learned shortly" : `is learned ${whenText(w.next_at)} around ${clockText(w.next_at)}`}` : ""}</p>}
      {canForget && (l || w.count > 0) && (ask
        ? <span className="pet-sum-btns"><span className="pet-q">Forget what's been learned about {r.name}?</span>
          <button type="button" className="btn" disabled={busy} onClick={() => setAsk(false)}>Keep it</button>
          <button type="button" className="btn pet-rm" disabled={busy} onClick={() => { setAsk(false); onForget(); }}>Forget</button></span>
        : <button type="button" className="btn pet-l-forget" disabled={busy} onClick={() => setAsk(true)}>Forget what's been learned</button>)}
    </div>
  );
}
