import { useEffect, useState } from "react";
import { h12 } from "../format";
import { api } from "../api";
import { useAuth } from "../auth";
import { useApp } from "../ctx";

interface Provider { id: string; kind: "ntfy" | "ha" | "webhook" | "apprise"; name: string; person: string | null; enabled: boolean; settings: Record<string, string | null | Record<string, string>>; one_tap?: boolean; one_tap_error?: string | null }
interface Sent { ts: number; person: string | null; title: string; body: string }
interface Delivery { ts: number; provider: string; title: string; ok: boolean; error?: string }

const BLANK = { ntfy: { url: "https://ntfy.sh", topic: "", token: "" }, ha: { url: "", token: "", service: "notify.mobile_app_" }, webhook: { url: "", headers: "" }, apprise: { url: "", key: "", urls: "", tag: "", headers: "" } };
type Kind = keyof typeof BLANK;
// "Name: value" lines in the form <-> the headers object the server stores
const parseHeaders = (t: string) => Object.fromEntries(t.split("\n").map(l => l.split(/:(.*)/s)).filter(x => x[0]?.trim() && x[1]?.trim()).map(x => [x[0].trim(), x[1].trim()]));
const LABEL: Record<Kind, string> = { ntfy: "ntfy", ha: "Home Assistant", webhook: "Webhook", apprise: "Apprise" };

export default function Notifications() {
  const { snap, say } = useApp();
  const auth = useAuth();
  const [items, setItems] = useState<Provider[]>([]);
  const [pub, setPub] = useState<{ public_url: string | null; from_env: boolean; push_server?: string | null; push_token_saved?: boolean; fcm?: boolean; fcm_project?: string | null; push_path?: "direct" | "relay" | "off" } | null>(null);
  const [pushEdit, setPushEdit] = useState("");
  const [pushTok, setPushTok] = useState("");
  const [pubEdit, setPubEdit] = useState("");
  const [log, setLog] = useState<{ sent: Sent[]; deliveries: Delivery[]; reach?: Record<string, number> }>({ sent: [], deliveries: [] });
  const [kind, setKind] = useState<Kind>("ntfy");
  const [form, setForm] = useState<Record<string, string>>({ ...BLANK.ntfy });
  const [name, setName] = useState("");
  const [person, setPerson] = useState<string | null>(null);
  const [test, setTest] = useState<Record<string, string>>({});
  const [confirm, setConfirm] = useState<string | null>(null);
  const load = () => {
    api.get<Provider[]>("/api/notify/providers").then(setItems).catch(() => {});
    api.get<{ public_url: string | null; from_env: boolean; push_server?: string | null; push_token_saved?: boolean; fcm?: boolean; fcm_project?: string | null; push_path?: "direct" | "relay" | "off" }>("/api/notify/settings")
      .then(p => { setPub(p); setPubEdit(p.public_url ?? ""); setPushEdit(p.push_server ?? ""); }).catch(() => {});
    api.get<{ sent: Sent[]; deliveries: Delivery[]; reach?: Record<string, number> }>("/api/notify/log").then(setLog).catch(() => {});
  };
  useEffect(() => { if (auth.isOwner) load(); }, [auth.isOwner]);
  useEffect(() => { if (snap && person === null && snap.people[0]) setPerson(snap.people[0].id); }, [snap]);
  if (!snap) return null;
  const pname = (id: string | null) => id ? snap.people.find(p => p.id === id)?.name ?? id : "Household";
  const header = <div><div className="k">Configure</div><h1 style={{ fontSize: 32 }}>Notifications</h1>
    <p className="mute" style={{ fontSize: 13 }}>Where alerts go. Each person picks which ones they want, quiet hours and the tone in their phone's Settings or Studio → Your settings.</p></div>;
  if (!auth.isOwner) return <>{header}<div className="box"><span className="mute">Only the owner can set up where notifications go.</span></div></>;

  const add = async () => {
    try {
      const settings: Record<string, unknown> = { ...form };
      if (kind === "webhook" || kind === "apprise") settings.headers = parseHeaders(form.headers ?? "");
      await api.post("/api/notify/providers", { kind, name: name.trim() || (kind === "ntfy" ? `ntfy · ${form.topic}` : LABEL[kind]), person, enabled: true, settings });
      setForm({ ...BLANK[kind] }); setName(""); say("Added. Press Test to check it arrives."); load();
    } catch (e) { say((e as Error).message); }
  };
  const runTest = async (id: string) => {
    setTest(t => ({ ...t, [id]: "Sending…" }));
    try { const r = await api.post<{ ok: boolean; error?: string }>(`/api/notify/test/${id}`); setTest(t => ({ ...t, [id]: r.ok ? "Sent. Check the phone." : r.error ?? "Failed" })); }
    catch (e) { setTest(t => ({ ...t, [id]: (e as Error).message })); }
    load();
  };
  const randomTopic = () => setForm(f => ({ ...f, topic: `embertide-${Math.random().toString(36).slice(2, 10)}` }));
  const f = (k: string, label: string, ph = "", type = "text") => (
    <label className="field" key={k}><span>{label}</span><input type={type} value={form[k] ?? ""} placeholder={ph} onChange={e => setForm({ ...form, [k]: e.target.value })} /></label>);

  // people whose notifications reach no phone (and no person-wide ntfy / Home Assistant target)
  const unreached = (snap?.people ?? []).filter(p => log.reach && log.reach[p.id] === 0 && !items.some(i => i.enabled && (i.person === p.id || !i.person)));
  return (
    <>
      {header}
      {unreached.map(p => (
        <div key={p.id} className="box" role="alert" style={{ borderColor: "var(--warn)", background: "color-mix(in srgb, var(--warn) 10%, var(--surf))" }}>
          <span><b style={{ fontWeight: 500 }}>{p.name}'s notifications aren't reaching any phone.</b> <span className="mute">On {p.name}'s phone: open Ember &amp; Tide → Settings → Notifications and switch them on (on an iPhone, open it from the Home Screen icon first). Until then their morning summary and questions only show inside the app.</span></span>
        </div>
      ))}
      <div className="grid2 even">
        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div className="box">
            <span className="k">Sending to</span>
            {items.length === 0 && <span className="mute" style={{ fontSize: 13 }}>Nothing yet. Add a phone on the right.</span>}
            <table className="t"><tbody>
              {items.map(p => (
                <tr key={p.id}>
                  <td><b style={{ fontWeight: 500 }}>{p.name}</b><br /><small className="mute">{p.kind === "ntfy" ? `ntfy · ${p.settings.topic}` : p.kind === "ha" ? `Home Assistant · ${p.settings.service}` : `${LABEL[p.kind]} · ${String(p.settings.url ?? "").replace(/^https?:\/\//, "").slice(0, 40)}`} · {pname(p.person)}</small>
                    {p.kind === "ha" && <><br /><small style={{ color: p.one_tap ? "var(--good)" : "var(--mute)" }}>{p.one_tap ? "One-tap buttons: on" : `One-tap buttons: off${p.one_tap_error ? ` (${p.one_tap_error})` : ""}, so buttons open a confirm page`}</small></>}
                    {test[p.id] && <><br /><small style={{ color: test[p.id].startsWith("Sent") ? "var(--good)" : test[p.id] === "Sending…" ? "var(--mute)" : "var(--bad)" }}>{test[p.id]}</small></>}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <button className="pill" onClick={() => runTest(p.id)}>Test</button>{" "}
                    <button className="pill" onClick={async () => { await api.put(`/api/notify/providers/${p.id}`, { ...p, enabled: !p.enabled }); load(); }}>{p.enabled ? "Pause" : "Resume"}</button>{" "}
                    {confirm === p.id
                      ? <><button className="pill" style={{ borderColor: "var(--bad)", color: "var(--bad)" }} onClick={async () => { await api.del(`/api/notify/providers/${p.id}`); setConfirm(null); load(); }}>Remove?</button>{" "}<button className="pill" onClick={() => setConfirm(null)}>Keep</button></>
                      : <button className="pill" onClick={() => setConfirm(p.id)}>Remove</button>}
                  </td>
                </tr>
              ))}
            </tbody></table>
          </div>

          <div className="box">
            <span className="k">Buttons in notifications</span>
            <span className="mute" style={{ fontSize: 13 }}>Buttons like "Wait an hour" call back to this server, so the phone has to be able to reach it. At home the network address works; away from home use your public address (for example a Cloudflare tunnel). Each button works once and expires after 12 hours; no password is ever put in a notification.</span>
            <div className="row" style={{ gap: 8 }}>
              <input type="text" style={{ flex: 1 }} value={pubEdit} placeholder={`${location.protocol}//${location.host}`} onChange={e => setPubEdit(e.target.value)} aria-label="Address phones use" disabled={pub?.from_env} />
              <button className="btn" disabled={pub?.from_env || pubEdit === (pub?.public_url ?? "")} onClick={async () => { try { await api.put("/api/notify/settings", { public_url: pubEdit || null }); say("Saved"); load(); } catch (e) { say((e as Error).message); } }}>Save</button>
            </div>
            {pub?.from_env && <span className="faint" style={{ fontSize: 12 }}>Set by ET_PUBLIC_URL on the server.</span>}
            {!pub?.public_url && <span style={{ fontSize: 12.5, color: "var(--warm)" }}>No address set, so notifications go out without buttons.</span>}
          </div>

          <div className="box">
            <span className="k">App notifications: Google push</span>
            {pub?.push_path === "direct"
              ? <span style={{ fontSize: 13 }}>Direct: your own Firebase project <b>{pub.fcm_project}</b>. Phones with the app turn notifications on in More → Settings; nobody needs anything else installed. Messages are encrypted for each phone, so Google only carries ciphertext.</span>
              : pub?.push_path === "relay"
                ? <span style={{ fontSize: 13 }}>Ember &amp; Tide push relay (the default; nothing to set up). Phones with the app turn notifications on in More → Settings. Messages are encrypted for each phone before they leave this server, so the relay and Google only forward ciphertext. Prefer not to use it? Set <code>ET_PUSH_RELAY=off</code> on the server, or add your own Firebase file (<code>fcm-service-account.json</code> in the data folder) to send direct.</span>
                : <span className="mute" style={{ fontSize: 13 }}>Off. The push relay is turned off (<code>ET_PUSH_RELAY=off</code>) and there is no Firebase file, so the Google-push app can't get notifications. Remove that setting to use the relay, or add your own <code>fcm-service-account.json</code> to the data folder.</span>}
            <span className="faint" style={{ fontSize: 12 }}>Web Push, ntfy, Home Assistant and webhooks work the same whichever you choose.</span>
          </div>

          <div className="box">
            <span className="k">App notifications: ntfy (optional, no Google)</span>
            <span className="mute" style={{ fontSize: 13 }}>For anyone who'd rather not use Google: the app can get its notifications through the ntfy app instead (UnifiedPush), encrypted for each phone. Enter the ntfy server those phones use. Either way, phones with the app get notifications there first; the ntfy and Home Assistant targets above only step in if that fails.</span>
            <div className="row" style={{ gap: 8 }}>
              <input type="text" style={{ flex: 1 }} value={pushEdit} placeholder="https://ntfy.example.com" onChange={e => setPushEdit(e.target.value)} aria-label="ntfy server for the app" />
            </div>
            <div className="row" style={{ gap: 8 }}>
              <input type="password" style={{ flex: 1 }} value={pushTok} autoComplete="off" placeholder={pub?.push_token_saved ? "Access token saved (leave empty to keep)" : "ntfy access token, if your server needs one to post"} onChange={e => setPushTok(e.target.value)} aria-label="ntfy access token" />
              <button className="btn" onClick={async () => { try { await api.put("/api/notify/settings", { push_server: pushEdit || null, push_token: pushTok || null, keep_push_token: !pushTok }); setPushTok(""); say("Saved"); load(); } catch (e) { say((e as Error).message); } }}>Save</button>
            </div>
            <span className="faint" style={{ fontSize: 12 }}>A locked-down ntfy needs either write access for everyone on UnifiedPush topics (<code>up*</code>, the usual setup) or an access token here.</span>
          </div>

          <div className="box">
            <span className="k">Recent notifications</span>
            {log.sent.length === 0 && <span className="mute" style={{ fontSize: 13 }}>Nothing yet.</span>}
            {log.sent.slice(0, 8).map((s, i) => {
              // a notification counts as delivered if some phone or app accepted it within a minute
              const got = log.deliveries.filter(d => Math.abs(d.ts - s.ts) < 60 && d.title === s.title);
              // the delivery log only covers this server run, so stay quiet when there's no record either way
              const status = got.some(d => d.ok) ? "delivered" : got.length ? "couldn't be delivered" : items.length === 0 && !pub?.fcm ? "not sent: no phone set up" : "";
              return <div key={i} className="row"><span>{s.title}<br /><small className="mute">{pname(s.person)} · {new Date(s.ts * 1000).toLocaleString([], { ...h12(), weekday: "short", hour: "numeric", minute: "2-digit", timeZone: snap?.tz })} {status && "· "}<span style={{ color: status === "delivered" ? "var(--good)" : status.startsWith("couldn't") ? "var(--bad)" : undefined }}>{status}</span></small></span></div>;
            })}
            {log.deliveries.filter(d => !d.ok).slice(0, 3).map((d, i) => <span key={i} style={{ fontSize: 12.5, color: "var(--bad)" }}>{d.provider}: {d.error}</span>)}
          </div>
        </div>

        <div className="box" style={{ alignContent: "start" }}>
          <span className="k">Add a target</span>
          <div className="seg" style={{ maxWidth: 360 }}>
            <button aria-pressed={kind === "ntfy"} onClick={() => { setKind("ntfy"); setForm({ ...BLANK.ntfy }); }}>ntfy app</button>
            <button aria-pressed={kind === "ha"} onClick={() => { setKind("ha"); setForm({ ...BLANK.ha }); }}>Home Assistant app</button>
            <button aria-pressed={kind === "apprise"} onClick={() => { setKind("apprise"); setForm({ ...BLANK.apprise }); }}>Apprise</button>
            <button aria-pressed={kind === "webhook"} onClick={() => { setKind("webhook"); setForm({ ...BLANK.webhook }); }}>Webhook</button>
          </div>
          {kind === "ntfy"
            ? <span className="mute" style={{ fontSize: 13 }}>Install ntfy on the phone (Android or iPhone), subscribe to the topic below, and that's it. Buttons work on Android. Use your own ntfy server if you have one; on the public ntfy.sh, pick a hard-to-guess topic.</span>
            : kind === "apprise"
            ? <span className="mute" style={{ fontSize: 13 }}>Sends through an Apprise API server you run, which reaches Gotify, Pushover, Telegram, Matrix, Discord, email and many more. Buttons aren't included; the message opens the app.</span>
            : kind === "webhook"
            ? <span className="mute" style={{ fontSize: 13 }}>Posts JSON (title, body, kind, person, priority, url, ts) to an address you choose, for your own automations. Buttons aren't included.</span>
            : <span className="mute" style={{ fontSize: 13 }}>Uses the Home Assistant Companion app's notify service. Find its name in HA under Developer tools → Actions, search "notify.mobile_app".</span>}
          <div className="authform">
            {kind === "ntfy" ? <>
              {f("url", "ntfy server", "https://ntfy.sh")}
              <label className="field"><span>Topic</span>
                <div className="row" style={{ gap: 8 }}><input type="text" style={{ flex: 1 }} value={form.topic ?? ""} onChange={e => setForm({ ...form, topic: e.target.value })} /><button className="pill" onClick={randomTopic}>Make one up</button></div></label>
              {f("token", "Access token (optional)", "tk_…", "password")}
            </> : kind === "apprise" ? <>
              {f("url", "Apprise API address", "http://apprise:8000")}
              {f("key", "Config key (saved on the Apprise server)", "embertide")}
              {f("urls", "…or Apprise addresses (comma-separated)", "tgram://bottoken/chatid", "password")}
              {f("tag", "Tag (optional)", "")}
              <label className="field"><span>Headers (optional, one per line)</span><textarea rows={2} value={form.headers ?? ""} placeholder="Authorization: Bearer …" onChange={e => setForm({ ...form, headers: e.target.value })} /></label>
            </> : kind === "webhook" ? <>
              {f("url", "Address to post to", "https://example.com/hook")}
              <label className="field"><span>Headers (optional, one per line)</span><textarea rows={2} value={form.headers ?? ""} placeholder="Authorization: Bearer …" onChange={e => setForm({ ...form, headers: e.target.value })} /><small className="mute">Saved values are never shown again.</small></label>
            </> : <>
              {f("url", "Home Assistant address", "http://homeassistant.local:8123")}
              {f("token", "Long-lived access token", "", "password")}
              {f("service", "Notify service", "notify.mobile_app_pixel_9")}
            </>}
            <label className="field"><span>{kind === "webhook" || kind === "apprise" ? "Whose alerts?" : "Whose phone?"}</span>
              <div className="seg" style={{ maxWidth: 360 }}>{snap.people.map(p => <button key={p.id} aria-pressed={person === p.id} onClick={() => setPerson(p.id)}>{p.name}</button>)}
                <button aria-pressed={person === null} onClick={() => setPerson(null)}>Household</button></div>
              <small className="mute">{person ? `${pname(person)}'s alerts, with buttons for their side.` : "Bed problems: offline, a frozen sensor."}</small></label>
            <label className="field"><span>Name (optional)</span><input type="text" value={name} placeholder={kind === "ntfy" ? "Pixel" : "Partner's iPhone"} onChange={e => setName(e.target.value)} /></label>
            <button className="btn pri" onClick={add}>Add</button>
          </div>
        </div>
      </div>
    </>
  );
}
