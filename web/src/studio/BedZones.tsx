import BedSidePicker, { type Half, type Side } from "../components/BedSidePicker";
import { personaColor } from "../format";
import type { PersonView } from "../types";

export interface ZonePart { what: string | null; reading?: string | null }

/**
 * The bed from above (the shared BedSidePicker drawing) for a device that watches both sides (radar zones, a
 * scale's load cells per side, two sensors by the nightstands). Each half says what feeds it; tapping a half picks
 * it for mapping.
 */
export default function BedZones({ people, parts, active, onActive, noun = "zone" }: {
  people: PersonView[]; parts: Partial<Record<Side, ZonePart>>; active?: Side | null;
  onActive?: (z: Side) => void; noun?: string;
}) {
  const halves: Partial<Record<Side, Half>> = {};
  for (const z of ["left", "right"] as Side[]) {
    const p = people.find(x => x.zone === z), part = parts[z];
    halves[z] = { name: p && p.name.toLowerCase() !== z ? p.name : undefined, color: p ? personaColor(p) : undefined,
      lines: <><span className={`bz-what${part?.what ? " set" : ""}`}>{part?.what ?? `No ${noun} yet`}</span>
        {part?.reading && <span className="bz-read">{part.reading}</span>}</> };
  }
  return <BedSidePicker value={active ?? null} halves={halves} caption={null} label="Bed sides" className="compact"
    onChange={onActive ? z => { if (z !== "both") onActive(z); } : undefined} />;
}
