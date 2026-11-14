/**
 * Keys for the whole phone app (any hardware keyboard; designed for the Minimal Phone 2's QWERTY), the shortcut
 * sheet (? or holding Sym), and the one-line key-hint bar shown in compact keyboard mode.
 *
 * How a key is handled, first match wins:
 *  1. ? / Sym held → shortcut sheet.  Backspace → back (closes the newest sheet, else back a screen).
 *  2. A screen's own keys: any visible enabled element with data-key="h" (space-separated list) is clicked — inside
 *     the focused [data-nav] item first. Screens use this for Next/Skip, ←/→ nights, phase −/+ etc.
 *  3. J/K or ↑/↓ move between [data-nav] items when the screen has them (e.g. Rhythm phases); Enter opens.
 *  4. App-wide: tabs (1-4 or T/R/L/M), Tonight actions (warmer/cooler, flashes, off/on, pause, how it feels).
 * Keys never fire while typing into a text field, or with Ctrl/Alt/Meta held.
 */
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useApp } from "../ctx";
import { stepC } from "../format";
import { type ActionId, typingInto, useLauncherActions } from "../keyboard";
import { handleBack } from "./back";
import Sheet from "./Sheet";

const HINTS_OFF = "et-key-hints-off";

function keyName(e: KeyboardEvent): string {
  const k = e.key;
  if (k.length === 1) return k.toLowerCase();
  return k.toLowerCase();             // arrowup, enter, escape, backspace …
}

function visible(el: Element): boolean {
  const h = el as HTMLElement;
  if ((h as HTMLButtonElement).disabled || h.getAttribute("aria-disabled") === "true") return false;
  // data-key-always: the key works even when compact mode hides the button (e.g. "Tonight ⋯")
  return h.hasAttribute("data-key-always") || !!(h.offsetParent || h.getClientRects().length);
}

/** data-cycle="v" on a group of buttons: the key presses the next one (Me → Both → partner → Me). */
function cycle(k: string): boolean {
  const g = document.querySelector<HTMLElement>(`[data-cycle~="${CSS.escape(k)}"]`);
  if (!g) return false;
  const bs = Array.from(g.querySelectorAll<HTMLButtonElement>("button")).filter(b => !b.disabled);
  if (!bs.length) return false;
  const i = bs.findIndex(b => b.getAttribute("aria-pressed") === "true");
  bs[(i + 1) % bs.length].click();
  return true;
}

/** The screen's own element for this key, if any (the focused list item's first). */
function screenKey(k: string): HTMLElement | null {
  const sel = `[data-key~="${CSS.escape(k)}"]`;
  const item = (document.activeElement as HTMLElement | null)?.closest?.("[data-nav]");
  const scoped = item ? Array.from(item.querySelectorAll(sel)).find(visible) : null;
  if (scoped) return scoped as HTMLElement;
  // sheets and dialogs sit on top: their keys win over the screen underneath
  const layer = document.querySelector(".sheet") ?? document;
  return (Array.from(layer.querySelectorAll(sel)).find(visible) as HTMLElement | undefined) ?? null;
}

function moveNav(dir: 1 | -1): boolean {
  let items = Array.from(document.querySelectorAll<HTMLElement>("[data-nav]")).filter(visible);
  if (!items.length) {
    // no list on this screen: J/K step through its controls, like Tab / Shift+Tab
    const scope = document.querySelector(".sheet") ?? document.querySelector(".screen");
    // text fields are left out: stepping into one would turn the next J into a typed letter (Tab still reaches them)
    items = Array.from(scope?.querySelectorAll<HTMLElement>("button, a[href], select, input[type=checkbox], input[type=radio], [tabindex='0']") ?? []).filter(visible);
  }
  if (!items.length) return false;
  const cur = (document.activeElement as HTMLElement | null)?.closest?.("[data-nav]") as HTMLElement | null;
  const i = cur ? items.indexOf(cur) : -1;
  const next = items[Math.max(0, Math.min(items.length - 1, i < 0 ? (dir > 0 ? 0 : items.length - 1) : i + dir))];
  const target = (next.matches("button,a,[tabindex]") ? next : next.querySelector<HTMLElement>("button,a,[tabindex]")) ?? next;
  if (!target.hasAttribute("tabindex") && !target.matches("button,a")) target.setAttribute("tabindex", "-1");
  target.focus();
  target.scrollIntoView({ block: "nearest" });
  return true;
}

const ROUTE_HINTS: Record<string, string> = {
  "/": "↑↓ warmer/cooler · H hot flash · C cold flash · O off · S more · V whose · ? keys",
  "/rhythm": "J/K phase · ←→ temperature · Enter open · 1–4 tabs · ? keys",
  "/sleep": "←→ nights · 1–4 tabs · ? keys",
  "/settings": "J/K move · Enter change · 1–4 tabs · ? keys",
};

export default function KeyLayer({ compact, easy, setSimpleOn, welcome = false }: { compact: boolean; easy: boolean; setSimpleOn: (v: boolean) => void; welcome?: boolean }) {
  const { me, snap, act, say } = useApp();
  const nav = useNavigate();
  const loc = useLocation();
  const [sheet, setSheet] = useState(false);
  const [hintsOff, setHintsOff] = useState(() => { try { return localStorage.getItem(HINTS_OFF) === "1"; } catch { return false; } });
  const feelUntil = useRef(0);
  const symDown = useRef<number | null>(null);
  const state = useRef({ me, snap, easy, path: loc.pathname });
  state.current = { me, snap, easy, path: loc.pathname };

  /** One action, the same whether it came from a key, a launcher shortcut or a typed command. */
  const run = (a: ActionId, src: "keyboard" | "launcher" = "keyboard"): boolean => {
    const { me: p, snap: s } = state.current;
    if (!p || !s) return false;
    const base = `/api/people/${p.id}`;
    const n = p.night;
    const flashOn = !!(n.flash && s.now < n.flash.end);
    const step = stepC(p.settings.units, p.settings.step_f);
    const g = n.guest;
    if (g) {
      // a side lent to a guest: only its temperature and on/off; flashes, pause and the rest don't apply
      const gset = (body: { on?: boolean; temp_c?: number }) => void act(`/api/guest/${p.zone}/set`, body);
      switch (a) {
        case "warmer": case "cooler":
          gset({ temp_c: Math.round((g.temp_c + (a === "warmer" ? step : -step)) * 100) / 100, ...(g.on ? {} : { on: true }) });
          say(a === "warmer" ? "Guest side a little warmer" : "Guest side a little cooler"); return true;
        case "off": gset({ on: !g.on }); say(g.on ? "Guest side off" : "Guest side on"); return true;
        case "on": if (!g.on) { gset({ on: true }); say("Guest side on"); return true; } return false;
        case "simple": case "tonight": break;
        default: say("This side is lent to a guest: just ↑ ↓ for temperature and O for on/off."); return true;
      }
    }
    switch (a) {
      case "hot": case "cold":
        void act(`${base}/flash`, { kind: a, source: src }); say(a === "hot" ? "Hot flash" : "Cold flash"); return true;
      case "extend":
        if (!flashOn) return false;
        void act(`${base}/flash/extend?source=${src}`); say("5 more minutes"); return true;
      case "stop":
        if (!n.flash) return false;
        void act(`${base}/flash/stop?source=${src}`); say("Flash stopped"); return true;
      case "warmer": case "cooler": {
        if (n.target_c == null) { say("Your side is off right now. N turns it on."); return true; }
        const sign = a === "warmer" ? 1 : -1;
        void act(`${base}/adjust`, { delta_c: sign * step, until: "next_phase", source: src });
        say(a === "warmer" ? "A little warmer" : "A little cooler"); return true;
      }
      case "off":
        if (n.override?.manual) { void act(`${base}/adjust/clear?source=${src}`); say("Turned off"); return true; }
        if (n.state === "skipped") return run("on", src);
        void act(`${base}/tonight`, { action: "skip", source: src }); say("Off for tonight"); return true;
      case "on":
        if (n.state === "skipped" || n.state === "paused") { void act(`${base}/tonight`, { action: "resume", source: src }); say("Back on"); return true; }
        if (n.target_c == null) {
          const t = p.phases[0]?.temp_c;
          if (t == null) return false;
          void act(`${base}/adjust`, { temp_c: t, minutes: 60, until: "next_phase", source: src }); say("On for an hour"); return true;
        }
        return false;
      case "pause":
        void act(`${base}/tonight`, { action: "pause", minutes: 60, source: src }); say("Paused for an hour"); return true;
      case "simple":
        if (!state.current.easy) { nav("/"); setSimpleOn(true); }
        return true;
      case "tonight":
        setSimpleOn(false); nav("/"); return true;
    }
    return false;
  };

  useLauncherActions(a => { run(a, "launcher"); });

  useEffect(() => {
    const feel = (v: "too_cold" | "just_right" | "too_hot") => {
      const p = state.current.me;
      if (!p) return;
      void act(`/api/people/${p.id}/feel`, { feel: v });
      say(v === "just_right" ? "Noted: just right" : `Noted: ${v === "too_hot" ? "too hot" : "too cold"}`);
    };
    const onDown = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || typingInto(e)) return;   // a screen handled it already
      const k = keyName(e);
      // Sym (or AltGraph on some keyboards) held for half a second opens the shortcut sheet
      if (k === "symbol" || k === "symbollock" || k === "altgraph") {
        if (symDown.current == null) symDown.current = Date.now();
        else if (Date.now() - symDown.current > 450) { setSheet(true); symDown.current = Infinity; }
        return;
      }
      if (k === "?" || (k === "/" && e.shiftKey)) { e.preventDefault(); setSheet(v => !v); return; }
      if (k === "backspace") { e.preventDefault(); if (!handleBack()) history.length > 1 && history.back(); return; }
      if (k === "escape") { if (!document.querySelector(".sheet")) handleBack(); return; }
      if (e.repeat) return;

      // "how does it feel": F, then 1 / 2 / 3 within a few seconds
      if (Date.now() < feelUntil.current && (k === "1" || k === "2" || k === "3")) {
        feelUntil.current = 0;
        e.preventDefault();
        feel(k === "1" ? "too_cold" : k === "2" ? "just_right" : "too_hot");
        return;
      }

      // Enter on a focused button/link already presses it: don't press a second thing
      const fe = document.activeElement as HTMLElement | null;
      if (k === "enter" && fe && fe !== document.body && fe.matches("button,a,[role=radio],[role=button],input,select")) return;
      const own = screenKey(k);
      if (own) { e.preventDefault(); own.click(); return; }
      if (cycle(k)) { e.preventDefault(); return; }
      if (document.querySelector(".wl")) return;          // the welcome: only its own keys
      const onTonightNow = state.current.easy || state.current.path === "/";
      if ((k === "j" && !onTonightNow) || (k === "arrowdown" && document.querySelector("[data-nav]"))) { if (moveNav(1)) { e.preventDefault(); return; } }
      if ((k === "k" && !onTonightNow) || (k === "arrowup" && document.querySelector("[data-nav]"))) { if (moveNav(-1)) { e.preventDefault(); return; } }

      if (document.querySelector(".sheet")) return;      // a sheet is open: arrows scroll it, letters do nothing
      const { easy: simpleApp, path } = state.current;
      if (!simpleApp) {
        const tab: Record<string, string> = { "1": "/", t: "/", "2": "/rhythm", r: "/rhythm", "3": "/sleep", l: "/sleep", "4": "/settings", m: "/settings" };
        if (tab[k]) { e.preventDefault(); setSimpleOn(false); nav(tab[k]); return; }
      }
      const onTonight = simpleApp || path === "/";
      const map: Record<string, ActionId> = { h: "hot", c: "cold", x: "stop", e: "extend" };
      if (onTonight) Object.assign(map, { arrowup: "warmer", "+": "warmer", "=": "warmer", arrowdown: "cooler", "-": "cooler", o: "off", n: "on", p: "pause" });
      if (map[k]) { if (run(map[k])) e.preventDefault(); return; }
      if (onTonight && k === "f") {
        e.preventDefault();
        feelUntil.current = Date.now() + 5000;
        say("How does it feel? 1 too cold · 2 just right · 3 too hot");
      }
    };
    const onUp = (e: KeyboardEvent) => {
      const k = keyName(e);
      if (k === "symbol" || k === "symbollock" || k === "altgraph") symDown.current = null;
    };
    window.addEventListener("keydown", onDown);
    window.addEventListener("keyup", onUp);
    return () => { window.removeEventListener("keydown", onDown); window.removeEventListener("keyup", onUp); };
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  const hint = welcome ? "Enter next · ← or Esc back · ↑↓ choose · Tab move"
    : easy ? (loc.pathname === "/" && me?.night.guest ? "Guest side · ↑↓ temperature · O on/off · 1 2 3 tabs · ? keys" : loc.pathname === "/" ? "↑↓ warmer/cooler · H hot flash · C cold flash · O off · F feel · 1 2 3 tabs · ? keys" : "1 Home · 2 Schedule · 3 Me · ? keys")
    : loc.pathname === "/" && me?.night.guest ? "Guest side · ↑↓ temperature · O on/off · 1–4 tabs · ? keys"
    : ROUTE_HINTS[loc.pathname] ?? "? keys";
  return <>
    {compact && !hintsOff && <div className="keyhints" role="note">
      <span>{hint}</span>
      <button aria-label="Hide key hints" onClick={() => { setHintsOff(true); try { localStorage.setItem(HINTS_OFF, "1"); } catch { /* */ } }}>×</button>
    </div>}
    {sheet && <ShortcutSheet easy={easy} onClose={() => setSheet(false)} />}
  </>;
}

const GROUPS: [string, [string, string][]][] = [
  ["Tonight", [["↑ or +", "A little warmer"], ["↓ or −", "A little cooler"], ["H", "Hot flash"], ["C", "Cold flash"], ["E", "5 more minutes of a flash"],
    ["X", "Stop the flash"], ["O", "Off tonight, or back on"], ["N", "Turn on now (an hour)"], ["P", "Pause for an hour"], ["F then 1 2 3", "How does it feel: too cold, just right, too hot"],
    ["S", "More for tonight (pause, undo, off…)"], ["V", "Whose night: me, both, partner"]]],
  ["Moving around", [["1–4 or T R L M", "Tonight, Rhythm, Sleep, More"], ["J / K", "Next or previous item"], ["Enter", "Open or press"], ["← →", "Previous or next (nights, temperatures)"],
    ["Backspace", "Back, or close"], ["? or hold Sym", "This list"]]],
];

function ShortcutSheet({ onClose, easy }: { onClose: () => void; easy: boolean }) {
  return <Sheet onClose={onClose} role="dialog" aria-label="Keyboard shortcuts">
    <div className="k">Keyboard shortcuts</div>
    <div className="keysheet">
      {GROUPS.filter(([g]) => !(easy && g === "Moving around")).map(([g, rows]) => <section key={g}>
        <h3>{g}</h3>
        <dl>{rows.map(([k, v]) => <div key={k}><dt><kbd>{k}</kbd></dt><dd>{v}</dd></div>)}</dl>
      </section>)}
      {easy && <p className="mute">Tab between Home, Schedule and Me with 1, 2 and 3.</p>}
    </div>
    <button className="item" data-key="enter" onClick={onClose}>Close<small /></button>
  </Sheet>;
}
