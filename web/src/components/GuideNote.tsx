import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";
import { hm12 } from "../format";
import { useSwipeAway } from "./useSwipeAway";

interface Guide {
  id: string; kind?: string; severity?: string; title: string; detail: string; evidence?: string[]; dismissed?: boolean;
  proposal: { rhythm_id: string; schedule?: string; from?: string | number; to: string | number } | null;
}

/** A tuning-guide suggestion on Tonight ("you usually get into bed 67 min after your set bedtime"), open by default
 *  with its reason, the nights it comes from, and the choice right there: change the time, or not now. Swiping the card
 *  away is the same as Not now. "Not now" is remembered on the server for this person until the suggestion changes. */
export default function GuideNote({ person }: { person: string }) {
  const { say } = useApp();
  const [items, setItems] = useState<Guide[]>([]);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.get<Guide[]>(`/api/people/${person}/insights`)
    .then(r => setItems(r.filter(i => i.kind === "guide" && i.severity === "suggest" && i.proposal?.schedule && !i.dismissed && !(i as { applied?: unknown }).applied)))
    .catch(() => { /* offline: try later */ });
  useEffect(() => {
    void load();
    const vis = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", vis);
    return () => document.removeEventListener("visibilitychange", vis);
  }, [person]);
  const g = items[0];
  const notNow = () => {
    if (!g) return;
    setItems(items.filter(i => i.id !== g.id));
    swipe.reset();
    api.post(`/api/people/${person}/insights/dismiss`, { id: g.id, sig: String(g.proposal?.to ?? "") }).catch(() => { /* hidden here anyway */ });
  };
  const swipe = useSwipeAway(notNow);
  if (done) return <div className="card guidenote" role="status"><p className="gn-title">{done}</p></div>;
  if (!g) return null;
  const apply = async () => {
    setBusy(true);
    try {
      await api.post(`/api/people/${person}/insights/apply`, { proposal: g.proposal, insight_id: g.id, source: "guide" });
      const msg = `${g.proposal!.schedule === "bedtime" ? "Bedtime" : "Wake time"} is now ${hm12(String(g.proposal!.to))}.`;
      setDone(msg); say(msg);
      window.setTimeout(() => { setDone(null); void load(); }, 4000);
    } catch (e) { say(`Couldn't do that: ${(e as Error).message}`); }
    setBusy(false);
  };
  return (
    <div className="card guidenote" role="region" aria-label="A suggestion for your schedule" style={swipe.style} {...swipe.handlers}>
      <div className="row"><div className="k">A suggestion{items.length > 1 ? ` · 1 of ${items.length}` : ""}</div></div>
      <p className="gn-title">{g.title}</p>
      <p className="gn-detail">{g.detail}</p>
      {!!g.evidence?.length && <ul className="gn-ev">{g.evidence.slice(-5).map(e => <li key={e}>{e}</li>)}</ul>}
      <div className="gn-actions">
        <button className="btn" onClick={notNow}>Not now</button>
        <button className="btn pri" disabled={busy} onClick={apply}>Change to {hm12(String(g.proposal!.to))}</button>
      </div>
    </div>
  );
}
