import { useEffect, useState } from "react";
import GuestPage, { GuestBanner } from "./Guest";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api } from "../api";
import ErrorBoundary from "../components/ErrorBoundary";
import ShowMore from "../components/ShowMore";
import Horizon from "../components/Horizon";
import { useApp } from "../ctx";
import { useAuth } from "../auth";
import { clock, cToF, eventText, personaColor, sourceText, STATE_TEXT, temp, weight, weightUnit } from "../format";
import type { EventRow } from "../types";
import Access from "./Access";
import Autopilot from "./Autopilot";
import Devices from "./Devices";
import Explorer from "./Explorer";
import Insights from "./Insights";
import Me from "./Me";
import { sideWord } from "./deviceKinds";

/** The bed's own state code ("SYSTEM_ON") in plain words; unknown codes in lower case without the prefix. */
const bedState = (x?: string | null) => !x ? "—" : x === "SYSTEM_ON" ? "On" : x === "SYSTEM_OFF" ? "Off (standby)" : x.replace(/^SYSTEM_/, "").replace(/_/g, " ").toLowerCase();
import Help from "./Help";
import Integrations from "./Integrations";
import Notifications from "./Notifications";
import RhythmEditor from "./RhythmEditor";
import Hub from "./Hub";

/** One Studio page breaking keeps the menu working; moving to another page clears it. */
function PageBoundary({ children }: { children: React.ReactNode }) {
  const loc = useLocation();
  return <ErrorBoundary name="studio page" resetKey={loc.pathname}>{children}</ErrorBoundary>;
}

function OwnerOnly() {
  return <div className="box"><span className="mute">Only the owner can open this page.</span></div>;
}

export default function Studio() {
  const { snap, connected } = useApp();
  const auth = useAuth();
  const loc = useLocation();
  // narrow screens: the nav is one scrolling row; keep the current page's tab in view
  useEffect(() => {
    if (!window.matchMedia("(max-width:900px)").matches) return;
    document.querySelector(".snav a.active")?.scrollIntoView({ inline: "center", block: "nearest" });
  }, [loc.pathname]);
  return (
    <div className="studio">
      <nav className="snav" aria-label="Studio">
        <span className="brand">Ember &amp; Tide</span>
        <span className="navh">Live</span>
        <NavLink end to="/studio">Overview</NavLink>
        <NavLink to="/studio/me">Your settings</NavLink>
        <NavLink to="/studio/guest">Guest mode</NavLink>
        <span className="navh">Analyze</span>
        <NavLink to="/studio/nights">Nights explorer</NavLink>
        <NavLink to="/studio/autopilot">Autopilot</NavLink>
        <NavLink to="/studio/insights">Insights &amp; Ask</NavLink>
        <span className="navh">Configure</span>
        <NavLink to="/studio/rhythm">Rhythm editor</NavLink>
        <NavLink to="/studio/devices">Devices &amp; calibration</NavLink>
        <NavLink to="/studio/integrations">Integrations</NavLink>
        <NavLink to="/studio/notifications">Notifications</NavLink>
        <NavLink to="/studio/access">People &amp; access</NavLink>
        <span className="navh">Tools</span>
        {auth.isOwner && <NavLink to="/studio/hub">Bed hub</NavLink>}
        {auth.isOwner && <NavLink to="/studio/console">Bed console</NavLink>}
        {auth.isOwner && <NavLink to="/studio/sim">Simulator</NavLink>}
        <NavLink to="/studio/data">Data export</NavLink>
        <NavLink to="/studio/help">Help</NavLink>
        <NavLink to="/" style={{ marginTop: 10 }}>← Phone view</NavLink>
        {auth.state.enabled && <button className="link" style={{ margin: "10px 10px 0", textAlign: "left" }} onClick={() => auth.signOut()}>Sign out {auth.me?.name}</button>}
        <div className="foot">
          <span style={{ color: snap?.system.online ? "var(--good)" : "var(--bad)" }}>● {snap?.system.name ?? "…"}</span>
          <span>{connected ? "Live" : "Reconnecting…"}{snap && snap.speed !== 1 ? ` · ×${snap.speed}` : ""}</span>
          {snap && <span className="num">{new Date(snap.now * 1000).toLocaleString([], { timeZone: snap.tz, weekday: "short", hour: "numeric", minute: "2-digit" })}</span>}
        </div>
      </nav>
      <main className="smain">
        <PageBoundary>
        <Routes>
          <Route index element={<Overview />} />
          <Route path="me" element={<Me />} />
          <Route path="guest" element={<GuestPage />} />
          <Route path="nights" element={<Explorer />} />
          <Route path="insights" element={<Insights />} />
          <Route path="autopilot" element={<Autopilot />} />
          <Route path="devices" element={<Devices />} />
          <Route path="access" element={<Access />} />
          <Route path="notifications" element={<Notifications />} />
          <Route path="integrations" element={<Integrations />} />
          <Route path="rhythm" element={<RhythmEditor />} />
          <Route path="hub" element={auth.isOwner ? <Hub /> : <OwnerOnly />} />
          <Route path="console" element={auth.isOwner ? <Console /> : <OwnerOnly />} />
          <Route path="sim" element={auth.isOwner ? <Simulator /> : <OwnerOnly />} />
          <Route path="data" element={<Data />} />
          <Route path="help" element={<Help />} />
          <Route path="explorer" element={<Navigate to="/studio/nights" replace />} />
          <Route path="*" element={<Navigate to="/studio" replace />} />
        </Routes>
        </PageBoundary>
      </main>
    </div>
  );
}

function Overview() {
  const { snap, me } = useApp();
  const auth = useAuth();
  const [events, setEvents] = useState<EventRow[]>([]);
  useEffect(() => { const f = () => api.get<EventRow[]>("/api/events?limit=30").then(setEvents).catch(() => {}); f(); const t = setInterval(f, 5000); return () => clearInterval(t); }, []);
  if (!snap) return null;
  const u = me?.settings.units ?? "F";
  const name = (id: string | null) => snap.people.find(p => p.id === id)?.name ?? "";
  return (
    <>
      <GuestBanner />
      <div><div className="k">Live</div><h1 style={{ fontSize: 34 }}>Tonight</h1></div>
      <div className="grid2">
        <div className="box"><span className="k">Both sides · rhythm (line), pad temperature (faint)</span>
          <Horizon people={snap.people} now={snap.now} tz={snap.tz} units={u} width={760} height={300} />
        </div>
        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          {snap.people.map(p => (
            <div className="box" key={p.id}>
              <div className="row"><b style={{ fontWeight: 500, color: personaColor(p) }}>{p.name}{p.name.toLowerCase() !== p.zone ? ` · ${sideWord(p.zone).toLowerCase()}` : ""}</b>
                <span className="num big" style={{ fontSize: 26 }}>{p.night.target_c == null ? "Off" : temp(p.night.target_c, u)}</span></div>
              <div className="row mute" style={{ fontSize: 13 }}><span>{STATE_TEXT[p.night.state] ?? p.night.state}</span><span>{p.night.phase_name ?? ""}</span></div>
              <div className="row" style={{ fontSize: 13 }}><span>Pad now {temp(p.bed.measured_c, u, 1)} · bed {p.bed.on ? `set to ${temp(p.bed.setpoint_c, u, 1)}` : "off"}</span></div>
              <div className="row" style={{ fontSize: 13 }}>
                <span><span className="dot" aria-hidden="true" style={{ background: p.presence.present ? "var(--good)" : p.presence.present === false ? "var(--faint)" : "var(--warn)" }} />
                  {p.presence.present ? "In bed" : p.presence.present === false ? "Not in bed" : "Not sure if in bed"}
                  {p.presence.stale ? <span className="mute"> · bed sensor quiet</span> : p.presence.echo ? <span className="mute"> · ignored a reading from the other side</span> : null}</span>
                {(p.presence.hr != null || p.presence.br != null) && <span className="num mute">{p.presence.hr ?? "—"} bpm · {p.presence.br ?? "—"} breaths/min</span>}</div>
              {p.schedule && <div className="row mute" style={{ fontSize: 12.5 }}><span title={`Pre-conditioning can run ${clock(p.schedule.window_start, snap.tz)}–${clock(p.schedule.window_end, snap.tz)}`}>Bedtime {clock(p.schedule.bedtime, snap.tz)}</span><span>Wake {clock(p.schedule.wake, snap.tz)}</span></div>}
            </div>
          ))}
        </div>
      </div>
      <LiveSignals />
      <div className="grid2">
        <div className="box"><div className="row"><span className="k">Recent events</span>{events.length > 0 && <span className="faint" style={{ fontSize: 12.5 }}>newest first</span>}</div>
          {events.length ? <ShowMore items={events} limit={8} noun="events" render={shown => <table className="t"><tbody>
            {shown.map((e, i) => <tr key={i}><td className="mute num" style={{ whiteSpace: "nowrap" }}>{clock(e.ts, snap.tz)}</td>
              <td>{eventText(e, u)}{sourceText(e.source) && <small className="faint" style={{ display: "block" }}>{sourceText(e.source)}</small>}</td><td>{name(e.person)}</td></tr>)}
          </tbody></table>} /> : <span className="mute">Nothing yet. Changes, flashes and nights starting show up here as they happen.</span>}
        </div>
        <div className="box"><span className="k">Bed</span>
          <table className="t"><tbody>
            <tr><td>Connected through</td><td>{snap.system.name}</td></tr>
            <tr><td>Status</td><td><span className="dot" aria-hidden="true" style={{ background: snap.system.online ? "var(--good)" : "var(--bad)" }} />{snap.system.online ? "Online" : "Offline"}{snap.system.detail ? <span className="mute"> · {snap.system.detail}</span> : null}</td></tr>
            <tr><td>Firmware</td><td>{snap.system.firmware ?? "—"}</td></tr>
            <tr><td>Bed state</td><td title={snap.system.system_state ?? undefined}>{bedState(snap.system.system_state)}</td></tr>
            <tr><td>Quiet fan</td><td>{snap.system.quiet ? "On" : "Off"}</td></tr>
            <tr><td>Bed light</td><td>{snap.system.brightness == null ? "—" : snap.system.brightness === 0 ? "Off" : `${snap.system.brightness}% bright`}</td></tr>
            {snap.system.hub && <tr><td>Hub</td><td>{snap.system.hub.line}{auth.isOwner && <> · <NavLink to="/studio/hub">details</NavLink></>}</td></tr>}
          </tbody></table></div>
      </div>
    </>
  );
}

function Console() {
  const { snap } = useApp();
  const [cmd, setCmd] = useState("");
  const [log, setLog] = useState<string[]>([]);
  if (!snap) return null;
  const send = async () => {
    const c = cmd.trim();
    if (!c) return;
    setCmd("");                    // clear first, so anything typed while it's sending isn't glued onto this command
    try { const r = await api.post<{ result: string }>("/api/console", { command: c }); setLog(l => [`› ${c}`, `  ${r.result}`, ...l]); }
    catch (e) { setLog(l => [`› ${c}`, `  ✗ ${(e as Error).message}`, ...l]); }
  };
  return (
    <>
      <div><div className="k">Bed</div><h1 style={{ fontSize: 34 }}>Console</h1></div>
      <div className="box">
        <div className="tscroll"><table className="t">
          <thead><tr><th>Side</th><th>On</th><th>Setpoint</th><th>Pad</th><th>Status</th><th>HR</th><th>Breath</th><th>Presence</th></tr></thead>
          <tbody>{snap.people.map(p => (
            <tr key={p.id}><td>{sideWord(p.zone)}{p.name.toLowerCase() !== p.zone ? ` · ${p.name}` : ""}</td><td>{p.bed.on ? "on" : "off"}</td>
              {/* the bed speaks °C (the SET command too); the person's own unit alongside */}
              {[p.bed.setpoint_c, p.bed.measured_c].map((c, k) => <td key={k} className="num">{c == null ? "—" : <>{c.toFixed(2)} °C{p.settings.units === "F" && <span className="faint"> · {Math.round(cToF(c))}°F</span>}</>}</td>)}
              <td>{p.presence.status ?? "—"} · {p.presence.status_text ?? ""}</td><td>{p.presence.hr ?? "—"}</td><td>{p.presence.br ?? "—"}</td>
              <td>{p.presence.present ? "in bed" : p.presence.present === false ? "empty" : "unknown"}{p.presence.echo ? " · echo" : ""}{p.presence.stale ? " · stale" : ""}</td></tr>))}</tbody>
        </table></div>
      </div>
      <div className="box">
        <span className="k">Send a command · allowlisted: SET, CLEAR, SYSTEM ON, FAN QUIET, IB DIMMER, IB RGB, FILL START, REBOOT · firmware/Wi-Fi commands are blocked</span>
        <div className="row"><input type="text" value={cmd} placeholder="SET LEFT 18.5" style={{ flex: 1 }} onChange={e => setCmd(e.target.value)} onKeyDown={e => e.key === "Enter" && send()} />
          <button className="btn pri" onClick={send}>Send</button></div>
        <div className="code">{log.length ? log.map((l, i) => <span key={i}>{l}</span>) : <span>No commands yet. The engine will re-apply the rhythm on its next change.</span>}</div>
      </div>
    </>
  );
}

function Simulator() {
  const { snap } = useApp();
  const [sim, setSim] = useState<{ speed: number; now: string; scripted: boolean; echo: boolean; blips: boolean; room_c: number; presence: Record<string, boolean> } | null>(null);
  const [jump, setJump] = useState("");
  const load = () => api.get<typeof sim>("/api/sim").then(setSim).catch(() => setSim(null));
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, []);
  if (!snap) return null;
  if (!sim) return <div className="box">The simulator isn't running. Start the core with <code>ET_BED=sim</code> to use this page.</div>;
  const post = (b: object) => api.post<typeof sim>("/api/sim", b).then(setSim);
  // Jump targets are wall-clock times in the household's time zone (the browser may be elsewhere).
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: snap.tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23" })
    .formatToParts(new Date(snap.now * 1000)).map(x => [x.type, x.value]));
  const nowH = Number(parts.hour);
  const iso = (h: number, m: number, morning = 0) => {
    // evening times belong to tonight (or last night before noon); morning times to the morning after that evening
    const d = new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day)));
    if (!morning && nowH < 12) d.setUTCDate(d.getUTCDate() - 1);
    if (morning && nowH >= 12) d.setUTCDate(d.getUTCDate() + 1);
    const p = (n: number) => String(n).padStart(2, "0");
    return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}T${p(h)}:${p(m)}`;
  };
  return (
    <>
      <div><div className="k">Development</div><h1 style={{ fontSize: 34 }}>Simulator</h1></div>
      <div className="grid2">
        <div className="box"><span className="k">Time</span>
          <div className="row"><span>Simulated now</span><span className="num">{new Date(snap.now * 1000).toLocaleString([], { timeZone: snap.tz })}</span></div>
          <div className="pills">{[1, 10, 60, 300].map(s => <button key={s} className="pill" aria-pressed={sim.speed === s} onClick={() => post({ speed: s })}>×{s}</button>)}</div>
          <div className="pills">
            {[["9:15 PM", 21, 15, 0], ["10:20 PM", 22, 20, 0], ["11:30 PM", 23, 30, 0], ["2:45 AM", 2, 45, 1], ["5:55 AM", 5, 55, 1], ["7:30 AM", 7, 30, 1]].map(([l, h, m, d]) =>
              <button key={l as string} className="pill" onClick={() => post({ jump_to: iso(h as number, m as number, d as number) })}>Jump to {l}</button>)}
          </div>
          <div className="row"><input type="datetime-local" aria-label="Jump to a date and time" value={jump} onChange={e => setJump(e.target.value)} style={{ flex: 1, minWidth: 0 }} /><button className="btn" onClick={() => jump && post({ jump_to: jump })}>Jump</button></div>
        </div>
        <div className="box"><span className="k">People in bed</span>
          <div className="row"><span>Follow a realistic night script</span><input type="checkbox" className="toggle" checked={sim.scripted} onChange={e => post({ scripted: e.target.checked })} /></div>
          {snap.people.map(p => (
            <div className="row" key={p.id}><span>{p.name.toLowerCase() === p.zone ? `Someone on the ${sideWord(p.zone).toLowerCase()}` : `${p.name} on the ${sideWord(p.zone).toLowerCase()}`}</span>
              <input type="checkbox" className="toggle" checked={!!sim.presence[p.zone]} onChange={e => post({ presence: { [p.zone]: e.target.checked } })} /></div>))}
          <div className="row"><span>Cross-side echo (real quirk)</span><input type="checkbox" className="toggle" checked={sim.echo} onChange={e => post({ echo: e.target.checked })} /></div>
          <div className="row"><span>Half-second false blips (real quirk)</span><input type="checkbox" className="toggle" checked={sim.blips} onChange={e => post({ blips: e.target.checked })} /></div>
          <div className="row"><span>Room temperature</span><span className="num">{temp(sim.room_c, "F")}</span></div>
        </div>
      </div>
    </>
  );
}

function Data() {
  const [hours, setHours] = useState(24 * 7);
  const [pseudo, setPseudo] = useState(false);
  return (
    <>
      <div><div className="k">Your data</div><h1 style={{ fontSize: 34 }}>Export</h1></div>
      <div className="box">
        <div className="row"><span>Range</span><select value={hours} onChange={e => setHours(parseInt(e.target.value))}>
          <option value={24}>Last 24 hours</option><option value={168}>Last 7 days</option><option value={720}>Last 30 days</option><option value={24 * 365}>Everything (1 year)</option></select></div>
        <div className="row"><span>Replace names with P1 / P2</span><input type="checkbox" className="toggle" checked={pseudo} onChange={e => setPseudo(e.target.checked)} /></div>
        <p className="mute" style={{ fontSize: 13 }}>One row per side every 15 seconds: setpoint, pad temperature, rhythm target, phase, presence, heart rate, breathing rate and sensor status. Good for spreadsheets or model training.</p>
        <div className="pills">
          <a className="btn pri" href={`/api/export?fmt=csv&hours=${hours}&pseudonymize=${pseudo}`}>Download CSV</a>
          <a className="btn" href={`/api/export?fmt=jsonl&hours=${hours}&pseudonymize=${pseudo}`}>Download JSON Lines</a>
        </div>
        <p className="mute" style={{ fontSize: 12 }}>Also available to scripts: <code>GET /api/export?fmt=csv&amp;hours=168</code></p>
      </div>
    </>
  );
}



function LiveSignals() {
  const { snap, me } = useApp();
  const [src, setSrc] = useState<{ id: string; name: string; role: string; role_label: string; zone: string | null; unit: string; value: number | null; age_s: number | null; error: string | null; online: boolean; device: string | null; driver: string }[]>([]);
  const [ins, setIns] = useState<{ title: string; detail: string; severity: string; person: string }[]>([]);
  useEffect(() => { const f = () => api.get<{ sources: typeof src }>("/api/sources").then(d => setSrc(d.sources)).catch(() => {}); f(); const t = setInterval(f, 4000); return () => clearInterval(t); }, []);
  useEffect(() => { snap?.people.forEach(p => api.get<{ title: string; detail: string; severity: string }[]>(`/api/people/${p.id}/insights`).then(r => setIns(x => [...x.filter(i => i.person !== p.name), ...r.filter(i => i.severity === "suggest").slice(0, 2).map(i => ({ ...i, person: p.name }))])).catch(() => {})); }, [snap?.people.length]);
  if (!snap) return null;
  const u = snap.people[0]?.settings.units ?? "F";
  const wu = weightUnit(me?.settings ?? snap.people[0]?.settings);
  const fmt =(s: typeof src[number]) => s.value == null ? "—" : s.unit === "°C" ? `${(u === "F" ? cToF(s.value) : s.value).toFixed(1)}°` : s.role === "light" ? (s.value >= 0.5 ? "on" : "off") : s.role === "presence" ? (s.value >= 0.5 ? "someone there" : "empty")
    : s.role === "home" ? (s.value >= 0.5 ? "home" : "away") : s.role === "location" ? (s.value < 0.3 ? "home" : `${s.value.toFixed(1)} km`)
    : s.role === "calendar" ? (s.value > 0 ? `until ${clock(s.value, snap.tz)}` : "free") : s.role === "weight" ? weight(s.value, wu) : `${Math.round(s.value * 10) / 10} ${s.unit}`;
  const ctx = snap.people.map(p => ({ p, c: p.night.context })).filter(x => x.c && (x.c.home != null || x.c.event_end || x.c.heading_home));
  const lights = snap.people.map(p => p.night.context?.lights_on).find(v => v != null);
  return (
    <div className="grid2">
      <div className="box"><div className="row"><span className="k">Live signals</span><NavLink to="/studio/devices" style={{ fontSize: 12.5 }}>Manage devices →</NavLink></div>
        {(ctx.length > 0 || lights != null) && <div style={{ display: "grid", gap: 4 }}>
          {ctx.map(({ p, c }) => <div key={p.id} className="row" style={{ fontSize: 13.5 }}>
            <b style={{ fontWeight: 500, color: personaColor(p) }}>{p.name}</b>
            <span className="mute" style={{ textAlign: "right" }}>{c!.heading_home && c!.eta_min != null ? `on the way home · about ${Math.round(c!.eta_min)} min`
              : [c!.home === true ? "home" : c!.home === false ? `away${c!.distance_km != null ? ` · ${c!.distance_km.toFixed(1)} km` : ""}` : "",
                 c!.event_end && c!.event_end > snap.now ? `event until ${clock(c!.event_end, snap.tz)}` : ""].filter(Boolean).join(" · ") || "no news"}{c!.why ? <><br /><small className="faint">{c!.why}</small></> : null}</span></div>)}
          {lights != null && <div className="row" style={{ fontSize: 13.5 }}><span>Bedroom light</span><span className="mute">{lights ? "on" : "off"}</span></div>}
        </div>}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(140px,1fr))", gap: 10 }}>
          <ShowMore items={src} limit={8} noun="devices" render={shown => shown.map(s => <div key={s.id} style={{ background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 12, padding: "10px 12px", display: "grid", gap: 2 }} title={s.device ?? undefined}>
            <span className="faint" style={{ fontSize: 11.5 }}>{s.role_label}{s.zone ? ` · ${sideWord(s.zone).toLowerCase()}` : ""}</span>
            <span style={{ fontSize: 20, fontWeight: 300 }} className="num">{fmt(s)}</span>
            <span style={{ fontSize: 11.5, color: s.error ? "var(--bad)" : s.online ? "var(--mute)" : "var(--warn)" }}>
              <span className="dot" aria-hidden="true" style={{ width: 6, height: 6, marginRight: 5, background: s.error ? "var(--bad)" : s.online ? "var(--good)" : "var(--warn)" }} />
              {s.name}{s.error ? " · not working" : !s.online ? " · no recent reading" : ""}</span></div>)} />
        </div>
        {!src.length && <span className="mute">No extra devices yet. Add a bed sensor, a scale or a room thermometer under <NavLink to="/studio/devices">Devices</NavLink>.</span>}
        </div>
      <div className="box"><div className="row"><span className="k">Suggestions</span><NavLink to="/studio/insights" style={{ fontSize: 12.5 }}>All insights →</NavLink></div>
        {ins.length ? ins.map((i, k) => <div key={k} style={{ borderLeft: "3px solid var(--acc)", paddingLeft: 10, display: "grid", gap: 2 }}><b style={{ fontWeight: 500 }}>{i.person}: {i.title}</b><span className="mute" style={{ fontSize: 13 }}>{i.detail}</span></div>)
          : <span className="mute">Nothing to change right now.</span>}</div>
    </div>
  );
}
