import { lazy, Suspense, useEffect, useState } from "react";
import type { PersonSettings } from "./types";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useApp } from "./ctx";
import { nativeBars } from "./native";
import BedSidePicker, { peopleHalves, type Side, useBedFromFoot } from "./components/BedSidePicker";
import { layoutProps, useLayout } from "./components/layout";
import KeyLayer from "./components/KeyLayer";
import ErrorBoundary from "./components/ErrorBoundary";
import { useHardwareKeyboard } from "./keyboard";
import RhythmScreen from "./screens/RhythmScreen";
import Settings from "./screens/Settings";
import SimpleApp from "./screens/SimpleApp";
import Welcome, { needsWelcome } from "./screens/Welcome";
import { useAuth } from "./auth";
import SleepScreen from "./screens/SleepScreen";
import Tonight from "./screens/Tonight";

// Studio is the computer-sized power tool: loaded only when opened, so phones never download it
const Studio = lazy(() => import("./studio/Studio"));

function WhoAmI() {
  const { snap, setMe } = useApp();
  if (!snap) return null;
  const halves = peopleHalves(snap.people);
  for (const p of snap.people) if (p.persona !== "neutral" && halves[p.zone as Side])
    halves[p.zone as Side]!.lines = <small className="mute">{p.persona === "tide" ? "Tide, runs hot" : "Ember, runs cold"}</small>;
  return (
    <div className="screen" style={{ justifyContent: "center", gap: 16 }}>
      <div style={{ display: "grid", gap: 16, width: "100%", maxWidth: 560, margin: "0 auto", justifyItems: "center" }}>
      <h1 style={{ fontSize: 34, justifySelf: "stretch" }}>Who's using this phone?</h1>
      <p className="mute" style={{ justifySelf: "stretch" }}>This sets your side, your default view and your controls. You can change it in Settings.</p>
      <BedSidePicker value={null} halves={halves} label="Whose phone is this"
        onChange={z => { const p = snap.people.find(x => x.zone === z); if (p) setMe(p.id); }} />
      {snap.people.filter(p => p.zone !== "left" && p.zone !== "right").map(p => (
        <button key={p.id} className="side" style={{ padding: 18 }} onClick={() => setMe(p.id)}>{p.name}</button>))}
      </div>
    </div>
  );
}

/** Light or dark, decided only by the phone's setting, the person's choice, or their schedule. */
function useTheme(s: PersonSettings | undefined): "light" | "dark" {
  const [sysLight, setSysLight] = useState(() => window.matchMedia?.("(prefers-color-scheme: light)").matches ?? false);
  const [, tick] = useState(0);
  useEffect(() => {
    const m = window.matchMedia?.("(prefers-color-scheme: light)");
    const on = () => setSysLight(!!m?.matches);
    m?.addEventListener?.("change", on);
    const t = window.setInterval(() => tick(x => x + 1), 60000);
    return () => { m?.removeEventListener?.("change", on); clearInterval(t); };
  }, []);
  if (!s || s.appearance === "system") return sysLight ? "light" : "dark";
  if (s.appearance !== "schedule") return s.appearance;
  const now = new Date(), cur = now.getHours() * 60 + now.getMinutes();
  const mins = (hm: string) => { const [h, m] = hm.split(":").map(Number); return h * 60 + m; };
  const lf = mins(s.light_from), df = mins(s.dark_from);
  const light = lf < df ? cur >= lf && cur < df : cur >= lf || cur < df;
  return light ? "light" : "dark";
}

/** True only once `flag` has stayed true for `ms` (so a normal start-up or a blip of a reconnect shows nothing). */
function useSustained(flag: boolean, ms: number): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (!flag) { setOn(false); return; }
    const t = window.setTimeout(() => setOn(true), ms);
    return () => window.clearTimeout(t);
  }, [flag, ms]);
  return flag && on;
}

function PhoneApp() {
  const { snap, me, connected, toast } = useApp();
  const loc = useLocation();
  const lostServer = useSustained(!connected, 5000);
  const [simpleOn, setSimpleOn] = useState(false);
  const [welcomeDone, setWelcomeDone] = useState(false);
  const auth = useAuth();
  const theme = useTheme(me?.settings);
  const easy = me?.settings.app_mode === "simple";          // the Simple app: Home, Schedule, Me
  const look = simpleOn && !easy ? "amoled" : theme;
  const lay = layoutProps(useLayout());
  const compact = useHardwareKeyboard();                     // Minimal Phone 2 keyboard (or any hardware keyboard / short square screen)
  useEffect(() => { nativeBars(look === "light" ? "#f3ece3" : look === "amoled" ? "#000000" : "#100e12", look === "light"); }, [look]);
  if (!snap) return <div className="app"><div className="screen" style={{ justifyContent: "center", textAlign: "center" }}><h1 style={{ fontFamily: "var(--brand)" }}>Ember &amp; Tide</h1><p className="mute">Connecting…</p></div></div>;
  return (
    <div className={`app ${lay.className}${compact ? " compact" : ""} ${simpleOn && !easy ? (me?.settings.simple_red ? "amoled night" : "amoled") : theme === "light" ? "light" : ""}`} style={lay.style}>
      {compact && me && !easy && !simpleOn && (welcomeDone || !needsWelcome(me, auth.state.enabled, auth.me?.person)) && (
        <nav className="ctabs" aria-label="Main">
          <NavLink end to="/"><kbd>1</kbd>Tonight</NavLink><NavLink to="/rhythm"><kbd>2</kbd>Rhythm</NavLink><NavLink to="/sleep"><kbd>3</kbd>Sleep</NavLink><NavLink to="/settings"><kbd>4</kbd>More</NavLink>
        </nav>
      )}
      {lostServer && <div className="banner">Reconnecting to your server…</div>}
      {!snap.system.online && <div className="banner">The bed isn't reporting right now ({snap.system.detail}).</div>}
      <ErrorBoundary full name="phone" resetKey={`${loc.pathname}|${easy}|${simpleOn}`}>
      {me && !welcomeDone && needsWelcome(me, auth.state.enabled, auth.me?.person) ? <Welcome me={me} onDone={() => setWelcomeDone(true)} /> :
       !me ? <WhoAmI /> : easy ? <SimpleApp /> : (
        <Routes>
          <Route path="/" element={<Tonight setSimpleOn={setSimpleOn} />} />
          <Route path="/rhythm" element={<RhythmScreen />} />
          <Route path="/sleep" element={<SleepScreen />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      )}
      </ErrorBoundary>
      {me && !easy && !simpleOn && (welcomeDone || !needsWelcome(me, auth.state.enabled, auth.me?.person)) && (
        <nav className="tabs" aria-label="Main">
          <NavLink end to="/">Tonight</NavLink><NavLink to="/rhythm">Rhythm</NavLink><NavLink to="/sleep">Sleep</NavLink><NavLink to="/settings">More</NavLink>
        </nav>
      )}
      {me && <ErrorBoundary silent name="keys"><KeyLayer compact={compact} easy={easy} setSimpleOn={setSimpleOn}
        welcome={!welcomeDone && needsWelcome(me, auth.state.enabled, auth.me?.person)} /></ErrorBoundary>}
      {toast && <div className="toast" role="status">{toast}</div>}
      <p className="desk-note">This is the phone view, sized like a phone on purpose. Add it to your home screen on your phone. The full tools are in <a href="/studio">Studio</a>.</p>
    </div>
  );
}

export default function App() {
  const loc = useLocation();
  useBedFromFoot();                                    // load the bed orientation once; left/right labels follow it everywhere
  const { toast, me, snap } = useApp();
  // Studio follows the signed-in person's appearance; with sign-in off (no one signed in) the first person's
  const theme = useTheme(me?.settings ?? snap?.people[0]?.settings);
  if (loc.pathname.startsWith("/studio")) return <div className={theme === "light" ? "light" : ""} style={{ minHeight: "100%", background: "var(--bg)", color: "var(--text)" }}><ErrorBoundary full name="studio" resetKey={loc.pathname}><Suspense fallback={<p className="mute" style={{ padding: 24 }}>Opening Studio…</p>}><Routes><Route path="/studio/*" element={<Studio />} /></Routes></Suspense></ErrorBoundary>{toast && <div className="toast" role="status">{toast}</div>}</div>;
  return <PhoneApp />;
}
