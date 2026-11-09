import { useEffect, useState } from "react";
import { api, isApp } from "../api";

export interface OuraStatus { available: boolean; connected: boolean; needs_reconnect: boolean; connected_at: number | null; last_sync: number | null; last_error: string | null; last_stored?: Record<string, number> | null }

const ago = (t: number) => {
  const s = Date.now() / 1000 - t;
  return s < 120 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
};

/** Connect this person's Oura ring through Oura's cloud (the server holds the sign-in; nothing is stored here). */
export default function OuraConnect({ person, compact = false }: { person: string; compact?: boolean }) {
  return <CloudConnect provider="oura" name="Oura" person={person} compact={compact} />;
}

/** Connect this person's account at a wearable maker (Oura, Withings, WHOOP, Polar) through its cloud. */
export function CloudConnect({ provider, name, person, compact = false }: { provider: string; name: string; person: string; compact?: boolean }) {
  const [st, setSt] = useState<OuraStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => api.get<OuraStatus>(`/api/cloud/me/${provider}?person=${encodeURIComponent(person)}`).then(setSt).catch(() => setSt(null));
  useEffect(() => { void load(); }, [person, provider]);
  // back from Oura's sign-in page (another tab or the in-app browser): look again
  useEffect(() => { const on = () => { if (document.visibilityState === "visible") void load(); }; document.addEventListener("visibilitychange", on); return () => document.removeEventListener("visibilitychange", on); }, [person]);
  if (!st) return null;
  const run = async (label: string, f: () => Promise<unknown>) => {
    setBusy(label); setMsg(null);
    try { await f(); } catch (e) { setMsg((e as Error).message); } finally { setBusy(null); void load(); }
  };
  const connect = () => run("connect", async () => {
    const r = await api.post<{ url: string }>(`/api/cloud/me/${provider}/connect`, { person });
    if (isApp()) window.open(r.url, "_blank"); else location.href = r.url;
  });
  if (!st.available) {
    return <p className="mute oura-note">{name}'s direct connection isn't switched on for this home yet. The person who set up Ember &amp; Tide turns it on once in Studio → Integrations.</p>;
  }
  return (
    <div className={`oura${compact ? " compact" : ""}`}>
      {st.connected && !st.needs_reconnect ? <>
        <div className="row"><span><b style={{ fontWeight: 500 }}>{name} connected</b><br />
          <small className="mute">{st.last_sync ? `Last looked ${ago(st.last_sync)}` : "First look in a moment"}. It checks after you wake, then every hour.</small></span>
          <button className="pill" disabled={!!busy} onClick={() => run("sync", () => api.post(`/api/cloud/me/${provider}/sync`, { person }))}>{busy === "sync" ? "Looking…" : "Look now"}</button></div>
        {st.last_error && <p className="warn-text">{st.last_error}</p>}
        {!compact && <button className="link" disabled={!!busy} onClick={() => run("off", () => api.post(`/api/cloud/me/${provider}/disconnect`, { person }))}>Disconnect {name}</button>}
      </> : <>
        {st.needs_reconnect && <p className="warn-text">{name}'s sign-in ran out. Connect again to keep your nights coming in.</p>}
        <button className="btn pri oura-btn" disabled={!!busy} onClick={connect}>{busy === "connect" ? `Opening ${name}…` : st.needs_reconnect ? `Connect ${name} again` : `Connect ${name}`}</button>
        <small className="mute">Opens {name}'s page to sign in and allow sleep and heart-rate data. Then come back here.</small>
      </>}
      {msg && <p className="warn-text" role="alert">{msg}</p>}
    </div>
  );
}
