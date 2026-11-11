import { useEffect, useRef, useState } from "react";
import { isApp } from "../api";
import { healthConnect, healthSync } from "../native";
import { isAndroidBrowser, isIOS } from "../pwa";
import { BRANDS, gives, type Brand } from "../wearables";
import { CloudConnect } from "./Oura";

export type Platform = "android-app" | "ios-app" | "ios-web" | "android-web" | "desktop";

export function platform(): Platform {
  const cap = (window as unknown as { Capacitor?: { getPlatform?: () => string } }).Capacitor;
  if (isApp()) return cap?.getPlatform?.() === "ios" ? "ios-app" : "android-app";
  if (isIOS()) return "ios-web";
  if (isAndroidBrowser()) return "android-web";
  return "desktop";
}

function Gives({ t }: { t: Brand["hc"] }) {
  const yes = gives(t), some = gives(t, "some");
  if (!yes && !some) return null;
  return <p className="wp-gives">{yes && <>Usually gives <b>{yes}</b>.</>}{some && <> Sometimes {some}, depending on the model.</>}</p>;
}

/** The health-store connect button for the native apps (Health Connect on Android, Apple Health on iPhone). */
function HealthButton({ label }: { label: string }) {
  const [st, setSt] = useState<"idle" | "busy" | "on" | "err">("idle");
  const [msg, setMsg] = useState<string | null>(null);
  const go = async () => {
    setSt("busy"); setMsg(null);
    try {
      const s = await healthConnect();
      if (s.connected) { setSt("on"); void healthSync().catch(() => {}); }
      else { setSt("err"); setMsg("Nothing was allowed. You can try again, or later in Settings → Watch or ring."); }
    } catch (e) { setSt("err"); setMsg((e as Error).message); }
  };
  return <>
    <button className="btn pri" disabled={st === "busy" || st === "on"} onClick={go}>{st === "on" ? "Connected ✓" : st === "busy" ? "Opening…" : label}</button>
    {msg && <p className="warn-text">{msg}</p>}
  </>;
}

/** "Do you have a wearable?": brand tiles, then a short how-to for that brand on this phone.
 *  `collapsible` (first-time setup): once one is picked the others fold away, leaving the chosen one with a Change
 *  button and its how-to right under it; tapping it (or Change) brings the full list back. */
export default function WearablePicker({ person, value, onChange, collapsible = false }: { person: string; value: string; onChange: (id: string) => void; collapsible?: boolean }) {
  const pf = platform();
  const b = BRANDS.find(x => x.id === value);
  const [open, setOpen] = useState(!b);
  const root = useRef<HTMLDivElement>(null);
  const reopenRef = useRef<HTMLButtonElement>(null);
  const folded = collapsible && !!b && !open;
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }      // not on arrival: only after a pick or a Change
    if (!collapsible) return;
    // after a pick: keep the chosen one and its how-to in view (focus follows to the chosen row for keyboards)
    if (folded) { reopenRef.current?.focus({ preventScroll: true }); root.current?.scrollIntoView?.({ block: "nearest" }); }
    else if (b) root.current?.querySelector<HTMLElement>("[aria-checked=true]")?.focus({ preventScroll: true });
  }, [folded]);   // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (id: string) => { onChange(id); if (collapsible) setOpen(false); };
  return (
    <div className={`wp${folded ? " wp-folded" : ""}`} ref={root}>
      {folded ? <button type="button" ref={reopenRef} className="wp-tile wp-chosen" aria-expanded={false} aria-label={`${b!.name}, chosen. Change`} onClick={() => setOpen(true)}>
        <span className="wp-ico" aria-hidden>{b!.icon}</span><span className="wp-chosen-name">{b!.name}</span>
        <span className="wp-change" aria-hidden>Change</span>
      </button>
        : <div className="wp-tiles" role="radiogroup" aria-label="Your watch or ring">
          {BRANDS.map(x => (
            <button key={x.id} type="button" role="radio" aria-checked={value === x.id} className="wp-tile" onClick={() => pick(x.id)}>
              <span className="wp-ico" aria-hidden>{x.icon}</span><span>{x.name}</span>
            </button>
          ))}
        </div>}
      {b && (!collapsible || folded) && <div className="wp-how card" aria-live="polite">{how(b, pf, person)}</div>}
    </div>
  );
}

function how(b: Brand, pf: Platform, person: string) {
  if (b.id === "other") return <p>No problem. The bed's own sensors know when you're in bed, your heart rate and how restless you are. A watch or ring just adds skin temperature and sleep stages.</p>;
  const cloud = b.cloudId ? <CloudConnect provider={b.cloudId} name={b.name.replace(/ ring$/, "")} person={person} compact /> : null;
  if (pf === "android-app") {
    if (!b.hc) return <p>{b.name} doesn't share with Android phones. The bed's own sensors still work well.</p>;
    return <>
      <p className="wp-step"><span className="wp-n">1</span>{b.hcHow}</p>
      <p className="wp-step"><span className="wp-n">2</span>Then let Ember &amp; Tide read it:</p>
      <HealthButton label="Connect Health Connect" />
      <Gives t={b.hc} />
      {cloud && <details className="wp-more"><summary>Or connect {b.name} directly</summary>
        <p className="mute">Brings what Health Connect doesn't carry. Either way is fine; duplicates are skipped.</p>
        {cloud}</details>}
    </>;
  }
  if (pf === "ios-app") {
    if (!b.ah) return <p>{b.name} doesn't share with iPhone. The bed's own sensors still work well.</p>;
    return <>
      {b.ahHow && <p className="wp-step"><span className="wp-n">1</span>{b.ahHow}</p>}
      <p className="wp-step"><span className="wp-n">{b.ahHow ? 2 : 1}</span>Then let Ember &amp; Tide read Apple Health:</p>
      <HealthButton label="Connect Apple Health" />
      <Gives t={b.ah} />
      {cloud && <details className="wp-more"><summary>Or connect {b.name} directly</summary>{cloud}</details>}
    </>;
  }
  // a browser (iPhone web app, Android browser, computer): only cloud connections
  if (cloud) return <>
    <p>Connect your {b.name} account through its cloud. It looks for new nights after you wake, then every hour.</p>
    {cloud}
    <Gives t={b.cloud ?? null} />
  </>;
  if (pf === "ios-web") return b.ah
    ? <p><b style={{ fontWeight: 500 }}>Apple Health: available in the iPhone app.</b> An iPhone web app can't read Apple Health, so {b.name} data will come once the iPhone app is out. Until then the bed's own sensors do the work.</p>
    : <p>{b.name} doesn't share with iPhone. The bed's own sensors still work well.</p>;
  if (pf === "android-web") return b.hc
    ? <p>{b.name} shares through Health Connect, which needs the Ember &amp; Tide Android app. Ask the person who set this up for it, then pick this again in Settings.</p>
    : <p>{b.name} doesn't share with Android phones. The bed's own sensors still work well.</p>;
  return <p>Watches and rings connect from the phone app. On this computer, Studio → Devices shows what's connected.</p>;
}
