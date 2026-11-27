import { useState } from "react";
import type { Snapshot } from "../types";

/**
 * Studio → Bed hub: which bed this server talks to, and what to put in .env to use another one. The bed is chosen when
 * the server starts (ET_BED), so this only explains and shows the lines to paste; it never reads or shows a secret.
 */

type Bed = { id: string; name: string; what: string; lines: string[]; note?: string; docs: string };

const BEDS: Bed[] = [
  { id: "orion-cloud", name: "Orion, through the maker's cloud", what: "Easiest for an Orion topper: a personal key from the maker's web app.",
    lines: ["ET_BED=orion-cloud", "ET_ORION_API_KEY=your-key"], docs: "docs/install.md" },
  { id: "orion-mqtt", name: "Orion, fully local", what: "The topper talks to your own MQTT broker; no cloud at all.",
    lines: ["ET_BED=orion-mqtt", "ET_MQTT_URL=mqtt://mosquitto:1883"], note: "Needs the local broker and a network change; follow the guide.", docs: "docs/bed-cutover.md" },
  { id: "ha-climate", name: "BedJet, OOLER or another bed in Home Assistant", what: "Any bed that Home Assistant shows as a thermostat, one per side.",
    lines: ["ET_BED=ha-climate", "ET_HA_URL=http://homeassistant.local:8123", "ET_HA_TOKEN=your-long-lived-token",
      "ET_BED_ENTITIES=left=climate.bed_left,right=climate.bed_right"], note: "New: not yet tried on a real unit.", docs: "docs/drivers.md, Home Assistant climate" },
  { id: "sleepme", name: "Sleepme Dock Pro", what: "Through Sleepme's own developer API. One dock per side.",
    lines: ["ET_BED=sleepme", "ET_SLEEPME_TOKEN=your-token"], note: "New: not yet tried on a real unit. Make the token in your Sleepme account, Developer API.", docs: "docs/drivers.md, Sleepme Dock Pro" },
  { id: "free-sleep", name: "Eight Sleep Pod with free-sleep", what: "Fully local, once the Pod runs the community free-sleep software.",
    lines: ["ET_BED=free-sleep", "ET_FREE_SLEEP_URL=http://192.168.1.60:3000"], note: "New: not yet tried on a real unit. Installing free-sleep means opening the Pod.", docs: "docs/drivers.md, Eight Sleep Pod" },
  { id: "sim", name: "No bed: the simulator", what: "The whole app with a pretend bed, to try things or set up before the bed arrives.",
    lines: ["ET_BED=sim"], docs: "docs/drivers.md" },
];

export default function BedConnection({ snap }: { snap: Snapshot }) {
  const sys = snap.system;
  const current = BEDS.find(b => b.id === sys.driver);
  const [pick, setPick] = useState<string>(BEDS.find(b => b.id !== sys.driver)?.id ?? "sim");
  const [copied, setCopied] = useState(false);
  const chosen = BEDS.find(b => b.id === pick) ?? BEDS[0];
  const text = chosen.lines.join("\n");
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 2000); } catch { /* the lines stay on screen to copy by hand */ }
  };
  return (
    <div className="box bed-connection"><span className="k">Bed connection</span>
      <div className="row"><strong style={{ fontSize: 17 }}>{current?.name ?? sys.name}</strong>
        <span className="dot" aria-hidden="true" style={{ background: sys.online ? "var(--good)" : "var(--warn)" }} /></div>
      <span className="mute" style={{ fontSize: 13 }}>{sys.online ? "Connected." : `Not connected right now${sys.detail ? ` (${sys.detail})` : ""}.`}</span>
      <details style={{ marginTop: 10 }}>
        <summary style={{ cursor: "pointer" }}>Use a different bed</summary>
        <p className="mute" style={{ fontSize: 13 }}>The bed is chosen when the server starts. Put these lines in the server's <code>.env</code> file
          (or the compose file's environment), then restart it, for example with <code>docker compose up -d</code>. Your people, rhythms and history stay.</p>
        <label className="row" style={{ gap: 10 }}><span>Bed</span>
          <select value={pick} onChange={e => setPick(e.target.value)}>
            {BEDS.map(b => <option key={b.id} value={b.id}>{b.name}{b.id === sys.driver ? " (in use)" : ""}</option>)}</select></label>
        <p style={{ fontSize: 13.5, margin: "6px 0" }}>{chosen.what}</p>
        <pre className="codeblock" aria-label="Lines for .env">{text}</pre>
        <div className="row" style={{ justifyContent: "space-between", border: 0 }}>
          <span className="faint" style={{ fontSize: 12 }}>Replace the example values with your own. More in {chosen.docs}.</span>
          <button className="pill" onClick={() => void copy()}>{copied ? "Copied" : "Copy"}</button></div>
        {chosen.note && <p className="faint" style={{ fontSize: 12.5 }}>{chosen.note}</p>}
      </details>
    </div>
  );
}
