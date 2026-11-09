import { useEffect, useState } from "react";
import { api } from "../api";
import { pushClear } from "../native";

export interface PetQ {
  id: string; zone: string; kind: "pet" | "unexplained"; status: string; answer: string | null;
  title: string; detail: string; start: number; end: number;
  choices: { answer: "yes" | "me" | "unsure"; pet?: string; label: string }[];
}

/** "Was Biscuit on the bed around 12:40 AM–1:10 AM?" — the one open question for this person, with big answers.
 *  Shown on Tonight, the morning check-in (Sleep) and in Studio. Nothing when there's no question. */
/** Take a pet-question notification off this phone once there's nothing left to answer (answered here, by the
 *  partner for the same visit, or by "Fix this night"). Web Push shows it under the tag et-pet_question-<person>. */
function clearPushed(person: string, native = true) {
  if (native) void pushClear("pet_question");                         // the Android app shows its own notification
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.getRegistration().then(r => r?.getNotifications({ tag: `et-pet_question-${person}` }))
    .then(ns => ns?.forEach(n => n.close())).catch(() => { /* no service worker here: nothing to clear */ });
}

export default function PetQuestion({ person, onAnswered, notifications = true }: { person: string; onAnswered?: () => void;
  /** clear this phone's own pet notification once nothing is left to answer (off where someone else's questions show) */
  notifications?: boolean }) {
  const [q, setQ] = useState<PetQ | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const load = () => api.get<PetQ | null>(`/api/people/${person}/pet-question`).then(x => {
      if (live) setQ(x);
      if (!x) clearPushed(person, notifications);
    }).catch(() => { /* offline: try later */ });
    void load();
    const t = window.setInterval(load, 5 * 60_000);
    const vis = () => { if (document.visibilityState === "visible") void load(); };    // coming back to the app: check again
    document.addEventListener("visibilitychange", vis);
    return () => { live = false; window.clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, [person]);
  if (done) return <div className="card petq" role="status"><p style={{ margin: 0, fontSize: 14 }}>{done}</p></div>;
  if (!q) return null;
  const answer = async (c: PetQ["choices"][number]) => {
    setBusy(true); setErr(null);
    try {
      const r = await api.post<{ text: string }>(`/api/people/${person}/pet-question/${q.id}`, { answer: c.answer, pet: c.pet ?? null });
      setDone(r.text); setQ(null); clearPushed(person, notifications); onAnswered?.();
      window.setTimeout(() => setDone(null), 6000);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="card petq" role="region" aria-label="A question about your pet">
      <div className="k">A quick question</div>
      <p style={{ fontSize: 16, margin: "4px 0 2px", fontWeight: 600 }}>{q.title}</p>
      <p className="mute" style={{ fontSize: 13, margin: "0 0 10px" }}>{q.detail}</p>
      <div style={{ display: "grid", gap: 8 }}>
        {q.choices.map(c => (
          <button key={c.answer + (c.pet ?? "")} className={c.answer === "yes" ? "btn pri" : "btn"} style={{ minHeight: 48 }}
            disabled={busy} onClick={() => answer(c)}>{c.label}</button>
        ))}
      </div>
      {err && <p role="alert" style={{ fontSize: 13 }}>Couldn't save: {err}</p>}
    </div>
  );
}
