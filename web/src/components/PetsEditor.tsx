import { useEffect, useState } from "react";
import { api } from "../api";
import { kgTo, toKg, type WeightUnit } from "../format";
import { sideTitle } from "./BedSidePicker";

/** Pets that get on the bed: used only so a cat or dog isn't mistaken for a person (it won't start a night,
 *  end an out-of-bed hold, or feed Autopilot). Owner-only to change. Shared by the welcome, phone Settings and
 *  Studio → People & access.
 *  Flow: a form with Save (nothing is sent while typing); saved pets show as compact summary cards under it with
 *  Edit and Remove; "Add another pet" opens a fresh form. */

export interface Pet {
  id?: string; name: string; kind: "dog" | "cat" | "other"; weight_kg: number;
  size?: "small" | "medium" | "large" | null; sleeps_on_bed: "yes" | "sometimes" | "no"; side: "left" | "right" | "both" | "either";
}
interface Draft extends Omit<Pet, "weight_kg"> { weight: string; sizeSet: boolean }

const sizeOf = (kg: number): "small" | "medium" | "large" => (kg < 10 ? "small" : kg < 25 ? "medium" : "large");
const KIND = { dog: "Dog", cat: "Cat", other: "Other" } as const;
const SIDE_KEYS = ["either", "left", "right", "both"] as const;
const sideText = (k: typeof SIDE_KEYS[number]) => k === "either" ? "Either side" : k === "both" ? "Both sides" : sideTitle(k);
const OFTEN = { yes: "Most nights", sometimes: "Sometimes", no: "Never on the bed" } as const;
const blank = (): Draft => ({ name: "", kind: "dog", weight: "", size: null, sizeSet: false, sleeps_on_bed: "sometimes", side: "either" });

export default function PetsEditor({ weightUnit, showIgnore = false, compact = false, onCount }:
  { /** the viewer's lb / kg (format.weightUnit) */ weightUnit: WeightUnit; showIgnore?: boolean; compact?: boolean; onCount?: (n: number) => void }) {
  const lb = weightUnit === "lb";
  const [pets, setPets] = useState<Pet[] | null>(null);
  const [ignore, setIgnore] = useState(true);
  const [form, setForm] = useState<{ index: number | null; d: Draft } | null>(null);   // index null = a new pet
  const [tried, setTried] = useState(false);                                           // show problems after a Save attempt
  const [confirmRm, setConfirmRm] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ pets: Pet[]; ignore: boolean }>("/api/pets")
      .then(r => { setPets(r.pets); setIgnore(r.ignore); onCount?.(r.pets.length); })
      .catch(() => setPets([]));
  }, []);

  const weightText = (kg: number) => lb ? `${Math.round(kgTo(kg, "lb"))} lb` : `${Math.round(kg * 10) / 10} kg`;
  const toDraft = (p: Pet): Draft => ({ ...p, sizeSet: !!p.size && p.size !== sizeOf(p.weight_kg),
    weight: String(Math.round(kgTo(p.weight_kg, weightUnit) * 10) / 10) });
  const kgOf = (d: Draft) => { const v = parseFloat(d.weight); return Number.isFinite(v) ? toKg(v, weightUnit) : NaN; };
  const problem = (d: Draft): string | null => {
    if (!d.name.trim()) return "Give them a name";
    const kg = kgOf(d);
    if (!Number.isFinite(kg)) return "Add their weight (a guess is fine)";
    if (kg < 0.5 || kg > 90) return lb ? "Weight between 1 and 200 lb" : "Weight between 0.5 and 90 kg";
    return null;
  };

  /** Save the whole list through the existing API; true when it worked. */
  const send = async (next: Pet[], ign = ignore): Promise<boolean> => {
    setBusy(true); setStatus("Saving…");
    try {
      const r = await api.put<{ pets: Pet[]; ignore: boolean }>("/api/pets", { ignore: ign, pets: next });
      setPets(r.pets); onCount?.(r.pets.length);
      setStatus("Saved");
      window.setTimeout(() => setStatus(s => (s === "Saved" ? null : s)), 1500);
      return true;
    } catch (e) { setStatus(`Couldn't save: ${(e as Error).message}`); return false; }
    finally { setBusy(false); }
  };
  const saveForm = async () => {
    if (!form || !pets) return;
    setTried(true);
    if (problem(form.d)) return;
    const d = form.d;
    const pet: Pet = { id: d.id, name: d.name.trim(), kind: d.kind, weight_kg: Math.round(kgOf(d) * 100) / 100,
      size: d.sizeSet ? d.size : null, sleeps_on_bed: d.sleeps_on_bed, side: d.side };
    const next = form.index == null ? [...pets, pet] : pets.map((p, i) => (i === form.index ? pet : p));
    if (await send(next)) { setForm(null); setTried(false); }
  };
  const remove = async (i: number) => {
    if (!pets) return;
    if (await send(pets.filter((_, j) => j !== i))) { setConfirmRm(null); if (form?.index === i) setForm(null); }
  };
  const open = (index: number | null) => { setForm({ index, d: index == null ? blank() : toDraft(pets![index]) }); setTried(false); setConfirmRm(null); };
  const edit = (patch: Partial<Draft>) => setForm(f => (f ? { ...f, d: { ...f.d, ...patch } } : f));

  if (pets === null) return <p className="mute">Loading…</p>;
  const d = form?.d;
  const kg = d ? kgOf(d) : NaN;
  const size = d ? (d.sizeSet && d.size ? d.size : Number.isFinite(kg) ? sizeOf(kg) : null) : null;
  const err = d && tried ? problem(d) : null;
  return (
    <div className={`pets${compact ? " pets-compact" : ""}`}>
      {d && (
        <form className="pet-card pet-form" aria-label={form!.index == null ? "New pet" : `Edit ${pets[form!.index]?.name ?? "pet"}`}
          onSubmit={e => { e.preventDefault(); void saveForm(); }}>
          <b className="pet-form-title">{form!.index == null ? (pets.length ? "Another pet" : "A pet") : `Edit ${pets[form!.index]?.name}`}</b>
          <div className="pet-row">
            <label className="pet-name"><span className="k">Name</span>
              <input type="text" value={d.name} maxLength={40} placeholder="e.g. Biscuit" autoFocus={!compact} onChange={e => edit({ name: e.target.value })} /></label>
            <label className="pet-weight"><span className="k">Weight</span>
              <span className="pet-unit"><input type="number" inputMode="decimal" min={lb ? 1 : 0.5} max={lb ? 200 : 90} step="0.5" value={d.weight}
                placeholder={lb ? "10" : "4.5"} onChange={e => edit({ weight: e.target.value })} /><small>{lb ? "lb" : "kg"}</small></span></label>
          </div>
          <div className="pet-row pet-chips">
            <span className="chips" role="radiogroup" aria-label="Kind">
              {(["dog", "cat", "other"] as const).map(k => <button key={k} type="button" className="chip" role="radio" aria-checked={d.kind === k}
                onClick={() => edit({ kind: k })}>{KIND[k]}</button>)}
            </span>
            <span className="chips" role="radiogroup" aria-label="Size">
              {(["small", "medium", "large"] as const).map(z => <button key={z} type="button" className="chip" role="radio" aria-checked={size === z}
                onClick={() => edit({ size: z, sizeSet: true })}>{z[0].toUpperCase() + z.slice(1)}</button>)}
            </span>
          </div>
          <div className="pet-row pet-chips">
            <span className="mute pet-q">On the bed?</span>
            <span className="chips" role="radiogroup" aria-label="Gets on the bed">
              {(["yes", "sometimes", "no"] as const).map(s => <button key={s} type="button" className="chip" role="radio" aria-checked={d.sleeps_on_bed === s}
                onClick={() => edit({ sleeps_on_bed: s })}>{s === "no" ? "Never" : OFTEN[s]}</button>)}
            </span>
            <select aria-label="Which side" value={d.side} onChange={e => edit({ side: e.target.value as Draft["side"] })}>
              {SIDE_KEYS.map(k => <option key={k} value={k}>{sideText(k)}</option>)}
            </select>
          </div>
          {err && <span className="pet-err" role="alert">{err}</span>}
          <div className="pet-form-actions">
            <button type="button" className="btn" disabled={busy} onClick={() => { setForm(null); setTried(false); }}>Cancel</button>
            <button type="submit" className="btn pri" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
          </div>
        </form>
      )}
      {pets.map((p, i) => i === form?.index ? null : (
        <div key={p.id ?? i} className="pet-card pet-sum">
          <div className="pet-sum-text">
            <b>{p.name}</b>
            <span className="mute">{KIND[p.kind]} · {weightText(p.weight_kg)} · {sideText(p.side)} · {OFTEN[p.sleeps_on_bed]}</span>
          </div>
          {confirmRm === i
            ? <span className="pet-sum-btns"><span className="pet-q">Remove {p.name}?</span>
              <button type="button" className="btn" disabled={busy} onClick={() => setConfirmRm(null)}>Keep</button>
              <button type="button" className="btn pet-rm" disabled={busy} onClick={() => void remove(i)}>Remove</button></span>
            : <span className="pet-sum-btns">
              <button type="button" className="btn" disabled={busy} onClick={() => setConfirmRm(i)}>Remove</button>
              <button type="button" className="btn" disabled={busy} onClick={() => open(i)} aria-label={`Edit ${p.name}`}>Edit</button></span>}
        </div>
      ))}
      <div className="pet-actions">
        {!form && <button type="button" className="btn" onClick={() => open(null)}>{pets.length ? "+ Add another pet" : "+ Add a pet"}</button>}
        {status && <small className="mute" role="status">{status}</small>}
      </div>
      {showIgnore && pets.length > 0 && (
        <label className="row pet-ignore"><span>Ignore readings that look like a pet<br />
          <small className="mute">A light weight on the bed scale, or a cat's fast heart on the bed sensor, isn't counted as someone in bed and isn't used by Autopilot.</small></span>
          <input type="checkbox" className="toggle" checked={ignore} disabled={busy} onChange={e => { const v = e.target.checked; setIgnore(v); void send(pets, v); }} /></label>
      )}
    </div>
  );
}
