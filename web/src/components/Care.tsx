import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";
import type { CareData, CareTask, HubGuide } from "../types";
import { pushClear } from "../native";
import Fold from "./Fold";
import Sheet from "./Sheet";
import ShowMore from "./ShowMore";

/**
 * Care and maintenance: the household's short list of jobs for the bed (purification tablets, adding water, a quick
 * check of the hub). The server holds the list and the dates; the words for the built-in jobs come from the bed's
 * hardware profile. Used on Studio → Bed hub (full cards), phone Settings (compact list) and Tonight (one calm line
 * only when something is due). Anyone signed in can read it; the owner and sleepers can mark a job done or put it off;
 * only the owner changes intervals or adds jobs (the server enforces it).
 */

const EVENT = "et-care";

/** The care list, refreshed when anything changes it (on any screen) and once a minute (a new day changes "due"). */
export function useCare(): { care: CareData | null; reload: () => void } {
  const [care, setCare] = useState<CareData | null>(null);
  const reload = useCallback(() => { api.get<CareData>("/api/care").then(setCare).catch(() => {}); }, []);
  const { snap } = useApp();
  const water = snap?.system.hub?.water_ok ?? null;
  useEffect(() => {
    reload();
    const t = window.setInterval(reload, 60000);          // a fallback; the rest below make changes show at once
    const vis = () => { if (document.visibilityState === "visible") reload(); };
    window.addEventListener(EVENT, reload);
    document.addEventListener("visibilitychange", vis);
    return () => { window.clearInterval(t); window.removeEventListener(EVENT, reload); document.removeEventListener("visibilitychange", vis); };
  }, [reload]);
  useEffect(() => { reload(); }, [water, reload]);        // the hub's water status changed in the live snapshot
  return { care, reload };
}
const changed = () => window.dispatchEvent(new Event(EVENT));

/** Mark done / put off, with a plain message either way. */
export function useCareActions() {
  const { say } = useApp();
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); say(ok); changed(); void pushClear("maintenance"); } catch (e) { say((e as Error).message || "That didn't work."); }
  };
  return {
    done: (t: CareTask, on?: string) => run(() => api.post(`/api/care/${t.id}/done`, on ? { on } : {}), "Marked as done."),
    later: (t: CareTask) => run(() => api.post(`/api/care/${t.id}/snooze`, {}), "OK, I'll remind you next week."),
    run,
  };
}

const day = (iso: string) => new Date(iso + "T12:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
const dueClass = (t: CareTask) => (t.status === "overdue" || t.status === "water" ? "warn" : t.status === "due" || t.status === "soon" ? "soon" : "");

function Source({ t }: { t: CareTask }) {
  return <small className="faint">{t.source_label}{t.url && <> · <a href={t.url} target="_blank" rel="noreferrer noopener">{t.source === "maker" ? "maker's page" : "related maker's page"}</a></>}</small>;
}

function Steps({ t, id }: { t: CareTask; id: string }) {
  if (!t.steps.length && !t.supplies) return null;
  return <Fold id={id} className="fold-sub" title="Steps and supplies" summary={t.supplies ? `${t.steps.length} steps` : undefined}>
    {t.supplies && <p className="mute care-supplies"><strong>You'll need:</strong> {t.supplies}</p>}
    {t.steps.length > 0 && <ol className="care-steps">{t.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>}
  </Fold>;
}

/** One job on the Bed hub page: status, last done, interval, steps and the buttons. */
function CareCard({ t, owner }: { t: CareTask; owner: boolean }) {
  const { done, later, run } = useCareActions();
  const [days, setDays] = useState(String(t.interval_days ?? ""));
  const [when, setWhen] = useState(t.last_done ?? "");
  useEffect(() => { setDays(String(t.interval_days ?? "")); }, [t.interval_days]);
  useEffect(() => { setWhen(t.last_done ?? ""); }, [t.last_done]);
  const scheduled = t.trigger === "interval";
  const n = Number(days);
  const okDays = Number.isInteger(n) && n >= 1 && n <= 3650;
  const today = new Date().toLocaleDateString("en-CA");
  const put = (body: Record<string, unknown>, msg: string) => run(() => api.put(`/api/care/${t.id}`, body), msg);
  return (
    <div className={`care-card ${dueClass(t)}${t.enabled ? "" : " off"}`}>
      <div className="row">
        <strong style={{ fontSize: 17 }}>{t.title}</strong>
        <span className={`care-status ${dueClass(t)}`}>{t.status_text}</span>
      </div>
      {t.why && <span className="mute" style={{ fontSize: 14 }}>{t.why}</span>}
      <small className="mute">
        {t.last_done ? `Last done ${day(t.last_done)}` : scheduled ? `Not marked as done yet, counting from ${day(t.counting_from)}` : "Not marked as done yet"}
        {t.snoozed_until && ` · put off until ${day(t.snoozed_until)}`}
      </small>
      <Steps t={t} id={`care.steps.${t.id}`} />
      <Source t={t} />
      {t.enabled && <div className="care-actions">
        <button className="btn pri" onClick={() => done(t)}>Done</button>
        <button className="btn" onClick={() => later(t)}>Remind me next week</button>
      </div>}
      {owner && <div className="care-edit">
        {scheduled && <label className="care-field">
          <span>Every</span>
          <input type="number" inputMode="numeric" min={1} max={3650} value={days} aria-label={`${t.title}: days between jobs`}
            onChange={e => setDays(e.target.value)} />
          <span>days</span>
          <button className="btn" disabled={!okDays || n === t.interval_days} onClick={() => put({ interval_days: n }, "Interval saved.")}>Save</button>
        </label>}
        {scheduled && t.note && <small className="faint">{t.note}</small>}
        <label className="care-field">
          <span>I last did this on</span>
          <input type="date" max={today} value={when} aria-label={`${t.title}: the date you last did this`} onChange={e => setWhen(e.target.value)} />
          <button className="btn" disabled={!when || when === t.last_done} onClick={() => put({ last_done: when }, "Date saved.")}>Save</button>
        </label>
        <label className="row care-toggle">
          <span>Remind me about this</span>
          <input type="checkbox" className="toggle" checked={t.enabled} aria-label={`Remind me about ${t.title}`}
            onChange={e => put({ enabled: e.target.checked }, e.target.checked ? "Turned on." : "Turned off.")} />
        </label>
        {t.custom && <button className="btn" onClick={() => { if (window.confirm(`Remove “${t.title}” from the list?`)) void run(() => api.del(`/api/care/${t.id}`), "Removed."); }}>Remove this job</button>}
      </div>}
    </div>
  );
}

function AddSheet({ onClose }: { onClose: () => void }) {
  const { run } = useCareActions();
  const [title, setTitle] = useState("");
  const [days, setDays] = useState("30");
  const [why, setWhy] = useState("");
  const [steps, setSteps] = useState("");
  const [when, setWhen] = useState("");
  const n = Number(days);
  const ok = title.trim().length > 0 && Number.isInteger(n) && n >= 1 && n <= 3650;
  const save = async () => {
    await run(() => api.post("/api/care", {
      title: title.trim(), interval_days: n, why: why.trim(),
      steps: steps.split("\n").map(s => s.trim()).filter(Boolean), ...(when ? { last_done: when } : {}),
    }), "Added.");
    onClose();
  };
  return <Sheet onClose={onClose} form aria-labelledby="care-add-title">
    <h2 id="care-add-title" style={{ fontSize: 20 }}>Add your own job</h2>
    <label className="care-field col"><span>What is it?</span>
      <input type="text" maxLength={60} value={title} placeholder="Wash the topper cover" onChange={e => setTitle(e.target.value)} /></label>
    <label className="care-field"><span>Every</span>
      <input type="number" inputMode="numeric" min={1} max={3650} value={days} onChange={e => setDays(e.target.value)} /><span>days</span></label>
    <label className="care-field col"><span>I last did this on (optional)</span>
      <input type="date" max={new Date().toLocaleDateString("en-CA")} value={when} onChange={e => setWhen(e.target.value)} /></label>
    <label className="care-field col"><span>Why (optional)</span>
      <input type="text" maxLength={200} value={why} onChange={e => setWhy(e.target.value)} /></label>
    <label className="care-field col"><span>Steps (optional, one per line)</span>
      <textarea rows={4} value={steps} onChange={e => setSteps(e.target.value)} /></label>
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 6 }}>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn pri" disabled={!ok} onClick={save}>Add</button>
    </div>
  </Sheet>;
}

/** Studio → Bed hub → Care: every job as a card, then "Add your own". */
export function CareSection({ owner }: { owner: boolean }) {
  const { care } = useCare();
  const [adding, setAdding] = useState(false);
  if (!care) return <div className="box"><span className="k">Care</span><span className="mute">Loading…</span></div>;
  return (
    <div className="box care-section">
      <span className="k">Care</span>
      <p className="mute" style={{ fontSize: 14, margin: 0 }}>Small jobs that keep the bed clean and quiet. Each one counts from the day you last did it.</p>
      <ShowMore items={care.tasks} limit={6} noun="jobs" render={shown => <div className="care-list">{shown.map(t => <CareCard key={t.id} t={t} owner={owner} />)}</div>} />
      {owner && care.can_add && <button className="btn" onClick={() => setAdding(true)}>Add your own</button>}
      {adding && <AddSheet onClose={() => setAdding(false)} />}
    </div>
  );
}

/** Phone Settings → Care: a compact list. Title, how soon, Done. Tapping the title opens the steps. */
export function CareCompact({ owner }: { owner: boolean }) {
  const { care } = useCare();
  const { done, later } = useCareActions();
  const [open, setOpen] = useState<CareTask | null>(null);
  if (!care) return null;
  const tasks = care.tasks.filter(t => t.enabled);
  return (
    <>
      <div className="card list care-compact">
        {tasks.length === 0 && <div className="row"><span className="mute">All care reminders are turned off.</span></div>}
        <ShowMore items={tasks} limit={5} noun="jobs" render={shown => <>{shown.map(t => (
          <div className="row" key={t.id}>
            <button type="button" className="care-name" onClick={() => setOpen(t)} aria-label={`${t.title}: ${t.status_text}. Show the steps`}>
              <span>{t.title}</span>
              <small className={`care-status ${dueClass(t)}`}>{t.status_text}{t.snoozed_until ? " · later" : ""}</small>
            </button>
            <button className={`btn${t.due_now ? " pri" : ""} care-done`} onClick={() => done(t)}>Done</button>
          </div>))}</>} />
        {owner && <p className="faint" style={{ fontSize: 12, padding: "8px 2px 0" }}>Change how often, or add your own, in Studio → Bed hub.</p>}
      </div>
      {open && <Sheet onClose={() => setOpen(null)} aria-labelledby="care-sheet-title">
        <h2 id="care-sheet-title" style={{ fontSize: 20 }}>{open.title}</h2>
        <p className="mute" style={{ fontSize: 14, margin: 0 }}>{open.status_text}{open.last_done ? ` · last done ${day(open.last_done)}` : ""}</p>
        {open.why && <p style={{ fontSize: 14 }}>{open.why}</p>}
        {open.supplies && <p className="mute" style={{ fontSize: 14 }}><strong>You'll need:</strong> {open.supplies}</p>}
        {open.steps.length > 0 && <ol className="care-steps">{open.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>}
        <Source t={open} />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 8 }}>
          <button className="btn" onClick={() => { void later(open); setOpen(null); }}>Remind me next week</button>
          <button className="btn pri" onClick={() => { void done(open); setOpen(null); }}>Done</button>
        </div>
      </Sheet>}
    </>
  );
}

/** Tonight: one calm line, only when a job is due or overdue (and not put off), never while a night is running. */
export function CareTonight({ running }: { running: boolean }) {
  const { care } = useCare();
  const { done, later } = useCareActions();
  if (running || !care) return null;
  const due = care.tasks.filter(t => t.due_now);
  if (!due.length) return null;
  const t = due[0];
  return (
    <div className="planrow note care-tonight" role="note">
      <span><strong>{t.title}</strong>
        {due.length > 1 && <small className="mute"> · and {due.length - 1} more</small>}
        <small className="mute" style={{ display: "block" }}>{t.status_text}</small></span>
      <span className="chips">
        <button className="chip" onClick={() => done(t)}>Done</button>
        <button className="chip" onClick={() => later(t)} aria-label="Remind me next week">Later</button>
      </span>
    </div>
  );
}

/** "What the hub's light colours mean": the maker's own guide, supplied by the bed's hardware profile (none for a generic bed). */
export function LightGuide({ guide, id }: { guide: HubGuide | null | undefined; id: string }) {
  if (!guide) return null;
  const swatch = (name: string) => {
    const last = name.toLowerCase().split(" ").pop() ?? "";
    return typeof CSS !== "undefined" && CSS.supports("color", last) ? last : null;
  };
  return (
    <Fold id={id} className="fold-sub" title={guide.title} summary={guide.source}>
      <dl className="light-guide">
        {guide.items.map(([name, what]) => {
          const c = swatch(name);
          return <div key={name} className="light-row">
            <dt>{c && <i className="light-dot" aria-hidden="true" style={{ background: c }} />}{name}</dt><dd>{what}</dd>
          </div>;
        })}
      </dl>
      <small className="faint">{guide.source}. {guide.note}</small>
    </Fold>
  );
}

/** Phone Settings: the Care section, folded, with where it stands in its header ("1 due" / "All up to date"). */
export function CareFold({ owner }: { owner: boolean }) {
  const { care } = useCare();
  if (!care || !care.tasks.length) return null;
  const due = care.tasks.filter(t => t.due).length;
  return (
    <Fold id="settings.care" title="Care" summary={due ? `${due} due` : "All up to date"} attention={care.tasks.some(t => t.due_now && t.status === "overdue")}>
      <CareCompact owner={owner} />
    </Fold>
  );
}
