import { useEffect, useMemo, useState } from "react";
import { api } from "../api";

export interface HaEnt { entity_id: string; name: string; domain: string; device_class: string | null; state: string | null; unit: string | null;
  area: string | null; fit: number; added: boolean; guess_kind: string; guess_role: string }

/**
 * Pick one Home Assistant entity from the shared connection, filtered to what fits this kind of device (a watch's
 * heart rate, a radar zone, a bedroom light). "Show everything" lifts the filter. Entity IDs are shown small, under
 * the friendly name. Only used on the Home Assistant path of the add flow and in Edit.
 */
export default function HaPicker({ kind, value, onPick, exclude = [], label = "Home Assistant entity" }: {
  kind: string; value: string; onPick: (e: HaEnt | null) => void; exclude?: string[]; label?: string;
}) {
  const [ents, setEnts] = useState<HaEnt[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [all, setAll] = useState(kind === "custom");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    setErr(null); setEnts(null);
    api.get<{ entities: HaEnt[] }>(`/api/ha/entities?kind=${all ? "custom" : kind}`).then(r => setEnts(r.entities)).catch(e => setErr((e as Error).message));
  }, [kind, all, reload]);
  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    return (ents ?? []).filter(e => !exclude.includes(e.entity_id) && (!t || `${e.name} ${e.entity_id} ${e.area ?? ""}`.toLowerCase().includes(t)));
  }, [ents, q, exclude.join()]);
  if (err) return <div className="hapick-empty"><p style={{ color: "var(--bad)", fontSize: 13, margin: 0 }}>Couldn't load your Home Assistant entities: {err}</p>
    <div className="pills"><button type="button" className="pill" onClick={() => setReload(n => n + 1)}>Try again</button></div></div>;
  return <div className="hapick" role="group" aria-label={label}>
    <div className="hapick-bar">
      <input type="search" placeholder="Search by name or room" value={q} onChange={e => setQ(e.target.value)} aria-label={`Search: ${label}`} />
      {kind !== "custom" && <label className="haselect-all"><input type="checkbox" checked={all} onChange={e => setAll(e.target.checked)} /> Show everything</label>}
    </div>
    {!ents && <p className="faint" style={{ margin: 0 }}>Loading your entities…</p>}
    {ents && <div className="hapick-list" role="listbox" aria-label={label}>
      {shown.slice(0, 200).map(e => {
        const on = value === e.entity_id;
        const used = e.added && !on;
        return <button type="button" key={e.entity_id} role="option" aria-selected={on} disabled={used} className={`hapick-row${on ? " on" : ""}`}
          title={used ? "Already added: use Edit on that device" : undefined} onClick={() => onPick(on ? null : e)}>
          <span className="hapick-radio" aria-hidden>{on ? "●" : "○"}</span>
          <span className="hapick-name"><b>{e.name}</b><small className="faint">{e.area ? `${e.area} · ` : ""}{e.entity_id}</small></span>
          <span className="hapick-state">{e.state ?? "—"}{e.unit ? ` ${e.unit}` : ""}</span>
          <span className="hapick-tags">{used && <span className="tag">already added</span>}</span>
        </button>;
      })}
      {!shown.length && <p className="faint" style={{ padding: 10, margin: 0 }}>{q ? "Nothing matches that search." : all ? "No entities found in Home Assistant." : "Nothing in Home Assistant looks like this kind of device. Tick Show everything to see all entities."}</p>}
    </div>}
  </div>;
}
