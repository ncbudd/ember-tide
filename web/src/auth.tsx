import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, appServer, isApp, setAppSession } from "./api";
import { nativeBars, nativeSignedOut, pushBeforeSignOut } from "./native";

interface Principal { kind: "user" | "token" | "open"; id: string; name: string; role: "owner" | "sleeper" | "viewer"; person: string | null }
interface AuthState { enabled: boolean; setup_needed: boolean; me: Principal | null; people: { id: string; name: string; taken: boolean }[] | null }
interface AuthCtx { state: AuthState; me: Principal | null; isOwner: boolean; signOut: () => Promise<void>; refresh: () => Promise<void> }

const A = createContext<AuthCtx | null>(null);
export function useAuth(): AuthCtx {
  const c = useContext(A);
  if (!c) throw new Error("no auth provider");
  return c;
}

function Shell({ children }: { children: ReactNode }) {
  useEffect(() => { nativeBars("#100e12", false); }, []);       // the sign-in screens are always dark
  return (
    <div className="app authapp">
      {/* Computers get a web page: this brand panel on the left, the form in a card. Phones and the app hide it. */}
      <aside className="authside" aria-hidden>
        <div className="authside-top">
          <div className="brandmark"><span className="e" /><span className="t" /></div>
          <span className="authside-name">Ember &amp; Tide</span>
        </div>
        <div className="authside-copy">
          <h2>Two sides of the bed.<br />One calm night.</h2>
          <p>Each of you gets your own temperature through the night, starting when you get into bed.</p>
        </div>
        <svg className="authside-waves" viewBox="0 0 600 220" preserveAspectRatio="none">
          <path d="M0 70 C80 70 110 150 190 150 S320 120 380 128 S500 150 540 110 S590 40 600 36" fill="none" stroke="var(--cool)" strokeWidth="3" strokeLinecap="round" />
          <path d="M0 110 C70 110 120 175 200 172 S330 150 390 160 S500 170 545 135 S590 80 600 74" fill="none" stroke="var(--warm)" strokeWidth="3" strokeLinecap="round" opacity=".85" />
        </svg>
        <p className="authside-foot">Runs on your own server at home.</p>
      </aside>
      <div className="screen authscreen">
        <div className="authcard">
          <div className="brandmark" aria-hidden><span className="e" /><span className="t" /></div>
          <h1 className="brand">Ember &amp; Tide</h1>
          {children}
        </div>
      </div>
    </div>
  );
}

function Field({ label, type = "text", value, set, auto, hint }: { label: string; type?: string; value: string; set: (v: string) => void; auto: string; hint?: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type={type} value={value} autoComplete={auto} autoCapitalize="none" spellCheck={false} onChange={e => set(e.target.value)} />
      {hint && <small className="mute">{hint}</small>}
    </label>
  );
}

function useSubmit(fn: () => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { await fn(); } catch (x) { setErr((x as Error).message); }
    setBusy(false);
  };
  return { busy, err, submit };
}

function Login({ done }: { done: () => void }) {
  const [u, setU] = useState(""), [p, setP] = useState("");
  const { busy, err, submit } = useSubmit(async () => { const r = await api.post<{ token?: string }>("/api/auth/login", { username: u.trim(), password: p }); if (r.token) setAppSession(null, r.token); done(); });
  return (
    <Shell>
      <p className="mute">Sign in to your bed.</p>
      <form className="authform" onSubmit={submit}>
        <Field label="Username" value={u} set={setU} auto="username" />
        <Field label="Password" type="password" value={p} set={setP} auto="current-password" />
        {err && <p className="err" role="alert">{err}</p>}
        <button className="btn pri big" disabled={busy || !u || !p}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
      {isApp() && <button type="button" className="link" style={{ textAlign: "center" }} onClick={() => { setAppSession("", null); localStorage.removeItem("et-server"); location.reload(); }}>Server: {appServer().replace(/^https?:\/\//, "")} · change</button>}
      <p className="faint" style={{ fontSize: 12.5 }}>Forgot it? The owner can make you a reset link in Studio → People &amp; access. Owners can run <code>reset-password</code> on the server (see the README).</p>
    </Shell>
  );
}

function Setup({ people, done }: { people: { id: string; name: string }[]; done: () => void }) {
  const [u, setU] = useState(""), [p, setP] = useState(""), [p2, setP2] = useState("");
  const [person, setPerson] = useState<string | null>(people[0]?.id ?? null);
  const { busy, err, submit } = useSubmit(async () => {
    if (p !== p2) throw new Error("The two passwords don't match.");
    const r = await api.post<{ token?: string }>("/api/auth/setup", { username: u.trim(), password: p, person });
    if (r.token) setAppSession(null, r.token);
    done();
  });
  return (
    <Shell>
      <p className="mute">Welcome. Make the first account. It becomes the owner, who can set up devices and invite the other sleeper.</p>
      <form className="authform" onSubmit={submit}>
        <Field label="Username" value={u} set={setU} auto="username" hint="Letters, numbers, dots or dashes." />
        <Field label="Password" type="password" value={p} set={setP} auto="new-password" hint="At least 8 characters. A short sentence works well." />
        <Field label="Password again" type="password" value={p2} set={setP2} auto="new-password" />
        <div className="field"><span>Which side is yours?</span>
          <div className="seg">{people.map(x => <button type="button" key={x.id} aria-pressed={person === x.id} onClick={() => setPerson(x.id)}>{x.name}</button>)}
            <button type="button" aria-pressed={person === null} onClick={() => setPerson(null)}>Neither</button></div>
          <small className="mute">You can rename people later in Settings.</small></div>
        {err && <p className="err" role="alert">{err}</p>}
        <button className="btn pri big" disabled={busy || !u || !p || !p2}>{busy ? "Setting up…" : "Create account"}</button>
      </form>
    </Shell>
  );
}

function Join(_props: { done: () => void }) {
  const code = location.hash.slice(1);
  const [inv, setInv] = useState<{ person: string | null; person_name: string | null; role: string; reset_for: string | null } | null>(null);
  const [bad, setBad] = useState<string | null>(null);
  const [u, setU] = useState(""), [p, setP] = useState(""), [p2, setP2] = useState("");
  useEffect(() => {
    if (!code) { setBad("This link is missing its code. Ask for a new invite link."); return; }
    api.get<typeof inv>(`/api/auth/invite?code=${encodeURIComponent(code)}`).then(setInv).catch(e => setBad((e as Error).message));
  }, [code]);
  const { busy, err, submit } = useSubmit(async () => {
    if (p !== p2) throw new Error("The two passwords don't match.");
    const r = await api.post<{ token?: string }>("/api/auth/accept", { code, username: u.trim(), password: p });
    if (r.token) setAppSession(null, r.token);
    location.replace("/");         // fresh start on the app, and the code leaves the address bar
  });
  if (bad) return <Shell><p className="err">{bad}</p><a className="btn big" href="/" style={{ textAlign: "center" }}>Go to sign in</a></Shell>;
  if (!inv) return <Shell><p className="mute">Checking your invite…</p></Shell>;
  if (inv.reset_for) return (
    <Shell>
      <p className="mute">Set a new password for <b>{inv.reset_for}</b>. Every device signed in with the old one gets signed out.</p>
      <form className="authform" onSubmit={submit}>
        <Field label="New password" type="password" value={p} set={setP} auto="new-password" hint="At least 8 characters." />
        <Field label="New password again" type="password" value={p2} set={setP2} auto="new-password" />
        {err && <p className="err" role="alert">{err}</p>}
        <button className="btn pri big" disabled={busy || !p || !p2}>{busy ? "Saving…" : "Set password and sign in"}</button>
      </form>
    </Shell>
  );
  return (
    <Shell>
      <p className="mute">{inv.person_name ? <>You're invited as <b>{inv.person_name}</b>. Your side, your rhythm, your settings.</> : inv.role === "viewer" ? "You're invited to look in (no controls)." : "You're invited."}</p>
      <form className="authform" onSubmit={submit}>
        <Field label="Pick a username" value={u} set={setU} auto="username" />
        <Field label="Password" type="password" value={p} set={setP} auto="new-password" hint="At least 8 characters." />
        <Field label="Password again" type="password" value={p2} set={setP2} auto="new-password" />
        {err && <p className="err" role="alert">{err}</p>}
        <button className="btn pri big" disabled={busy || !u || !p || !p2}>{busy ? "Joining…" : "Join"}</button>
      </form>
      <p className="faint" style={{ fontSize: 12.5 }}>Tip: after you join, use your browser's "Add to Home Screen" so it opens like an app.</p>
    </Shell>
  );
}

/** Where a notification button lands when it can't POST by itself (the Home Assistant app opens a link). */
function ActPage() {
  const code = location.hash.slice(1);
  const [info, setInfo] = useState<{ label: string; person_name: string } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<typeof info>(`/api/notify/act/${encodeURIComponent(code)}`).then(setInfo).catch(e => setMsg((e as Error).message)); }, [code]);
  return (
    <Shell>
      {msg ? <p className="mute">{msg}</p>
        : !info ? <p className="mute">Checking…</p>
        : <>
          <p style={{ textAlign: "center", fontSize: 18 }}>{info.label}<br /><span className="mute" style={{ fontSize: 14 }}>for {info.person_name}'s side</span></p>
          <button className="btn pri big" disabled={busy} onClick={async () => {
            setBusy(true);
            try { const r = await api.post<{ done: string }>(`/api/notify/act/${encodeURIComponent(code)}`); setMsg(r.done); } catch (e) { setMsg((e as Error).message); }
          }}>Do it</button>
        </>}
      <a className="faint" href="/" style={{ textAlign: "center", fontSize: 13 }}>Open Ember &amp; Tide</a>
    </Shell>
  );
}

/** The app's first screen: where is your Ember & Tide server? */
function ServerPick({ done }: { done: () => void }) {
  const [v, setV] = useState("https://");
  const { busy, err, submit } = useSubmit(async () => {
    let url = v.trim().replace(/\/+$/, "");
    if (!/^https?:\/\//.test(url)) url = "https://" + url;
    let host = "";
    try { host = new URL(url).hostname; } catch { throw new Error("That doesn't look like an address."); }
    // plain http only inside the home: anywhere else your password and session would cross the internet unencrypted
    const home = /^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|\[?::1\]?$)/.test(host) || host.endsWith(".local") || host.endsWith(".lan") || host.endsWith(".home.arpa");
    if (url.startsWith("http://") && !home) throw new Error("Use https:// for an address outside your home network, so your password is encrypted.");
    let ok = false;
    try {
      const r = await fetch(url + "/healthz", { credentials: "omit" });
      const j = r.ok ? await r.json() : null;
      ok = !!(j && j.ok === true && typeof j.version === "string");     // it really is an Ember & Tide server
    } catch { ok = false; }
    if (!ok) throw new Error("Couldn't reach Ember & Tide there. Check the address (and that your phone is on the same network for a home address).");
    setAppSession(url, null);
    done();
  });
  return (
    <Shell>
      <p className="mute">Where is your Ember &amp; Tide? The same address you open in a browser.</p>
      <form className="authform" onSubmit={submit}>
        <Field label="Server address" value={v} set={setV} auto="url" hint="For example https://sleep.example.com or http://192.168.1.20:8790" />
        {err && <p className="err" role="alert">{err}</p>}
        <button className="btn pri big" disabled={busy || v.length < 10}>{busy ? "Checking…" : "Continue"}</button>
      </form>
    </Shell>
  );
}

/** Decides between first-time setup, an invite, sign-in, and the app. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState | null>(null);
  const [down, setDown] = useState(false);
  const refresh = useCallback(async () => {
    try { setState(await api.get<AuthState>("/api/auth/state")); setDown(false); } catch { setDown(true); }
  }, []);
  const [needServer, setNeedServer] = useState(() => isApp() && !appServer());
  useEffect(() => { if (!needServer) refresh(); }, [refresh, needServer]);
  useEffect(() => {
    const on = () => refresh();
    window.addEventListener("et-signed-out", on);
    return () => window.removeEventListener("et-signed-out", on);
  }, [refresh]);
  useEffect(() => {
    if (!down) return;
    const quick = window.setTimeout(refresh, 1500);              // a phone just woken often fails the first try
    const t = window.setInterval(() => { if (document.visibilityState !== "hidden") refresh(); }, 4000);   // not in the background
    return () => { clearTimeout(quick); clearInterval(t); };
  }, [down, refresh]);
  // only say we can't reach the server once it has kept failing for a few seconds; until then it's "Connecting…"
  const [downLong, setDownLong] = useState(false);
  useEffect(() => {
    if (!down) { setDownLong(false); return; }
    const t = window.setTimeout(() => setDownLong(true), 5000);
    return () => clearTimeout(t);
  }, [down]);

  if (needServer) return <ServerPick done={() => setNeedServer(false)} />;
  if (location.pathname === "/act") return <ActPage />;
  if (!state) return <Shell>
    <p className="mute">{down && downLong ? "Can't reach your server. Retrying…" : "Connecting…"}</p>
    {down && downLong && isApp() && <button type="button" className="link" onClick={() => { setAppSession("", null); localStorage.removeItem("et-server"); location.reload(); }}>
      Server: {appServer().replace(/^https?:\/\//, "")} · change
    </button>}
  </Shell>;
  if (state.setup_needed) return <Setup people={state.people ?? []} done={refresh} />;
  if (!state.me) return location.pathname === "/join" ? <Join done={refresh} /> : <Login done={refresh} />;
  const signOut = async () => { await pushBeforeSignOut(); try { await api.post("/api/auth/logout"); } finally { if (isApp()) { setAppSession(null, null); await nativeSignedOut(); } await refresh(); } };
  return <A.Provider value={{ state, me: state.me, isOwner: state.me.role === "owner", signOut, refresh }}>{children}</A.Provider>;
}
