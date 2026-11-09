import type { PersonView } from "../types";
import { BRANDS } from "../wearables";
import type { Dial, DialsInfo } from "./DialSetup";
import { type DevSource, ownerOf, personLabel } from "./deviceKinds";
import type { PhoneRow } from "./PhoneRows";

/**
 * Studio → Devices: every kind of device, grouped by whose it is. The ONE place that decides which group a thing goes
 * in, so a new kind of device can't be forgotten: add it to DeviceItem and to deviceGroups() below.
 *
 * - sources (watch, radar, scale, calendar, room, …): ownerOf() (a person, a side's sleeper, or both)
 * - bedside dials: the person on the dial's side; no side yet → shared
 * - phones ("From this phone"): their owner
 * - cloud accounts (Oura, Withings, …): each person who connected one
 * - the watch or ring picked in Settings, when nothing else already stands for it (it comes in through the phone's
 *   health app, so there's no device of its own)
 * "Both of you" only holds things that really are shared (a room sensor, a family calendar, a radar covering the bed).
 */

export interface CloudProvider { id: string; name: string; available: boolean; connected: Record<string, boolean> }

type DeviceItem =
  | { type: "source"; key: string; s: DevSource }
  | { type: "dial"; key: string; d: Dial }
  | { type: "phone"; key: string; r: PhoneRow }
  | { type: "cloud"; key: string; provider: CloudProvider; person: string }
  | { type: "wearable"; key: string; brand: string; person: string };

export interface DeviceGroup { key: string; label: string; person: PersonView | null; items: DeviceItem[] }

export function deviceGroups<S extends DevSource>(people: PersonView[], all: {
  sources: S[]; dials?: DialsInfo | null; phones?: PhoneRow[]; cloud?: CloudProvider[];
}): DeviceGroup[] {
  const groups: DeviceGroup[] = [
    ...people.map(p => ({ key: p.id, label: personLabel(p), person: p, items: [] as DeviceItem[] })),
    { key: "both", label: people.length > 1 ? "Both of you" : "Shared", person: null, items: [] },
  ];
  const into = (owner: string | null | undefined, it: DeviceItem) =>
    (groups.find(g => g.key === owner) ?? groups[groups.length - 1]).items.push(it);

  // one radar or scale covering both sides is stored as a part per side: list it once
  const reps = all.sources.filter(s => !s.group || all.sources.find(x => x.group === s.group) === s);
  for (const s of reps) into(ownerOf(s, people), { type: "source", key: `src-${s.id}`, s });
  for (const d of all.dials?.dials ?? []) {
    if (d.hidden) continue;
    into(people.find(p => p.zone === d.side)?.id, { type: "dial", key: `dial-${d.id}`, d });
  }
  for (const r of all.phones ?? []) into(r.person, { type: "phone", key: `phone-${r.person}`, r });
  for (const pr of all.cloud ?? []) {
    if (!pr.available) continue;
    for (const p of people) if (pr.connected?.[p.id]) into(p.id, { type: "cloud", key: `cloud-${pr.id}-${p.id}`, provider: pr, person: p.id });
  }
  for (const p of people) {
    const b = BRANDS.find(x => x.id === p.settings.wearable);
    if (!b || b.id === "other" && !p.settings.wearable) continue;
    const g = groups.find(x => x.key === p.id)!;
    const covered = g.items.some(it => (it.type === "source" && (it.s.kind === "wearable" || it.s.role === "wearable_hr"))
      || (it.type === "cloud" && it.provider.id === b.cloudId));
    if (!covered) g.items.push({ type: "wearable", key: `wearable-${p.id}`, brand: b.id, person: p.id });
  }
  return groups;
}
