import { useState } from "react";
import { api } from "../api";
import Tip from "../components/Tip";
import type { PersonView, Snapshot } from "../types";
import type { Dial } from "./DialSetup";
import { BRANDS } from "../wearables";
import { sideWord } from "./deviceKinds";

/**
 * Devices list rows for things that aren't "sources": a phone reporting through the Android app's "From this phone"
 * switches, and a bedside dial (found through Home Assistant). Both are listed under their person, like any device.
 */

export interface PhoneRow { person: string; name: string; device: string | null; sharing: string[]; seen: number | null }

const LABEL: Record<string, string> = {
  location: "home / away", wifi: "home Wi-Fi", calendar: "calendar times", alarm: "next alarm",
  charging: "charging and bedtime mode", watch: "watch connected", dial: "bedside dial nearby",
};

export const agoText = (s: number) => s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;

function Acts({ name, confirmText, onRemove, edit }: { name: string; confirmText: string; onRemove: () => Promise<void>; edit?: () => void }) {
  const [ask, setAsk] = useState(false);
  return <span className="devline-edit devline-acts">
    {ask
      ? <><button type="button" className="pill" style={{ borderColor: "var(--bad)", color: "var(--bad)" }} onClick={async () => { await onRemove(); setAsk(false); }}>{confirmText}</button>
          <button type="button" className="pill" onClick={() => setAsk(false)}>Keep</button></>
      : <><button type="button" className="pill" aria-label={`Remove ${name}`} onClick={() => setAsk(true)}>Remove</button>
          {edit && <button type="button" className="pill" onClick={edit}>Edit</button>}</>}
  </span>;
}

/** "Alex's Pixel · from this phone · near the dial at night". Remove forgets what it reported (it reports again while its switches are on). */
export function PhoneLine({ r, snap, me, onChanged, say }: { r: PhoneRow; snap: Snapshot; me: string | null; onChanged: () => void; say: (m: string) => void }) {
  const now = Date.now() / 1000;
  const p = snap.people.find(x => x.id === r.person);
  const bedside = p?.presence.fusion?.votes?.find(v => v.signal === "phone:bedside");
  const stale = r.seen == null || now - r.seen > 3600;
  const title = `${r.name}'s ${r.device || "phone"}`;
  return <div className="devline"><div className="devline-main">
    <span className={`sdot ${stale ? "off" : "ok"}`} role="img" aria-label={stale ? "Quiet" : "Reporting"} />
    <span className="devline-id"><b>{title}</b>
      <small className="faint">From this phone · {r.sharing.length ? `shares ${r.sharing.map(s => LABEL[s] ?? s).join(", ")}` : "shares nothing right now"}</small>
      {r.sharing.includes("dial") && p && <small className="faint">{bedside ? "Near the dial at night · counts a little toward in bed" : `Near the ${sideWord(p.zone).toLowerCase()} dial at night, it counts a little toward in bed`}
        <Tip label="About the phone's bedside vote" text="With the phone charging or in bedtime mode at night, a short Bluetooth listen finds the bedside dial. A phone right by its owner's dial is a small (0.15) vote for in bed. It never votes for empty." /></small>}</span>
    <span className="devline-read"><span style={{ color: stale ? "var(--warn)" : undefined }}>{r.seen ? agoText(now - r.seen) : "not yet"}</span>
      <small className="mute">{stale ? "quiet for a while" : bedside ? "says in bed (a weak vote)" : "last report"}</small></span>
    <span className="devline-who tag">{p?.name ?? r.name}</span>
    <Acts name={title} confirmText={`Forget ${title}?`} onRemove={async () => {
      try { await api.del(`/api/people/${r.person}/phone-context`); say(`Forgot ${title}. ${r.person === me ? "Turn off" : "It comes back if"} its switches in the app (Settings → From this phone)${r.person === me ? " to stop it reporting" : " stay on"}.`); onChanged(); }
      catch (e) { say((e as Error).message); } }} />
  </div></div>;
}

/** A bedside dial: online or not, which side, its beacon heard by whose phone. Edit opens the dial setup; Remove hides it. */
export function DialLine({ d, people, onEdit, onChanged, say }: { d: Dial; people: PersonView[]; onEdit: () => void; onChanged: () => void; say: (m: string) => void }) {
  const now = Date.now() / 1000;
  const owner = people.find(p => p.zone === d.side);
  const heardBy = d.heard ? people.find(p => p.id === d.heard!.person) : undefined;
  return <div className="devline"><div className="devline-main">
    <span className={`sdot ${d.online ? "ok" : "off"}`} role="img" aria-label={d.online ? "Online" : "Offline"} />
    <span className="devline-id"><b>{d.name}</b>
      <small className="faint">Bedside dial · Home Assistant{d.firmware ? ` · firmware ${d.firmware.split(" ")[0]}` : ""}</small></span>
    <span className="devline-read"><span>{d.online ? "online" : d.last_seen ? `offline since ${new Date(d.last_seen * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}` : "offline"}</span>
      <small className="mute">{!d.beacon ? (d.side ? `by the ${sideWord(d.side).toLowerCase()} side` : "side not set")
        : heardBy && d.heard?.seen_at ? `beacon ${d.beacon} heard by ${heardBy.name}'s phone ${agoText(now - d.heard.seen_at)}`
        : `beacon ${d.beacon} · not heard by a phone yet`}</small></span>
    <span className="devline-who tag">{owner?.name ?? "Side not set"}</span>
    <Acts name={d.name} confirmText={`Hide ${d.name}?`} edit={onEdit} onRemove={async () => {
      try { await api.put(`/api/dials/${d.id}/hidden`, { hidden: true }); say(`${d.name} hidden. Show it again in the dial setup; delete it in Home Assistant to remove it there.`); onChanged(); }
      catch (e) { say((e as Error).message); } }} />
  </div></div>;
}

/** A cloud account (Oura, Withings, …) one person connected. Remove disconnects it (their history stays). */
export function CloudLine({ provider, person, mine, onChanged, say }: { provider: { id: string; name: string }; person: PersonView; mine: boolean; onChanged: () => void; say: (m: string) => void }) {
  return <div className="devline"><div className="devline-main">
    <span className="sdot ok" role="img" aria-label="Connected" />
    <span className="devline-id"><b>{person.name}'s {provider.name}</b><small className="faint">Cloud account · sleep, heart rate and more, a few times a day</small></span>
    <span className="devline-read"><span>connected</span><small className="mute">Integrations → Cloud accounts</small></span>
    <span className="devline-who tag">{person.name}</span>
    {!mine ? <span className="devline-edit faint" style={{ fontSize: 12 }}>{person.name} manages it</span> : <Acts name={`${person.name}'s ${provider.name}`} confirmText={`Disconnect ${provider.name}?`} onRemove={async () => {
      try { await api.post(`/api/cloud/me/${provider.id}/disconnect`, { person: person.id }); say(`${provider.name} disconnected. What it already sent stays.`); onChanged(); }
      catch (e) { say((e as Error).message); } }} />}
  </div></div>;
}

/** The watch or ring someone picked in Settings that comes in through the phone's health app (no device of its own). */
export function WearableLine({ brand, person }: { brand: string; person: PersonView; say: (m: string) => void }) {
  const b = BRANDS.find(x => x.id === brand);
  return <div className="devline"><div className="devline-main">
    <span className="sdot off" role="img" aria-label="Through the phone" />
    <span className="devline-id"><b>{person.name}'s {b?.name ?? "watch or ring"}</b><small className="faint">Through the phone's health app (Health Connect or Apple Health)</small></span>
    <span className="devline-read"><span>picked in Settings</span><small className="mute">change it in {person.name}'s Settings → Watch or ring</small></span>
    <span className="devline-who tag">{person.name}</span>
    <span className="devline-edit" />
  </div></div>;
}
