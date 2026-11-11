import { useEffect, useState } from "react";
import { api } from "../api";
import Fold from "../components/Fold";
import { useApp } from "../ctx";
import { healthConnect, healthDisconnect, healthOpen, healthSetTypes, healthStatus, healthSync, type HealthReport, type HealthStatus } from "../native";

const NAMES: Record<string, string> = {
  hr: "heart rate", skin_temp: "skin temperature", hrv: "HRV", resp_rate: "breathing", spo2: "blood oxygen", sleep_sessions: "sleep nights",
  resting_hr: "resting heart rate", body_temp: "body temperature", exercise_min: "workouts", steps: "hours of steps",
  active_kcal: "hours of active calories", caffeine_mg: "caffeine entries", weight_kg: "weigh-ins",
};

/** What can be shared, grouped, with a one-line reason each. Keys match the app's HealthSync kinds. */
const KINDS: { group: "night" | "day"; key: string; label: string; why: string }[] = [
  { group: "night", key: "skin_temp", label: "Skin temperature", why: "The clearest sign you're running warm or cold." },
  { group: "night", key: "hr", label: "Heart rate", why: "Tells restless-and-warm apart from just restless." },
  { group: "night", key: "sleep", label: "Sleep stages", why: "Lines up deep and REM sleep with your temperature plan." },
  { group: "night", key: "hrv", label: "Heart rate variability", why: "How settled your body is through the night." },
  { group: "night", key: "resp_rate", label: "Breathing rate", why: "Another sign of how deeply you're sleeping." },
  { group: "night", key: "spo2", label: "Blood oxygen", why: "Shown alongside your nights." },
  { group: "night", key: "resting_hr", label: "Resting heart rate", why: "Your normal, so a raised night stands out." },
  { group: "night", key: "body_temp", label: "Body temperature", why: "Spots nights you run warm, like when you're unwell." },
  { group: "day", key: "exercise", label: "Exercise", why: "A late workout can keep you warm at bedtime." },
  { group: "day", key: "caffeine", label: "Caffeine", why: "Late coffee can slow settling. Only if you log it." },
  { group: "day", key: "steps", label: "Steps", why: "How active the day was." },
  { group: "day", key: "active_kcal", label: "Active calories", why: "Like steps, from your watch." },
  { group: "day", key: "weight", label: "Weight", why: "Helps a bed scale tell the two of you apart." },
];

const ago = (ms: number) => {
  const s = (Date.now() - ms) / 1000;
  return s < 120 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
};

function summary(r: HealthReport): string {
  const parts = Object.entries(r.stored).filter(([, n]) => n > 0).map(([k, n]) => k === "sleep_sessions" ? `${n} sleep night${n > 1 ? "s" : ""}` : `${n.toLocaleString()} ${NAMES[k] ?? k}`);
  const had = Object.entries(r.already ?? {}).filter(([k, n]) => n > 0 && k !== "sleep_sessions").reduce((p, [, n]) => p + n, 0);
  const text = parts.length ? `Added ${parts.join(", ")}.` : "Nothing new since the last sync.";
  return had ? `${text} ${had.toLocaleString()} reading${had === 1 ? " was" : "s were"} already here.` : text;
}

const NIGHT_LABEL = (d: string) => new Date(d + "T12:00:00").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });

/** Recent nights from the server: what the watch or ring sent for each and what's still missing (and why). */
type WearNight = { date: string; text: string; stages: string[]; hr: Record<string, number>; live: number | null };

/** Watch or ring data from the phone's Health Connect, for Autopilot and the tuning guide. Only inside the Android app. */
export default function HealthConnect() {
  const { me } = useApp();
  const [st, setSt] = useState<HealthStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const [nights, setNights] = useState<WearNight[]>([]);

  const load = () => {
    healthStatus().then(setSt).catch(() => setSt(null));
    if (me) api.get<WearNight[]>(`/api/people/${me.id}/wearable/nights?days=7`).then(setNights).catch(() => setNights([]));
  };
  useEffect(() => { load(); }, [me?.id]);
  if (!st) return null;

  const run = async (label: string, f: () => Promise<unknown>) => {
    setBusy(label); setMsg(null);
    try { await f(); } catch (e) { setMsg((e as Error).message); } finally { setBusy(null); load(); }
  };
  const sync = (full = false) => run("sync", () => healthSync(full));        // the result shows in the summary row
  const connect = () => run("connect", async () => {
    const s = await healthConnect();
    setSt(s);
    if (s.connected) await healthSync();
  });
  // Turning one on asks Health Connect for just that permission (if it isn't allowed yet) and reads it straight away.
  const toggle = (key: string, on: boolean) => run("t:" + key, async () => {
    const next = on ? [...new Set([...st.types, key])] : st.types.filter(k => k !== key);
    let s = await healthSetTypes(next);
    setSt(s);
    if (on && st.connected && s.needs_permission.includes(key)) {
      s = await healthConnect();
      setSt(s);
    }
    if (on && s.allowed.includes(key)) await healthSync(true);
    if (!on && s.allowed.includes(key)) setMsg("Ember & Tide has stopped reading it. To take the permission back as well, open Health Connect.");
  });
  const r = st.report;
  const avail = new Set(st.available.length ? st.available : KINDS.map(k => k.key));

  const list = (group: "night" | "day") => KINDS.filter(k => k.group === group).map(k => {
    const offered = avail.has(k.key);
    const on = st.types.includes(k.key);
    const waiting = on && st.connected && st.needs_permission.includes(k.key);
    return <label key={k.key} className="row" style={{ padding: "10px 0", minHeight: 52, cursor: offered ? "pointer" : "default" }}>
      <span style={{ fontSize: 14.5 }}>{k.label}<br />
        <span className="mute" style={{ fontSize: 12.5 }}>
          {!offered ? "This phone's Health Connect doesn't offer it yet." : waiting ? "Waiting for permission: tap Allow below." : k.why}
        </span>
      </span>
      <input type="checkbox" className="toggle" aria-label={k.label} checked={on && offered} disabled={!offered || !!busy}
        onChange={e => toggle(k.key, e.target.checked)} />
    </label>;
  });

  const trouble = st.sdk === "update" || (st.connected && st.needs_permission.length > 0) || !!(r && r.warnings.length);
  const sum = st.sdk === "unavailable" ? "Not on this phone" : st.sdk === "update" ? "Needs installing"
    : !st.connected ? "Not connected" : `Connected · ${st.last ? `synced ${ago(st.last)}` : "not synced yet"}`;
  return <Fold id="settings.health" anchor="watch" title="Watch or ring" summary={trouble ? `${sum} · needs a look` : sum} attention={trouble}>
    <div className="card list">
      {st.sdk === "unavailable" && <p className="mute" style={{ fontSize: 13, padding: "10px 0" }}>Health Connect isn't available on this phone.</p>}
      {st.sdk === "update" && <div className="row" style={{ padding: "6px 0" }}>
        <span>Health Connect needs installing or updating</span>
        <button className="btn" onClick={() => run("connect", healthConnect)}>Get it</button>
      </div>}
      {st.sdk === "available" && !st.connected && <>
        <p style={{ fontSize: 14, padding: "8px 0 0" }}>Connect Health Connect so Autopilot can use your watch or ring: skin temperature most of all, plus heart rate and sleep stages.</p>
        <p className="mute" style={{ fontSize: 13 }}>Read only. It goes to your own server and nowhere else. Choose what to share below.</p>
        <div style={{ padding: "4px 0 8px" }}><button className="btn pri" disabled={!!busy || st.types.length === 0} onClick={connect}>{busy === "connect" ? "Connecting…" : "Connect Health Connect"}</button></div>
      </>}
      {st.sdk === "available" && st.connected && <>
        <div className="row">
          <span>Health Connect<br /><span className="mute" style={{ fontSize: 12.5 }}>
            {st.last ? `Synced ${ago(st.last)}` : "Not synced yet"}{st.background ? " · syncs every 6 hours" : " · syncs when you open the app"}
          </span></span>
          <button className="btn" disabled={!!busy} onClick={() => sync()}>{busy === "sync" ? "Syncing…" : "Sync now"}</button>
        </div>
        {st.needs_permission.length > 0 && <div className="row">
          <span className="mute" style={{ fontSize: 13 }}>Not allowed yet: {st.needs_permission.map(k => KINDS.find(x => x.key === k)?.label.toLowerCase() ?? k).join(", ")}</span>
          <button className="btn" disabled={!!busy} onClick={connect}>Allow</button>
        </div>}
        {r && <button className="row" style={{ background: "none", border: 0, padding: "6px 0", textAlign: "left", cursor: "pointer" }} onClick={() => setShowDetails(!showDetails)} aria-expanded={showDetails}>
          <span style={{ fontSize: 13 }}>{summary(r)}{r.warnings.length ? ` ${r.warnings.length} thing${r.warnings.length > 1 ? "s" : ""} to check.` : ""}</span>
          <span className="mute" style={{ fontSize: 12 }}>{showDetails ? "Hide" : "Details"}</span>
        </button>}
        {r && showDetails && <div style={{ display: "grid", gap: 6, fontSize: 12.5, paddingBottom: 6 }}>
          {r.nights_rechecked > 0 && <span>Autopilot re-checked {r.nights_rechecked} night{r.nights_rechecked > 1 ? "s" : ""} with the new data.</span>}
          {r.warnings.map((w, i) => <span key={"w" + i} style={{ color: "var(--warn)" }}>{w}</span>)}
          {r.notes.map((n, i) => <span key={"n" + i} className="mute">{n}</span>)}
          <span className="mute">Read from Health Connect{r.from ? ` (since ${new Date(r.from).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })})` : ""}: {Object.entries(r.read).filter(([, n]) => n > 0).map(([k, n]) => `${k.replace(/_/g, " ")} ${n}`).join(" · ") || "nothing"}</span>
          {(r.detail ?? []).length > 0 && <span className="faint">By app: {r.detail!.filter(d => d.samples > 0 || d.records > 0).map(d => `${d.kind.replace(/_/g, " ")} from ${d.from} ${d.samples || d.records}`).join(" · ")}</span>}
        </div>}
        {nights.length > 0 && <Fold id="health.nights" title="Your recent nights" summary={`${nights.filter(n => n.stages.length).length} of ${nights.length} with sleep stages`}>
          <div style={{ display: "grid", gap: 8, fontSize: 13, padding: "4px 0 8px" }}>
            {nights.map(n => <span key={n.date}><b>{NIGHT_LABEL(n.date)}</b> · <span className="mute">{n.text}</span></span>)}
          </div>
        </Fold>}
      </>}
      {st.sdk === "available" && <>
        <Fold id="health.night" title="For the night" summary={`${KINDS.filter(k => k.group === "night" && st.types.includes(k.key)).length} shared`} defaultOpen={!st.connected}>{list("night")}</Fold>
        <Fold id="health.day" title="Your day, for the tuning guide" summary={`${KINDS.filter(k => k.group === "day" && st.types.includes(k.key)).length} shared`}>{list("day")}</Fold>
      </>}
      {st.sdk === "available" && st.connected && <div className="pills" style={{ padding: "10px 0 8px" }}>
        <button className="pill" disabled={!!busy} onClick={() => sync(true)}>Re-import last 7 days</button>
        <button className="pill" onClick={() => healthOpen()}>Open Health Connect</button>
        <button className="pill" disabled={!!busy} onClick={() => run("off", healthDisconnect)}>Disconnect</button>
      </div>}
      {msg && <p style={{ fontSize: 13, paddingBottom: 8 }} role="status">{msg}</p>}
    </div>
  </Fold>;
}
