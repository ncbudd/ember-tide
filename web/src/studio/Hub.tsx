import { useState } from "react";
import { api } from "../api";
import Sheet from "../components/Sheet";
import { CareSection, LightGuide, useCare } from "../components/Care";
import { LightControl, QuietFanControl, useHubDetail } from "../components/HubControls";
import { useApp } from "../ctx";
import { sideWord } from "./deviceKinds";

/**
 * Studio → Bed hub: how the bed's control unit is doing, its light and fan, and the two maintenance actions.
 * Owner only (the server enforces it). Wording is the same for every kind of bed: the server turns each
 * driver's own flags into plain language.
 */

const ago = (s: number) => {
  const m = Math.round(s / 60);
  if (m < 2) return "just now";
  if (m < 120) return `${m} minutes ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} hours ago` : `${Math.round(h / 24)} days ago`;
};
const RAW_NAMES: Record<string, string> = { reservoir: "reservoir", ctank_left: "left tank", ctank_right: "right tank" };

export default function Hub() {
  const { snap, say } = useApp();
  const d = useHubDetail();
  const { care } = useCare();
  const [sheet, setSheet] = useState<null | "restart" | "fill">(null);
  const [busy, setBusy] = useState(false);
  if (!snap) return null;
  const caps = snap.system.capabilities;
  const running = snap.people.some(p => ["rhythm", "out_of_bed_hold"].includes(p.night.state));
  const untried = d?.untried ?? [];
  const run = async (what: "restart" | "fill") => {
    setBusy(true);
    try {
      await api.post(`/api/hub/${what}`, { confirm: true, force: running });
      say(what === "restart" ? "Restarting the hub. It's back in about a minute." : "Fill mode started.");
      setSheet(null);
    } catch (e) { say((e as Error).message || "That didn't work."); }
    setBusy(false);
  };
  const water = d?.water_ok;
  const waterRaw = Object.entries(d?.water_raw ?? {}).map(([k, v]) => `${RAW_NAMES[k] ?? k.replace(/_/g, " ")} ${v}`).join(", ");
  return (
    <>
      <div><div className="k">Bed</div><h1 style={{ fontSize: 34 }}>Bed hub</h1>
        <p className="mute" style={{ maxWidth: 560 }}>The unit that heats, cools and pumps water for the topper. Everything here is the owner's.</p></div>

      <div className="grid2">
        <div className="box"><span className="k">Status</span>
          {!d?.available ? <span className="mute">{d ? "This bed doesn't report hub details." : "Loading…"}</span> : <>
            <div className="row"><strong style={{ fontSize: 20 }}>{d.line}</strong>
              <span className="dot" aria-hidden="true" style={{ background: d.ok ? "var(--good)" : "var(--warn)" }} /></div>
            {(d.faults ?? []).map(f => (
              <div key={f.code + f.zone} className="hub-fault" role="note">
                <strong>{f.name}{f.zone ? ` · ${sideWord(f.zone)} side` : ""}</strong>
                <span className="mute">{f.advice}</span>
              </div>
            ))}
            <table className="t"><tbody>
              <tr><td>Water</td><td>{water == null ? "—" : water ? "OK" : "Check the water"}
                {water === false && waterRaw && <small className="faint" style={{ display: "block" }}>The hub reports: {waterRaw}. What these numbers mean isn't confirmed, so it's worth a look.</small>}</td></tr>
              <tr><td>Safety check</td><td>{d.safety_ok == null ? "—" : d.safety_ok ? "OK" : "Not passing"}</td></tr>
              <tr><td>Wi-Fi</td><td>{d.wifi?.dbm == null ? "—" : `${d.wifi.words} · ${d.wifi.dbm} dBm`}
                {(d.wifi?.name || d.wifi?.address) && <small className="faint" style={{ display: "block" }}>{[d.wifi.name, d.wifi.address].filter(Boolean).join(" · ")}</small>}</td></tr>
              <tr><td>Last restart</td><td>{d.uptime_s == null ? "—" : ago(d.uptime_s)}</td></tr>
              <tr><td>Last command received</td><td>{d.last_command ?? "—"}
                {d.command_received != null && <small className="faint" style={{ display: "block" }}>{d.command_received ? "The hub confirmed our last command arrived." : "Waiting for the hub to confirm our last command."}</small>}</td></tr>
              <tr><td>Fan</td><td>{d.fan && Object.keys(d.fan.rpm).length ? Object.entries(d.fan.rpm).map(([z, v]) => `${sideWord(z)} ${v} rpm`).join(" · ") : "—"}
                {d.fan?.pwm != null && <small className="faint" style={{ display: "block" }}>Power {d.fan.pwm}%</small>}</td></tr>
              {d.humidity != null && <tr><td>Room humidity</td><td>{Math.round(d.humidity)}%</td></tr>}
            </tbody></table>
          </>}
          <LightGuide guide={care?.hub_guide} id="hub.lightguide.studio" />
        </div>

        <div style={{ display: "grid", gap: 14, alignContent: "start" }}>
          <div className="box"><span className="k">Light</span>
            <LightControl snap={snap} />
            <QuietFanControl snap={snap} />
            {!caps.includes("led_dim") && !caps.includes("fan_quiet") && <span className="mute">This bed has no hub light or fan setting.</span>}
          </div>

          <div className="box"><span className="k">Maintenance</span>
            {caps.includes("reboot") && <div className="row"><span>Restart the hub<br /><small className="mute">Clears most small glitches. Temperatures resume afterwards.</small></span>
              <button className="btn" onClick={() => setSheet("restart")}>Restart…</button></div>}
            {caps.includes("water_fill") && <div className="row"><span>Add water<br /><small className="mute">Starts the hub's fill mode.{untried.includes("water_fill") ? " Not tried on this setup yet." : ""}</small></span>
              <button className="btn" onClick={() => setSheet("fill")}>{untried.includes("water_fill") ? "Add water (not tried yet)…" : "Add water…"}</button></div>}
            {!caps.includes("reboot") && !caps.includes("water_fill") && <span className="mute">This bed has no maintenance actions here.</span>}
          </div>

          <div className="box"><span className="k">Firmware</span>
            <table className="t"><tbody>
              <tr><td>Installed</td><td>{d?.firmware?.version ?? snap.system.firmware ?? "—"}</td></tr>
              {d?.firmware?.build && <tr><td>Build</td><td>{d.firmware.build}</td></tr>}
              {d?.firmware?.panel && <tr><td>Light panel</td><td>{d.firmware.panel}</td></tr>}
            </tbody></table>
            <p className="mute" style={{ fontSize: 13.5, margin: 0 }}>Updates aren't installed automatically while the hub is kept on the home network, so it stays on the version it has. There's nothing to install from here.</p>
          </div>
        </div>
      </div>

      <CareSection owner />

      {sheet === "restart" && <Sheet onClose={() => setSheet(null)} locked={busy} role="alertdialog" aria-labelledby="tw-title" aria-describedby="tw-desc">
        <h2 id="tw-title" style={{ fontSize: 20 }}>Restart the hub?</h2>
        <p id="tw-desc" className="mute" style={{ fontSize: 14 }}>The hub restarts and is back in about a minute. Temperatures resume afterwards.</p>
        {running && <p className="mute" style={{ fontSize: 13.5 }}>A night is running, so both sides pause heating and cooling for that minute.</p>}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 6 }}>
          <button className="btn" disabled={busy} onClick={() => setSheet(null)}>Cancel</button>
          <button className="btn pri" disabled={busy} onClick={() => run("restart")}>{running ? "Restart anyway" : "Restart"}</button>
        </div>
      </Sheet>}
      {sheet === "fill" && <Sheet onClose={() => setSheet(null)} locked={busy} role="alertdialog" aria-labelledby="tw-title" aria-describedby="tw-desc">
        <h2 id="tw-title" style={{ fontSize: 20 }}>Add water</h2>
        <p id="tw-desc" className="mute" style={{ fontSize: 14 }}>This puts the hub into its fill mode. Follow the maker's instructions for adding water.</p>
        <ol className="mute" style={{ fontSize: 14, paddingLeft: 20, margin: "4px 0", display: "grid", gap: 4 }}>
          <li>Make sure nobody is on the topper.</li>
          <li>Start fill mode here, then add water as the maker describes.</li>
          <li>Stop when it's full, then check the status here.</li>
        </ol>
        {untried.includes("water_fill") && <p style={{ fontSize: 13.5 }}>Not tried on this setup yet. Go slowly, and stop if anything looks wrong.</p>}
        {running && <p className="mute" style={{ fontSize: 13.5 }}>A night is running. Filling now interrupts it.</p>}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 6 }}>
          <button className="btn" disabled={busy} onClick={() => setSheet(null)}>Cancel</button>
          <button className="btn pri" disabled={busy} onClick={() => run("fill")}>{running ? "Start anyway" : "Start fill mode"}</button>
        </div>
      </Sheet>}
    </>
  );
}
