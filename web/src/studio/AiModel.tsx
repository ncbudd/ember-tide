import { useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { useApp } from "../ctx";
import type { AiModelList, AiPreset, AiSettings, AiTestResult } from "../types";

/** Studio, AI model: connect the household's own model (local or hosted) without touching environment variables.
 * The key goes to the server and is never shown again (only "ends in 1234"). */
export default function AiModel() {
  const { snap, say } = useApp();
  const auth = useAuth();
  const [s, setS] = useState<AiSettings | null>(null);
  const [preset, setPreset] = useState("off");
  const [url, setUrl] = useState("");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [replaceKey, setReplaceKey] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const fill = (x: AiSettings, p: string) => {
    setS(x); setPreset(p); setKey(""); setReplaceKey(false); setModels([]);
    setUrl(p === x.preset ? x.url : ""); setModel(p === x.preset ? x.model : "");
  };
  const load = () => api.get<AiSettings>("/api/ai/settings").then(x => fill(x, x.preset)).catch(() => {});
  useEffect(() => { if (auth.isOwner) load(); }, [auth.isOwner]);
  const header = <div><div className="k">Configure</div><h1 style={{ fontSize: 32 }}>AI model</h1>
    <p className="mute" style={{ fontSize: 13 }}>Connect your own AI model for Ask and the morning review. Without one, the built-in analysis is used.</p></div>;
  if (!snap) return null;
  if (!auth.isOwner) return <>{header}<div className="box"><span className="mute">Only the owner can change the AI model.</span></div></>;
  if (!s) return header;

  const pr: AiPreset | undefined = s.presets.find(p => p.id === preset);
  const savedHere = preset === s.preset && s.key_set;
  const askKey = !!pr && (pr.needs_key || !!pr.key_optional);
  const showKeyField = askKey && (!savedHere || replaceKey);
  const pick = (p: string) => {
    const x = s.presets.find(q => q.id === p);
    setPreset(p); setMsg(null); setModels([]); setKey(""); setReplaceKey(false);
    if (p === s.preset) { setUrl(s.url); setModel(s.model); } else { setUrl(x?.url ?? ""); setModel(x?.model ?? ""); }
  };
  const body = () => ({ preset, url: url.trim(), model: model.trim(), key: key || undefined });
  const test = async () => {
    setBusy(true); setMsg(null);
    try {
      const t = await api.post<AiTestResult>("/api/ai/settings/test", body());
      setMsg({ ok: t.ok, text: t.ok ? `Works. ${t.model} answered in ${((t.ms ?? 0) / 1000).toFixed(1)} s${t.reply ? `: “${t.reply}”` : ""}.` : t.error ?? "That didn't work." });
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    setBusy(false);
  };
  const loadModels = async () => {
    setBusy(true); setMsg(null);
    try {
      const m = await api.post<AiModelList>("/api/ai/settings/models", body());
      if (m.ok) { setModels(m.models); if (!m.models.length) setMsg({ ok: false, text: "The server didn't list any models." }); }
      else setMsg({ ok: false, text: m.error ?? "Couldn't load the models." });
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    setBusy(false);
  };
  const save = async () => {
    setBusy(true); setMsg(null);
    try { const r = await api.put<AiSettings>("/api/ai/settings", preset === "off" ? { preset: "off" } : body()); fill(r, r.preset); setMsg({ ok: true, text: "Saved." }); say("Saved"); }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    setBusy(false);
  };
  const reset = async () => {
    setBusy(true);
    try { const r = await api.del<AiSettings>("/api/ai/settings"); fill(r, r.preset); setMsg({ ok: true, text: "Back to the server defaults." }); }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    setBusy(false);
  };
  const card = (id: string, name: string, sub: string) =>
    <button key={id} type="button" className="aicard" aria-pressed={preset === id} onClick={() => pick(id)}>
      <b>{name}</b><span>{sub}</span></button>;
  const group = (g: "local" | "hosted", title: string) => <>
    <h2 className="intsec">{title}</h2>
    <div className="aigrid">{s.presets.filter(p => p.group === g).map(p => card(p.id, p.name, p.id === s.preset && s.configured ? "in use" : p.kind === "anthropic" ? "Anthropic key" : p.needs_key ? "needs a key" : "on your network"))}</div></>;
  const ready = preset === "off" || (!!pr && (!pr.needs_key || !!key || savedHere) && (pr.kind === "anthropic" || !!url.trim()));

  return (
    <>
      {header}
      <div className="box">
        <p style={{ fontSize: 14, margin: 0 }}>
          {s.configured ? <>In use: <b>{s.model || "the default model"}</b>{s.url ? ` at ${s.url}` : ""}.</> : "No AI model is connected."}
          {s.source === "env" && s.configured ? " This comes from the server's settings; saving a choice here replaces it." : ""}
        </p>
      </div>
      <h2 className="intsec">Choose</h2>
      <div className="aigrid">{card("off", "Off", "built-in analysis only")}</div>
      {group("local", "On your network")}
      {group("hosted", "Hosted")}
      {pr && <div className="box aiform">
        <h2 style={{ fontSize: 20, margin: 0 }}>{pr.name}</h2>
        <p style={{ fontSize: 13, margin: 0 }}>{pr.help}</p>
        <p style={{ fontSize: 13, margin: 0, color: pr.leaves_house ? "var(--text)" : "var(--good)" }}><b>{pr.note}</b></p>
        {pr.kind === "openai" && <label className="fld">Server address
          <input type="text" value={url} placeholder={pr.url_hint || pr.url} spellCheck={false} onChange={e => setUrl(e.target.value)} /></label>}
        <label className="fld">Model
          <input type="text" value={model} list="ai-models" placeholder={pr.model || "model name"} spellCheck={false} onChange={e => setModel(e.target.value)} />
          <datalist id="ai-models">{[...models, ...pr.models].map(m => <option key={m} value={m} />)}</datalist></label>
        <div className="pills"><button className="btn" disabled={busy} onClick={loadModels}>Load models</button>
          {models.length > 0 && <span className="mute" style={{ fontSize: 13, alignSelf: "center" }}>{models.length} found: tap the model box to pick one.</span>}</div>
        {askKey && (showKeyField
          ? <label className="fld">{pr.key_label ?? "Key"}{pr.key_optional ? " (only if it asks for one)" : ""}
              <input type="password" autoComplete="off" value={key} placeholder="paste it here" onChange={e => setKey(e.target.value)} /></label>
          : <div className="pills" style={{ alignItems: "center" }}><span style={{ fontSize: 14 }}>Key saved, ends in {"••"}{s.key_tail}</span>
              <button className="pill" onClick={() => setReplaceKey(true)}>Replace</button></div>)}
        {askKey && <p className="fld-hint">The key stays on this server and is never shown again.</p>}
        <div className="pills">
          <button className="btn" disabled={busy || !ready} onClick={test}>Test</button>
          <button className="btn pri" disabled={busy || !ready} onClick={save}>Save</button>
        </div>
      </div>}
      {preset === "off" && <div className="box"><p style={{ fontSize: 13, margin: 0 }}>Ask and the morning review use the built-in analysis only. Nothing leaves the house.</p>
        <div className="pills"><button className="btn pri" disabled={busy} onClick={save}>Save</button></div></div>}
      {msg && <p style={{ fontSize: 14, margin: "8px 0", color: msg.ok ? "var(--good)" : "var(--bad)" }} role="status">{msg.text}</p>}
      {s.source === "app" && <div className="pills"><button className="pill" disabled={busy} onClick={reset}>Use the server defaults</button></div>}
    </>
  );
}
