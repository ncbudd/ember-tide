import { useEffect, useState } from "react";
import Tip from "../components/Tip";
import { api } from "../api";
import { publicOrigin } from "../native";
import { useAuth } from "../auth";
import { useApp } from "../ctx";

interface McpKey { id: string; label: string; scope: "read" | "change"; person: string | null; created: number; last_used: number | null }
type Client = "claude-code" | "claude-desktop" | "vscode" | "cursor" | "other";

const CLIENTS: { id: Client; name: string }[] = [
  { id: "claude-code", name: "Claude Code" }, { id: "claude-desktop", name: "Claude Desktop" },
  { id: "vscode", name: "VS Code (Copilot)" }, { id: "cursor", name: "Cursor" }, { id: "other", name: "Other" },
];

/** Ready-to-paste setup for each AI app. `url` is the server's /mcp address, `key` the new key (or a placeholder). */
export function snippet(c: Client, url: string, key: string): { where: string; text: string } {
  const auth = `Bearer ${key}`;
  if (c === "claude-code") return { where: "Run this once in a terminal:", text: `claude mcp add --transport http embertide ${url} --header "Authorization: ${auth}"` };
  if (c === "claude-desktop") return { where: "Add this to claude_desktop_config.json (Settings > Developer > Edit config), then restart Claude Desktop. It needs Node.js (npx):",
    text: JSON.stringify({ mcpServers: { embertide: { command: "npx", args: ["-y", "mcp-remote", url, "--header", "Authorization:${EMBERTIDE_AUTH}"], env: { EMBERTIDE_AUTH: auth } } } }, null, 2) };
  if (c === "vscode") return { where: "Save this as .vscode/mcp.json in a project (or add it with MCP: Add Server), then start the server from the file and use Copilot in Agent mode:",
    text: JSON.stringify({ servers: { embertide: { type: "http", url, headers: { Authorization: auth } } } }, null, 2) };
  if (c === "cursor") return { where: "Add this to ~/.cursor/mcp.json (or the project's .cursor/mcp.json):",
    text: JSON.stringify({ mcpServers: { embertide: { url, headers: { Authorization: auth } } } }, null, 2) };
  return { where: "Any app that supports MCP over HTTP needs just these two things:", text: `URL:    ${url}\nHeader: Authorization: ${auth}\n\nIf the app only speaks stdio, run:\nnpx -y mcp-remote ${url} --header "Authorization:\${EMBERTIDE_AUTH}"\nwith EMBERTIDE_AUTH set to "${auth}".` };
}

export default function McpAccess() {
  const { snap, say } = useApp();
  const auth = useAuth();
  const [keys, setKeys] = useState<McpKey[] | null>(null);
  const [loadErr, setLoadErr] = useState(false);
  const [label, setLabel] = useState("");
  const [scope, setScope] = useState<"read" | "change">("read");
  const [fresh, setFresh] = useState<{ token: string; label: string } | null>(null);
  const [client, setClient] = useState<Client>("claude-code");
  const [base, setBase] = useState<string>(publicOrigin());
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const viewer = auth.me?.role === "viewer";
  const load = () => {
    setLoadErr(false);
    api.get<McpKey[]>("/api/auth/me/mcp-tokens").then(setKeys).catch(() => setLoadErr(true));
  };
  useEffect(() => {
    if (auth.state.enabled && auth.me?.person) load();
    // the owner's public address setting, when there is one (only the owner may read it); else this page's own address
    api.get<{ public_url: string | null }>("/api/notify/settings").then(s => { if (s.public_url) setBase(s.public_url.replace(/\/+$/, "")); }).catch(() => {});
  }, [auth.state.enabled, auth.me?.person]);
  if (!snap) return null;
  const pname = (id: string | null) => snap.people.find(p => p.id === id)?.name ?? "your";
  const day = (ts: number) => new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const url = `${base}/mcp`;

  const copy = async (t: string) => {
    try { await navigator.clipboard.writeText(t); say("Copied"); return; } catch { /* plain http on the home network has no clipboard API */ }
    const ta = document.createElement("textarea"); ta.value = t; ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta); ta.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove(); say(ok ? "Copied" : "Select the text and copy it");
  };
  const make = async () => {
    setBusy(true);
    try { const r = await api.post<{ token: string; label: string }>("/api/auth/me/mcp-tokens", { label: label.trim(), scope }); setFresh({ token: r.token, label: r.label }); setLabel(""); load(); }
    catch (e) { say((e as Error).message); }
    setBusy(false);
  };

  const header = <div><div className="k">Configure</div><h1 style={{ fontSize: 32 }}>Connect an AI app</h1>
    <p className="mute" style={{ fontSize: 13 }}>Talk to your bed from the AI app you already use, such as Claude, Copilot or Cursor: ask how you slept, or have it nudge tonight.</p></div>;
  if (!auth.state.enabled) return <>{header}<div className="box"><span className="mute" style={{ fontSize: 13.5 }}>Sign-in is off on this server, so an AI app can connect to <code>{url}</code> without a key. Turn sign-in on to give each app its own key.</span></div></>;
  if (!auth.me?.person) return <>{header}<div className="box"><span className="mute">This account has no side of the bed, so an AI app would have nothing to look at.</span></div></>;
  if (keys === null) return <>{header}{loadErr && <div className="box"><span className="mute">Couldn't load your keys right now.</span><div><button className="pill" onClick={load}>Retry</button></div></div>}</>;

  const sn = snippet(client, url, fresh?.token ?? "<your key>");
  return (
    <>
      {header}
      <div className="box">
        <span className="k">How it works</span>
        <span className="mute" style={{ fontSize: 13.5 }}>The AI app gets a key that acts as <b>{pname(auth.me.person)}</b> and nobody else. It can look at your nights, your rhythm and Autopilot's suggestions.
          With "read and change" it can also ask for changes; they follow the same limits as Ask, and anything bigger than a small tweak waits for you to tap Apply here in the app.
          Your AI app asks you before it uses each tool.</span>
        <span className="mute" style={{ fontSize: 13.5 }}>Whatever the AI app reads is sent to that app's provider (the company behind it) to answer you. Only connect apps you are happy for that to happen with. You can revoke a key at any time.</span>
      </div>

      <div className="box" style={{ alignContent: "start" }}>
        <span className="k">Make a key</span>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <input type="text" value={label} placeholder="Claude Desktop on laptop" maxLength={60} onChange={e => setLabel(e.target.value)} style={{ flex: 1, minWidth: 180, minHeight: 44 }} aria-label="What this key is for" />
          <select value={viewer ? "read" : scope} disabled={viewer} onChange={e => setScope(e.target.value as "read" | "change")} aria-label="What the AI app may do" style={{ minHeight: 44 }}>
            <option value="read">Look only</option><option value="change">Look and change</option>
          </select>
          <button className="btn pri" style={{ minHeight: 44 }} disabled={busy} onClick={make}>Make key</button>
        </div>
        <span className="faint" style={{ fontSize: 12.5, color: "var(--mute)" }}>{viewer ? "This account can only look, so keys can only read." : "Start with look only. You can always make a second key that can change things."}
          <Tip label="Look and change" text="The AI app can propose the same things Ask can: warmer or cooler tonight, a flash, a rhythm tweak. Small ones apply at once only if you turned on 'small changes' in Settings; everything else waits for your tap in the app." /></span>
        {fresh && <div className="box" style={{ borderColor: "var(--acc)" }}>
          <span style={{ fontSize: 13.5 }}>Key for <b>{fresh.label}</b>. Copy it now: it is shown only once.</span>
          <div className="row" style={{ gap: 8 }}><code className="secret">{fresh.token}</code><button className="pill" style={{ minHeight: 44 }} onClick={() => copy(fresh.token)}>Copy</button></div>
        </div>}
      </div>

      <div className="box" style={{ alignContent: "start" }}>
        <span className="k">Set up your AI app</span>
        <div className="chips" role="tablist" aria-label="AI app">
          {CLIENTS.map(c => <button key={c.id} role="tab" aria-selected={client === c.id} className={"chip" + (client === c.id ? " on" : "")} style={{ minHeight: 44 }} onClick={() => setClient(c.id)}>{c.name}</button>)}
        </div>
        <span className="mute" style={{ fontSize: 13 }}>Address: <code>{url}</code>{base === publicOrigin() && !base.startsWith("https://") ? " (an address your AI app can reach: if it runs on another device, use the public address from Notifications)" : ""}</span>
        <span style={{ fontSize: 13.5 }}>{sn.where}</span>
        <pre className="secret" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", margin: 0, padding: 10 }}>{sn.text}</pre>
        <div className="row" style={{ justifyContent: "flex-start" }}><button className="pill" style={{ minHeight: 44 }} onClick={() => copy(sn.text)}>Copy</button></div>
        {!fresh && <span className="faint" style={{ fontSize: 12.5, color: "var(--mute)" }}>Make a key above and it is filled in for you.</span>}
      </div>

      <div className="box" style={{ alignContent: "start" }}>
        <span className="k">Your keys</span>
        <table className="t"><thead><tr><th>For</th><th>Can</th><th>Last used</th><th /></tr></thead><tbody>
          {keys.length === 0 && <tr><td colSpan={4} className="mute">No keys yet.</td></tr>}
          {keys.map(k => <tr key={k.id}><td>{k.label}<br /><small className="mute">made {day(k.created)}</small></td><td>{k.scope === "change" ? "Look and change" : "Look only"}</td>
            <td className="mute">{k.last_used ? day(k.last_used) : "never"}</td>
            <td style={{ textAlign: "right" }}>{confirm === k.id
              ? <><button className="pill" style={{ minHeight: 44, borderColor: "var(--bad)", color: "var(--bad)" }} onClick={async () => { await api.del(`/api/auth/me/mcp-tokens/${k.id}`); setConfirm(null); say(`Revoked ${k.label}`); load(); }}>Revoke?</button>{" "}<button className="pill" style={{ minHeight: 44 }} onClick={() => setConfirm(null)}>Keep</button></>
              : <button className="pill" style={{ minHeight: 44 }} onClick={() => setConfirm(k.id)}>Revoke</button>}</td></tr>)}
        </tbody></table>
      </div>
    </>
  );
}
