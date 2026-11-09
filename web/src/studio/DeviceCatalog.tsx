import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import Fold from "../components/Fold";
import ShowMore from "../components/ShowMore";
import Tip from "../components/Tip";
import { cToF, weight, type WeightUnit } from "../format";
import type { Baseline, Learning, PersonView } from "../types";
import { type DevSource, Ico, type TileId, TILES, ownerOf, sideWord, tileOf, whoText } from "./deviceKinds";
import { type DialsInfo, dialSummary } from "./DialSetup";

/** Studio → Devices: the common things people add, one tile per kind. Every tile can add another (a second
 *  calendar, a partner's ring) and lists what's already there, each with Edit. Plus the Calibration box. */

interface Cal { empty_kg?: Record<string, number>; side_kg?: Record<string, number> }
interface Item { key: string; label: string; who?: string; edit?: string; link?: string }

export default function DeviceCatalog({ sources, people, dials, onAdd, onEdit }: {
  sources: DevSource[]; people: PersonView[]; dials: DialsInfo | null;
  onAdd: (t: TileId) => void; onEdit: (id: string) => void;
}) {
  const [hc, setHc] = useState<string[]>([]);
  const [phones, setPhones] = useState<{ person: string; device: string | null }[]>([]);
  const [oura, setOura] = useState<Record<string, boolean>>({});
  useEffect(() => {
    api.get<{ people?: Record<string, boolean> }>("/api/oura/status").then(r => setOura(r.people ?? {})).catch(() => {});
    api.get<{ person: string; device: string | null }[]>("/api/phone-context").then(setPhones).catch(() => {});
    for (const p of people) api.get<{ device: string }[]>(`/api/people/${p.id}/imports`).then(r => { if (r.length) setHc(x => x.includes(p.id) ? x : [...x, p.id]); }).catch(() => {});
  }, [people.map(p => p.id).join()]);

  const name = (pid: string) => people.find(p => p.id === pid)?.name ?? "";
  const items = (t: TileId): Item[] => {
    const seen = new Set<string>();
    const out: Item[] = sources.filter(s => tileOf(s) === t).filter(s => !s.group || (!seen.has(s.group) && !!seen.add(s.group)))
      .map(s => ({ key: s.id, label: s.name, who: whoText(ownerOf(s, people), people), edit: s.id }));
    if (t === "wearable") {
      hc.forEach(pid => out.push({ key: `hc${pid}`, label: "Health Connect (Android app)", who: name(pid) }));
      Object.entries(oura).filter(([, on]) => on).forEach(([pid]) => out.push({ key: `oura${pid}`, label: "Oura (direct)", who: name(pid) }));
    }
    if (t === "phone") phones.forEach(p => out.push({ key: `ph${p.person}`, label: `Android app${p.device ? ` (${p.device})` : ""}`, who: name(p.person) }));
    if (t === "dials") dials?.dials.filter(d => !d.hidden).forEach(d => out.push({ key: d.id, label: d.name, who: d.side ? whoText(people.find(p => p.zone === d.side)?.id ?? "both", people) : "side not set", edit: `dial:${d.id}` }));
    return out;
  };
  const inUse = TILES.filter(t => items(t.id).length).length;

  return <Fold id="devices.catalog" className="box" title="Add a device" summary={`${inUse} of ${TILES.length} kinds in use`} keepSummary defaultOpen>
    <span className="faint" style={{ fontSize: 12.5 }}>Pick a kind to add one; you can add as many as you like (a second calendar, a partner's ring).</span>
    <div className="catgrid">
      {TILES.map(t => {
        const list = items(t.id);
        return <div key={t.id} className={`cattile${list.length ? " added" : ""}`}>
          <button type="button" className="cat-main" onClick={() => onAdd(t.id)}>
            <Ico name={t.icon} /><span className="cat-text"><b>{t.title}</b><span>{t.adds}</span></span></button>
          {list.length > 0 && <div className="cat-list">
            <ShowMore items={list} limit={3} render={shown => <>{shown.map(i => <div className="cat-row" key={i.key}>
              <span className="cat-item"><span>{i.label}</span>{i.who && !i.label.includes(i.who) && <small className="faint"> · {i.who}</small>}</span>
              {i.edit && <button type="button" className="link" onClick={() => onEdit(i.edit!)} aria-label={`Edit ${i.label}`}>Edit</button>}
              {i.link && <Link className="link" to={i.link}>Open</Link>}</div>)}</>} />
          </div>}
          <span className="cat-foot">
            {list.length > 0 && <span className="cat-ok">Added: {list.length}</span>}
            <button type="button" className="link cat-add" onClick={() => onAdd(t.id)}>{list.length ? "Add another" : t.id === "dials" ? "Set up" : "Add"}</button>
            {t.id === "dials" && dials && <small className="faint">{dialSummary(dials)}</small>}
          </span>
        </div>;
      })}
    </div>
  </Fold>;
}

/** Calibration in one place: what Autopilot has learned per person, and each bed scale's zero and sleeper weight. */
export function Calibration({ people, units, weightUnit = "kg", cal, scales, onEdit }: {
  people: PersonView[]; units: "F" | "C"; weightUnit?: WeightUnit; cal: Cal; scales: DevSource[]; onEdit: (id: string) => void;
}) {
  const [ap, setAp] = useState<Record<string, { learning?: Learning; baseline?: Baseline | null }>>({});
  useEffect(() => {
    for (const p of people) api.get<{ learning?: Learning; baseline?: Baseline | null }>(`/api/people/${p.id}/autopilot`).then(r => setAp(x => ({ ...x, [p.id]: r }))).catch(() => {});
  }, [people.map(p => p.id).join()]);
  const deg = (c: number) => units === "F" ? `${Math.round(cToF(c))}°` : `${c.toFixed(1)}°`;
  return <Fold id="devices.calibration" className="box" title="Calibration" keepSummary defaultOpen
    summary={people.map(p => { const l = ap[p.id]?.learning; return `${p.name}: ${!l ? "…" : l.done ? "knows your normal" : `learning ${l.have} of ${l.need}`}`; }).join(" · ")}>
    <div className="calib">
      {people.map(p => {
        const l = ap[p.id]?.learning, band = ap[p.id]?.baseline?.band;
        const parts = band ? [band.bed_c && `bed ${deg(band.bed_c[0])}–${deg(band.bed_c[1])}`, band.hr && `heart rate ${Math.round(band.hr[0])}–${Math.round(band.hr[1])}`,
          band.skin && `skin ${deg(band.skin[0])}–${deg(band.skin[1])}`].filter(Boolean) : [];
        return <div key={p.id} className="calib-row">
          <span className={`sdot ${!l ? "off" : l.done ? "ok" : "warn"}`} aria-hidden />
          <span className="calib-text"><b>{p.name}</b><span className="mute">{!l ? "Checking…"
            : !l.done ? `Learning ${p.name}'s nights: ${l.have} of ${l.need}. Autopilot only watches until then.`
            : parts.length ? `Normal for ${p.name}: ${parts.join(", ")}. Autopilot stays quiet inside this.` : "Knows a normal night. A watch or ring gives a fuller picture."}</span>
            {l && !l.done && <span className="calib-bar" aria-hidden><span style={{ width: `${Math.min(100, l.have / Math.max(1, l.need) * 100)}%` }} /></span>}</span>
          <Link className="link" to="/studio/autopilot">Autopilot</Link>
        </div>;
      })}
      {scales.map(s => {
        const zero = cal.empty_kg?.[s.id], kg = cal.side_kg?.[s.id];
        return <div key={s.id} className="calib-row">
          <span className={`sdot ${zero != null ? "ok" : "warn"}`} aria-hidden />
          <span className="calib-text"><b>{s.name}{s.group && s.zone ? ` · ${sideWord(s.zone).toLowerCase()} side` : ""}</b><span className="mute">
            {zero != null ? `Empty bed reads about ${weight(zero, weightUnit, 1)}` : "Learns what the empty bed weighs once it has been empty a while"}
            {kg ? `; the sleeper weighs about ${weight(kg, weightUnit)}, so in bed means at least ${weight(kg * 0.6, weightUnit)}.` : "."}
            <Tip label="How the bed scale copes with clutter" text="Duvets, pillows, a weighted blanket or laundry don't count as someone in bed: the scale follows what the empty bed weighs (re-zeroing after it has been empty a while) and asks for most of a person before it says in bed. Short bumps and perching on the edge are ignored." /></span></span>
          <button type="button" className="link" onClick={() => onEdit(s.id)}>Edit</button>
        </div>;
      })}
    </div>
  </Fold>;
}
