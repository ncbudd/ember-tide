import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";
import type { Snapshot, HubDetail } from "../types";
import { LightGuide, useCare } from "./Care";

/**
 * The bed hub's light and quiet fan, shared by Studio → Bed hub and the phone's Settings card, so both use the
 * same words and presets. Owner only (the server refuses anyone else).
 */

/** Calm colours for the hub's light. Off turns the light down to nothing. */
export const LIGHT_COLORS: readonly { name: string; rgb: [number, number, number] }[] = [
  { name: "Warm", rgb: [255, 160, 80] },
  { name: "Amber", rgb: [255, 120, 30] },
  { name: "Rose", rgb: [235, 110, 140] },
  { name: "Moon blue", rgb: [110, 150, 255] },
  { name: "Sage", rgb: [130, 200, 150] },
  { name: "White", rgb: [255, 255, 255] },
];
const same = (a: number[] | null | undefined, b: number[]) => !!a && a.length === 3 && a.every((v, i) => Math.abs(v - b[i]) <= 2);

/** The owner's hub detail, refreshed every few seconds while the page is open. */
export function useHubDetail(every = 5000, enabled = true): HubDetail | null {
  const [d, setD] = useState<HubDetail | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    const load = () => api.get<HubDetail>("/api/hub").then(r => live && setD(r)).catch(() => {});
    load();
    const t = window.setInterval(load, every);
    return () => { live = false; window.clearInterval(t); };
  }, [every, enabled]);
  return d;
}

const has = (snap: Snapshot, cap: string) => snap.system.capabilities.includes(cap);

/** Run a hub change and say plainly if it fails. */
export function useHubCall() {
  const { say } = useApp();
  return async (run: () => Promise<unknown>) => {
    try { await run(); } catch (e) { say((e as Error).message || "That didn't work."); }
  };
}

export function LightControl({ snap }: { snap: Snapshot }) {
  const call = useHubCall();
  const live = snap.system.brightness;
  const [level, setLevel] = useState<number | null>(null);          // while the slider is being dragged
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  if (!has(snap, "led_dim")) return null;
  const shown = level ?? live ?? 0;
  const commit = (v: number) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      call(() => api.put("/api/hub/light", { brightness: v })).finally(() => setLevel(null));
    }, 250);
  };
  const colours = has(snap, "led_rgb");
  const pick = (rgb: [number, number, number]) => call(() => api.put("/api/hub/light", { rgb, ...(shown < 5 ? { brightness: 30 } : {}) }));
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <label className="field" style={{ display: "grid", gap: 6 }}>
        <span className="row"><span>Brightness</span>
          <span className="num mute" aria-live="polite">{shown === 0 ? "Off" : `${shown}%`}</span></span>
        <input type="range" min={0} max={100} step={5} value={shown} aria-label="Hub light brightness"
          style={{ width: "100%", minHeight: 44 }}
          onChange={e => { const v = Number(e.target.value); setLevel(v); commit(v); }} />
      </label>
      {colours && <div style={{ display: "grid", gap: 6 }}>
        <span>Colour</span>
        <div className="swatches hub-swatches" role="radiogroup" aria-label="Hub light colour">
          <button type="button" role="radio" aria-checked={live === 0} aria-label="Off" title="Off"
            style={{ background: "var(--raise)", color: "var(--text)", border: "2px solid var(--line2)", fontSize: 12 }}
            onClick={() => { setLevel(0); call(() => api.put("/api/hub/light", { brightness: 0 })).finally(() => setLevel(null)); }}>Off</button>
          {LIGHT_COLORS.map(c => {
            const on = live !== 0 && same(snap.system.rgb, c.rgb);
            return <button key={c.name} type="button" role="radio" aria-checked={on} aria-label={c.name} title={c.name}
              style={{ background: `rgb(${c.rgb.join(",")})` }} onClick={() => pick(c.rgb)}>{on ? "✓" : ""}</button>;
          })}
        </div>
      </div>}
    </div>
  );
}

/** Phone Settings → Bed hub: everyone sees the status line; the owner also gets the light and the quiet fan. */
export function HubCard({ snap, owner }: { snap: Snapshot; owner: boolean }) {
  const t = snap.system.hub;
  const { care } = useCare();
  const controls = owner && (has(snap, "led_dim") || has(snap, "fan_quiet"));
  const detail = useHubDetail(5000, owner);      // the faults and their advice are the owner's to see
  if (!t && !controls) return null;
  const faults = owner && !t?.ok ? detail?.faults ?? [] : [];
  return (
    <div className="card list">
      {t && <div className="row"><span>Status<br /><small className="mute">{t.ok ? "The hub reports no problems." : "Reported by the hub."}</small></span>
        <strong style={{ color: t.ok ? "var(--good)" : "var(--warn)" }}>{t.line}</strong></div>}
      {faults.map(f => <div className="row" key={f.code}><span>{f.name}<br /><small className="mute">{f.advice}</small></span></div>)}
      <LightGuide guide={care?.hub_guide} id="hub.lightguide.phone" />
      {controls && <div className="item"><LightControl snap={snap} /></div>}
      {controls && <QuietFanControl snap={snap} />}
    </div>
  );
}

export function QuietFanControl({ snap }: { snap: Snapshot }) {
  const call = useHubCall();
  if (!has(snap, "fan_quiet")) return null;
  return (
    <div className="row"><span>Quiet fan<br /><small className="mute">Slower and quieter. The topper may take a little longer to reach a temperature.</small></span>
      <input type="checkbox" className="toggle" aria-label="Quiet fan" checked={!!snap.system.quiet}
        onChange={e => call(() => api.put("/api/hub/fan", { quiet: e.target.checked }))} /></div>
  );
}
