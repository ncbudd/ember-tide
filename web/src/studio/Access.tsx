import { useEffect, useRef, useState } from "react";
import BedSidePicker, { BedOrientation, peopleHalves, sideTitle, type Side, SideCheck, useBedFromFoot } from "../components/BedSidePicker";
import Fold from "../components/Fold";
import PetsEditor from "../components/PetsEditor";
import Tip from "../components/Tip";
import { api } from "../api";
import { canShare, publicOrigin, shareText } from "../native";
import { useAuth } from "../auth";
import { weightUnit } from "../format";
import { useApp } from "../ctx";
import type { Snapshot } from "../types";
import { type DeviceKey, deviceKeyName } from "../screens/Account";

interface User { id: string; username: string; role: string; person: string | null; created: number }
interface Invite { id: string; person: string | null; role: string; created: number; expires: number }
interface Token { id: string; name: string; role: string; person: string | null; created: number; last_used: number | null }

const ROLE_TEXT: Record<string, string> = {
  owner: "Owner: everything, including devices and the bed console",
  sleeper: "Sleeper: their own side, plus what the partner shares",
  viewer: "Viewer: can look, can't change anything",
};
const day = (ts: number) => new Date(ts * 1000).toLocaleDateString([], { month: "short", day: "numeric" });

export default function Access() {
  const { snap, say, me } = useApp();
  useBedFromFoot();                                    // re-render the labels when the orientation changes
  const auth = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [usersLoaded, setUsersLoaded] = useState(false);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [tokens, setTokens] = useState<Token[]>([]);
  const [devKeys, setDevKeys] = useState<DeviceKey[]>([]);
  const [invPerson, setInvPerson] = useState<string | null>(null);
  const [invRole, setInvRole] = useState("sleeper");
  const [link, setLink] = useState<string | null>(null);
  const [tokName, setTokName] = useState("Home Assistant");
  const [tokRole, setTokRole] = useState("owner");
  const [newTok, setNewTok] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [reset, setReset] = useState<{ user: string; link: string } | null>(null);
  const [petCount, setPetCount] = useState<number | null>(null);
  const [swapAsk, setSwapAsk] = useState(false);
  const [checkFor, setCheckFor] = useState<string | null>(null);
  const load = () => {
    api.get<User[]>("/api/auth/users").then(u => { setUsers(u); setUsersLoaded(true); }).catch(() => {});
    api.get<Invite[]>("/api/auth/invites").then(setInvites).catch(() => {});
    api.get<Token[]>("/api/auth/tokens").then(setTokens).catch(() => {});
    api.get<DeviceKey[]>("/api/auth/tokens/devices").then(setDevKeys).catch(() => setDevKeys([]));
  };
  useEffect(() => { if (auth.isOwner && auth.state.enabled) load(); }, [auth.isOwner, auth.state.enabled]);
  // pick the side nobody has an account for yet, once, after the account list arrives; after that the choice is yours
  const autoPicked = useRef(false);
  useEffect(() => {
    if (!snap || autoPicked.current || !usersLoaded) return;
    autoPicked.current = true;
    const free = snap.people.find(p => !users.some(u => u.person === p.id));
    setInvPerson((free ?? snap.people[0]).id);
  }, [snap, users, usersLoaded]);
  if (!snap) return null;
  const pname = (id: string | null) => snap.people.find(p => p.id === id)?.name ?? "No side";

  const setMode = async (pid: string, mode: "full" | "simple") => {
    try { await api.patch<Snapshot>(`/api/people/${pid}/settings`, { app_mode: mode }); say(mode === "simple" ? "Simple app on for that phone" : "Full app on for that phone"); }
    catch (x) { say(`Couldn't change it: ${(x as Error).message}`); }
  };
  const modes = <Fold id="studio.access.modes" className="box" title="Phone app" keepSummary defaultOpen
    summary={snap.people.map(p => `${p.name}: ${(p.settings.app_mode ?? "full") === "simple" ? "Simple" : "Full"}`).join(" · ")}>
    <span className="mute" style={{ fontSize: 13 }}>Simple shows just the temperature, hot and cold, and an easy schedule; you keep the details here in Studio.</span>
    {snap.people.map(p => <div className="row" key={p.id}><span>{p.name}</span>
      <div className="seg" style={{ width: 200 }} role="group" aria-label={`Phone app for ${p.name}`}>
        {(["full", "simple"] as const).map(m => <button key={m} aria-pressed={(p.settings.app_mode ?? "full") === m} onClick={() => setMode(p.id, m)}>{m === "full" ? "Full" : "Simple"}</button>)}
      </div></div>)}
  </Fold>;

  const two = snap.people.length === 2;
  const swap = async () => {
    try { await api.post("/api/household/swap-sides"); say("Sides swapped"); } catch (x) { say(`Couldn't swap: ${(x as Error).message}`); }
    setSwapAsk(false);
  };
  const sides = <Fold id="studio.access.sides" className="box" title="Sides of the bed" keepSummary defaultOpen
    summary={snap.people.map(p => `${p.name}: ${sideTitle(p.zone as Side).toLowerCase()}`).join(" · ")}>
    <span className="mute" style={{ fontSize: 13 }}>Left and right as you lie in bed. Not sure? Check it: lie on your side and see where the bed sees you.</span>
    <BedSidePicker value={null} halves={peopleHalves(snap.people)} caption={null} label="Who sleeps on each side" />
    <div className="pills">
      {two && (swapAsk
        ? <><button className="pill" style={{ borderColor: "var(--warm)" }} onClick={swap}>Swap {snap.people[0].name} and {snap.people[1].name}?</button>
            <button className="pill" onClick={() => setSwapAsk(false)}>Keep</button></>
        : <button className="pill" onClick={() => setSwapAsk(true)}>Swap sides</button>)}
      {snap.people.map(p => <button key={p.id} className="pill" aria-pressed={checkFor === p.id} onClick={() => setCheckFor(checkFor === p.id ? null : p.id)}>
        Check sides{two ? ` (${p.name} lies down)` : ""}</button>)}
    </div>
    <BedOrientation onSaid={say} />
    {swapAsk && <span className="faint" style={{ fontSize: 12.5 }}>Plans, history, each person's watch, phone and bedside dial go with them, and so does tonight's night if someone's already in bed. Sensors in the bed keep watching the same spot.</span>}
    {checkFor && <SideCheck key={checkFor} personId={checkFor} onDone={() => setCheckFor(null)} />}
  </Fold>;
  const pets = <Fold id="studio.access.pets" className="box" title="Pets" keepSummary
    summary={petCount == null ? undefined : petCount === 0 ? "none listed" : `${petCount} pet${petCount === 1 ? "" : "s"} · readings ignored`}>
    <span className="mute" style={{ fontSize: 13 }}>A cat or dog on the bed can look like a person to the sensors. List them and their readings are ignored.
      <Tip label="What ignoring a pet means" text={`A light weight on a bed scale, or a fast cat or small-dog heartbeat on the bed sensor, won't start a night, won't end an out-of-bed hold, and isn't used by Autopilot. Nothing about your pets is kept beyond a short note in the night's story ("looked like Biscuit, ignored").`} /></span>
    <PetsEditor weightUnit={weightUnit(me?.settings ?? snap.people[0]?.settings)} showIgnore learned onCount={setPetCount} />
  </Fold>;
  const header = <div><div className="k">Configure</div><h1 style={{ fontSize: 32 }}>People &amp; access</h1>
    <p className="mute" style={{ fontSize: 13 }}>Who can sign in, and keys for Home Assistant, dials and scripts.</p></div>;
  if (!auth.state.enabled) return <>{header}{sides}{modes}{pets}<div className="box"><b style={{ fontWeight: 500 }}>Sign-in is off</b><span className="mute" style={{ fontSize: 13.5 }}>This server runs with <code>ET_AUTH=off</code>, so anyone who can reach it is treated as the owner. That's fine on a trusted home network for testing. Remove the setting and restart to require sign-in; the first visit then asks you to create the owner account.</span></div></>;
  if (!auth.isOwner) return <>{header}<div className="box"><span className="mute">Only the owner can manage people and keys.</span></div></>;

  const makeInvite = async () => {
    try {
      const r = await api.post<{ path: string }>("/api/auth/invites", { person: invRole === "viewer" ? null : invPerson, role: invRole });
      setLink(`${publicOrigin()}${r.path}`); load();
    } catch (e) { say(`Couldn't make the link: ${(e as Error).message}`); }
  };
  const copy = async (t: string) => {
    try { await navigator.clipboard.writeText(t); say("Copied"); return; } catch { /* plain http on the home network has no clipboard API */ }
    const ta = document.createElement("textarea"); ta.value = t; ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta); ta.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove(); say(ok ? "Copied" : "Select the text and copy it");
  };

  return (
    <>
      {header}
      {sides}
      {modes}
      {pets}
      <div className="grid2 even">
        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div className="box">
            <span className="k">Accounts</span>
            <table className="t"><thead><tr><th>Username</th><th>Side</th><th>Role</th><th /></tr></thead><tbody>
              {users.map(u => (
                <tr key={u.id}>
                  <td>{u.username}{u.id === auth.me?.id && <span className="mute"> · you</span>}</td>
                  <td><select value={u.person ?? ""} onChange={async e => { try { await api.patch(`/api/auth/users/${u.id}`, { person: e.target.value || null, set_person: true }); load(); } catch (x) { say((x as Error).message); } }}>
                    <option value="">No side</option>{snap.people.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></td>
                  <td><select value={u.role} onChange={async e => { try { await api.patch(`/api/auth/users/${u.id}`, { role: e.target.value }); load(); } catch (x) { say((x as Error).message); } }}>
                    <option value="owner">Owner</option><option value="sleeper">Sleeper</option><option value="viewer">Viewer</option></select></td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}><button className="pill" onClick={async () => { const r = await api.post<{ path: string }>(`/api/auth/users/${u.id}/reset`); setReset({ user: u.username, link: `${publicOrigin()}${r.path}` }); }}>Reset password</button>{" "}
                    {u.id !== auth.me?.id && (confirm === u.id
                    ? <><button className="pill" style={{ borderColor: "var(--bad)", color: "var(--bad)" }} onClick={async () => { try { await api.del(`/api/auth/users/${u.id}`); say(`Removed ${u.username}`); } catch (x) { say((x as Error).message); } setConfirm(null); load(); }}>Remove {u.username}?</button>{" "}
                        <button className="pill" onClick={() => setConfirm(null)}>Keep</button></>
                    : <button className="pill" onClick={() => setConfirm(u.id)}>Remove</button>)}</td>
                </tr>
              ))}
            </tbody></table>
            {reset && <div className="row" style={{ gap: 8 }}><span style={{ fontSize: 13 }}>Reset link for {reset.user} (24 h, one use):</span></div>}
            {reset && <div className="row" style={{ gap: 8 }}><code className="secret">{reset.link}</code><button className="pill" onClick={() => copy(reset.link)}>Copy</button></div>}
            <span className="faint" style={{ fontSize: 12 }}>Removing an account signs it out everywhere. Its sleep history stays with the person.</span>
          </div>

          <div className="box">
            <span className="k">Invite someone</span>
            <span className="mute" style={{ fontSize: 13 }}>Makes a one-time sign-up link that works for 7 days.<Tip label="How invites work" text={`Whoever opens it picks their own username and password, and stays signed in on their phone (90 days, renewed each time they use it). The account stays until you remove it here. Nothing to install: on an iPhone they can "Add to Home Screen" afterwards.`} /></span>
            <div className="row"><span>As</span>
              <div className="seg" style={{ maxWidth: 320 }}>{["sleeper", "viewer", "owner"].map(r => <button key={r} aria-pressed={invRole === r} onClick={() => setInvRole(r)}>{r[0].toUpperCase() + r.slice(1)}</button>)}</div></div>
            {invRole !== "viewer" && <>
              <BedSidePicker value={(snap.people.find(p => p.id === invPerson)?.zone as Side | undefined) ?? null} label="Their side of the bed"
                caption="As they lie in bed, which side is theirs? Tap it."
                halves={Object.fromEntries(Object.entries(peopleHalves(snap.people)).map(([z, h]) => { const p = snap.people.find(x => x.zone === z); const has = p && users.find(u => u.person === p.id);
                  return [z, { ...h, lines: has ? <small className="mute">has an account ({has.username})</small> : undefined }]; }))}
                onChange={z => { const p = snap.people.find(x => x.zone === z); if (p) setInvPerson(p.id); }} />
              <div><button className="pill" aria-pressed={invPerson === null} onClick={() => setInvPerson(null)}>{invPerson === null ? "✓ " : ""}No side (just looks after things)</button></div></>}
            <span className="faint" style={{ fontSize: 12 }}>{ROLE_TEXT[invRole]}</span>
            {invRole !== "viewer" && invPerson && users.some(u => u.person === invPerson) && <span style={{ fontSize: 12.5, color: "var(--warm)" }}>{pname(invPerson)} already has an account. A second account for the same side is possible, but you probably want the other side.</span>}
            <div><button className="btn pri" onClick={makeInvite}>Make invite link</button></div>
            {link && <div className="row" style={{ gap: 8 }}><code className="secret">{link}</code><button className="pill" onClick={() => copy(link)}>Copy</button>
              {canShare() && <button className="pill" onClick={() => shareText("Ember & Tide", `Here's your link for Ember & Tide: ${link}`)}>Share…</button>}</div>}
            {link && <span className="mute" style={{ fontSize: 12.5 }}>Now send it to them (Messages, email, anything). Only someone with this link can sign up, and only once.</span>}
            {link && location.hostname.match(/^(localhost|127\.)/) && <span className="faint" style={{ fontSize: 12 }}>This link uses "localhost", which only works on this computer. Open Studio by its network address (or your public address) before making links for other devices.</span>}
            {invites.length > 0 && <>
              <span className="k" style={{ marginTop: 6 }}>Open invites</span>
              {invites.map(i => <div className="row" key={i.id}><span>{i.role === "viewer" ? "Viewer" : pname(i.person)} · {i.role}<br /><small className="mute">made {day(i.created)}, works until {day(i.expires)}</small></span>
                <button className="pill" onClick={async () => { await api.del(`/api/auth/invites/${i.id}`); if (invites.length <= 1) setLink(null); load(); }}>Cancel</button></div>)}
            </>}
          </div>
        </div>

        <div className="box" style={{ alignContent: "start" }}>
          <span className="k">Keys for devices and scripts</span>
          <span className="mute" style={{ fontSize: 13 }}>For Home Assistant, bedside dials, the MCP server or your own scripts. They send <code>Authorization: Bearer et_…</code>. A key is shown once, so copy it into the other system straight away.</span>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <input type="text" value={tokName} onChange={e => setTokName(e.target.value)} style={{ flex: 1, minWidth: 140 }} aria-label="Key name" />
            <select value={tokRole} onChange={e => setTokRole(e.target.value)} aria-label="Key access"><option value="owner">Full control</option><option value="viewer">Read only</option></select>
            <button className="btn pri" disabled={!tokName.trim()} onClick={async () => { try { const r = await api.post<{ token: string }>("/api/auth/tokens", { name: tokName.trim(), role: tokRole }); setNewTok(r.token); load(); } catch (e) { say((e as Error).message); } }}>Make key</button>
          </div>
          {newTok && <div className="box" style={{ borderColor: "var(--acc)" }}>
            <span style={{ fontSize: 13 }}>Copy this now. It won't be shown again.</span>
            <div className="row" style={{ gap: 8 }}><code className="secret">{newTok}</code><button className="pill" onClick={() => copy(newTok)}>Copy</button></div>
            <button className="pill" style={{ justifySelf: "start" }} onClick={() => setNewTok(null)}>I've saved it</button>
          </div>}
          <table className="t"><thead><tr><th>Name</th><th>Access</th><th>Last used</th><th /></tr></thead><tbody>
            {tokens.length === 0 && <tr><td colSpan={4} className="mute">No keys yet.</td></tr>}
            {tokens.map(t => <tr key={t.id}><td>{t.name}</td><td>{t.role === "owner" ? "Full control" : t.role === "viewer" ? "Read only" : `${pname(t.person)}'s side`}</td>
              <td className="mute">{t.last_used ? day(t.last_used) : "never"}</td>
              <td style={{ textAlign: "right" }}>{confirm === t.id
                ? <><button className="pill" style={{ borderColor: "var(--bad)", color: "var(--bad)" }} onClick={async () => { await api.del(`/api/auth/tokens/${t.id}`); setConfirm(null); say(`Revoked ${t.name}`); load(); }}>Revoke?</button>{" "}<button className="pill" onClick={() => setConfirm(null)}>Keep</button></>
                : <button className="pill" onClick={() => setConfirm(t.id)}>Revoke</button>}</td></tr>)}
          </tbody></table>
          <span className="k" style={{ marginTop: 6 }}>Phone widgets and watches</span>
          <span className="mute" style={{ fontSize: 13 }}>Each phone makes these by itself after sign-in. They can only show that person's side and change its temperature, flashes and tonight, and they end when that phone signs out.</span>
          <table className="t"><thead><tr><th>Whose</th><th>For</th><th>Last used</th><th /></tr></thead><tbody>
            {devKeys.length === 0 && <tr><td colSpan={4} className="mute">None yet.</td></tr>}
            {devKeys.map(k => <tr key={k.id}><td>{k.username ?? "?"}<br /><small className="mute">{pname(k.person)}</small></td><td>{deviceKeyName(k)}</td>
              <td className="mute">{k.last_used ? day(k.last_used) : "never"}</td>
              <td style={{ textAlign: "right" }}>{confirm === k.id
                ? <><button className="pill" style={{ borderColor: "var(--bad)", color: "var(--bad)" }} onClick={async () => { await api.del(`/api/auth/tokens/${k.id}`); setConfirm(null); say(`Revoked ${k.username ?? ""}'s ${deviceKeyName(k).toLowerCase()} key`); load(); }}>Revoke?</button>{" "}<button className="pill" onClick={() => setConfirm(null)}>Keep</button></>
                : <button className="pill" onClick={() => setConfirm(k.id)}>Revoke</button>}</td></tr>)}
          </tbody></table>
        </div>
      </div>
    </>
  );
}
