import { lieSide } from "../components/BedSidePicker";
import type { PersonView } from "../types";

/**
 * Studio → Devices: the kinds of things people add (one tile each, also step 1 of the add flow), the ways each one
 * connects, and "who is it for". Kept in one place so the catalog, the add flow, the device list and Edit agree.
 */

export interface DevSource {
  id: string; name: string; driver: string; role: string; zone: string | null; person: string | null; kind: string; group?: string | null;
  device: string | null; params: Record<string, string>; enabled: boolean;
}

export type TileId = "wearable" | "phone" | "dials" | "presence" | "scale" | "room" | "light" | "leak" | "calendar" | "other";
export type PathId = "hc" | "ah" | "cloud" | "app" | "iosapp" | "ha" | "mqtt" | "ics" | "webhook" | "sim";

export const ICONS = {
  watch: <><rect x="7" y="6.5" width="10" height="11" rx="3" /><path d="M9 6.5 9.6 3h4.8l.6 3.5M9 17.5l.6 3.5h4.8l.6-3.5M12 10v2.2l1.4 1" /></>,
  dial: <><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="4.5" /><path d="M12 4v2.5" /></>,
  phone: <><rect x="7.5" y="3" width="9" height="18" rx="2.2" /><path d="M11 18h2" /></>,
  radar: <><path d="M5 18a10 10 0 0 1 14 0M7.5 15a6.5 6.5 0 0 1 9 0M10 12.2a3 3 0 0 1 4 0" /><circle cx="12" cy="18.5" r="1" /></>,
  scale: <><rect x="4" y="7" width="16" height="10" rx="2" /><path d="M7 17v2.5M17 17v2.5M9.5 12h5" /></>,
  thermo: <><path d="M10 14.5V5.5a2 2 0 1 1 4 0v9a3.6 3.6 0 1 1-4 0Z" /><path d="M12 10v6" /></>,
  bulb: <><path d="M9 17h6M9.8 20h4.4M12 3.5a5.5 5.5 0 0 0-3.2 10c.7.5 1.2 1.3 1.2 2.1V17h4v-1.4c0-.8.5-1.6 1.2-2.1A5.5 5.5 0 0 0 12 3.5Z" /></>,
  drop: <path d="M12 3.5C12 3.5 6 10.2 6 14.5a6 6 0 0 0 12 0C18 10.2 12 3.5 12 3.5Z" />,
  calendar: <><rect x="4" y="5.5" width="16" height="14" rx="2" /><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4" /></>,
  plus: <><circle cx="12" cy="12" r="8" /><path d="M12 8.5v7M8.5 12h7" /></>,
};
export function Ico({ name, size = 26 }: { name: keyof typeof ICONS; size?: number }) {
  return <svg className="cat-ico" viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{ICONS[name]}</svg>;
}

export interface Tile { id: TileId; icon: keyof typeof ICONS; title: string; adds: string; kinds: string[] }
export const TILES: Tile[] = [
  { id: "wearable", icon: "watch", title: "Watch or ring", adds: "Heart rate, skin temperature and sleep stages from Oura, Apple Watch, Pixel, Galaxy, Garmin, Whoop and more.", kinds: ["wearable"] },
  { id: "phone", icon: "phone", title: "Phone", adds: "Home or away, next alarm, charging at the bedside: built into the Android app.", kinds: ["phone", "location"] },
  { id: "dials", icon: "dial", title: "Bedside dials", adds: "A dial by each side to see and change the temperature, start a flash, check in in the morning.", kinds: [] },
  { id: "presence", icon: "radar", title: "Presence radar", adds: "A second opinion on whether each side is in bed, so nights start and stop at the right moment.", kinds: ["radar", "motion", "pad"] },
  { id: "scale", icon: "scale", title: "Bed scale", adds: "Weight under each side: the most reliable in-bed signal, and it tells sleepers and pets apart.", kinds: ["scale"] },
  { id: "room", icon: "thermo", title: "Room temperature", adds: "Lets Autopilot tell a warm bedroom from a rhythm that's set too warm.", kinds: ["room"] },
  { id: "light", icon: "bulb", title: "Bedroom light", adds: "Lights-off compared with your bedtime, in the tuning guide and the nights explorer.", kinds: ["light"] },
  { id: "leak", icon: "drop", title: "Water leak sensor", adds: "Stops both sides pumping water immediately if a cover or hose leaks.", kinds: ["leak"] },
  { id: "calendar", icon: "calendar", title: "Calendar", adds: "Late evenings and early starts are known, so pre-condition waits and wake-ups fit.", kinds: ["calendar"] },
  { id: "other", icon: "plus", title: "Something else", adds: "Any Home Assistant entity, MQTT topic or web hook: you say what it measures.", kinds: ["custom"] },
];

const WEARABLE_ROLES = ["wearable_hr", "skin_temp", "wearable_hrv"];
export const tileOf = (s: DevSource): TileId => s.kind === "custom" && WEARABLE_ROLES.includes(s.role) ? "wearable"
  : (TILES.find(t => t.kinds.includes(s.kind))?.id ?? "other");
/** Kinds that vote on in bed for one side (and can cover both sides as a left and a right part). */
const SIDE_KINDS = ["radar", "motion", "pad", "scale"];
export const isSided = (kind: string, role: string) => SIDE_KINDS.includes(kind) || (kind === "custom" && (role === "presence" || role === "weight"));

/** Who a kind of device can be for, and the default. "both" = shared (room, light, a family calendar) or, for a bed
 * sensor, both sides (a left and a right part). */
export function whoChoices(tile: TileId, guideOnly: boolean): { both: false | string; default: "both" | "person" | null } {
  if (guideOnly || tile === "wearable" || tile === "phone") return { both: false, default: "person" };
  if (tile === "calendar") return { both: "Both of us (a shared calendar)", default: "person" };
  if (tile === "presence" || tile === "scale") return { both: "One part watches each side (radar zones, load cells per side)", default: "both" };
  if (tile === "room" || tile === "light" || tile === "leak") return { both: "Both of us", default: "both" };
  return { both: "Both of us / the room", default: "both" };
}

/** Whose a device is: a person id, or "both". A bed sensor on one side belongs to whoever sleeps there. */
export function ownerOf(s: DevSource, people: PersonView[]): string {
  if (s.group) return "both";
  if (s.person) return s.person;
  if (s.zone) return people.find(p => p.zone === s.zone)?.id ?? "both";
  return "both";
}

/** Left/Right as you lie (follows the bed-orientation setting; zones stay the bed's names). */
export const sideWord = (z: string) => lieSide(z === "right" ? "right" : "left") === "left" ? "Left" : "Right";
export function personLabel(p: PersonView): string {
  return p.name.toLowerCase() === p.zone ? `${sideWord(p.zone)} side` : `${p.name} (${sideWord(p.zone).toLowerCase()} side)`;
}
export function whoText(owner: string, people: PersonView[]): string {
  if (owner === "both") return people.length > 1 ? "Both" : "Everyone";
  return people.find(p => p.id === owner)?.name ?? "Someone";
}
