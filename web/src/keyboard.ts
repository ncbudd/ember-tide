/**
 * Hardware keyboards (the Minimal Phone 2's backlit QWERTY, a Bluetooth keyboard on a tablet): when one is
 * present the app switches to a compact keyboard layout (no bottom tab bar, a one-line key-hint bar) and every
 * main action has a key. Detection, in order:
 *  1. the person's choice on this device (Settings: Auto / On / Off), kept in localStorage;
 *  2. the Android app's report (MainActivity → `et-keyboard`, Configuration.keyboard == QWERTY and not hidden);
 *  3. in a browser: a physical key press (a real key, not the on-screen keyboard), or a short near-square screen
 *     like the Minimal Phone 2's (~360×413).
 * Launcher shortcuts and command-bar text ("hot flash", "cooler", "off tonight") arrive as `et-action` and are
 * parsed here into the same actions the keys use.
 */
import { useEffect, useState } from "react";
import { isApp } from "./api";
import { nativeKeyboard, nativeTakeAction } from "./native";

export type CompactPref = "auto" | "on" | "off";
const PREF = "et-compact";


export function compactPref(): CompactPref {
  try { const v = localStorage.getItem(PREF); return v === "on" || v === "off" ? v : "auto"; } catch { return "auto"; }
}

export function setCompactPref(v: CompactPref) {
  try { v === "auto" ? localStorage.removeItem(PREF) : localStorage.setItem(PREF, v); } catch { /* private mode */ }
  window.dispatchEvent(new Event("et-compact-pref"));
}

/** A small near-square screen like the Minimal Phone 2 (3.9", ~360×413). */
function shortSquare(w = window.innerWidth, h = window.innerHeight): boolean {
  return h <= 460 && w <= 480 && w / h >= 0.75 && w / h <= 1.25;
}

let sawPhysicalKey = false;
const NAV_KEYS = /^(Arrow(Up|Down|Left|Right)|Escape|Enter|Tab|[a-z0-9?+\-])$/i;

/** Is a hardware keyboard in use? Updates live (keyboard attached, slid away, setting changed). */
export function useHardwareKeyboard(): boolean {
  const [native, setNative] = useState<boolean | null>(() => {
    const k = (window as unknown as { __etKeyboard?: { hardware: boolean } }).__etKeyboard;
    return k ? k.hardware : null;
  });
  const [pref, setPref] = useState<CompactPref>(compactPref);
  const [typed, setTyped] = useState(sawPhysicalKey);
  const [square, setSquare] = useState(() => shortSquare());
  useEffect(() => {
    const onKb = (e: Event) => setNative(!!(e as CustomEvent<{ hardware: boolean }>).detail?.hardware);
    const onPref = () => setPref(compactPref());
    const onResize = () => setSquare(shortSquare());
    const onKey = (e: KeyboardEvent) => {
      // the on-screen keyboard in an Android WebView reports key "Unidentified" / keyCode 229; real keys don't
      // and an on-screen keyboard only shows while a text field has focus: keys typed into a field never count
      // (Return on a phone's on-screen keyboard looks like a real Enter otherwise)
      const t = e.target as HTMLElement | null;
      const inField = !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
      if (!sawPhysicalKey && !inField && !e.isComposing && e.keyCode !== 229 && e.key !== "Unidentified" && NAV_KEYS.test(e.key)) {
        sawPhysicalKey = true;
        setTyped(true);
      }
    };
    window.addEventListener("et-keyboard", onKb);
    window.addEventListener("et-compact-pref", onPref);
    window.addEventListener("resize", onResize);
    window.addEventListener("keydown", onKey, true);
    if (isApp()) nativeKeyboard().then(r => setNative(!!r.hardware)).catch(() => { /* older app build */ });
    return () => {
      window.removeEventListener("et-keyboard", onKb);
      window.removeEventListener("et-compact-pref", onPref);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("keydown", onKey, true);
    };
  }, []);
  if (pref !== "auto") return pref === "on";
  if (native !== null && isApp()) return native || square;
  return typed || square;
}

/** Plain-language commands from a launcher or the share sheet → an action id. */
export type ActionId = "hot" | "cold" | "extend" | "stop" | "warmer" | "cooler" | "off" | "on" | "pause" | "simple" | "tonight";

function parseCommand(text: string): ActionId | null {
  const t = text.toLowerCase().replace(/['’]/g, "").replace(/[^a-z0-9+\- ]/g, " ").replace(/\s+/g, " ").trim();
  if (!t) return null;
  // whole words/phrases only ("tonight" must not count as "on")
  const words = ` ${t} `;
  const has = (...w: string[]) => w.some(x => words.includes(` ${x} `));
  if (has("stop flash", "stop", "thats enough", "enough")) return "stop";
  if (has("5 more", "five more", "extend", "+5")) return "extend";
  if (has("cold flash", "im cold", "i am cold", "warm me")) return "cold";
  if (t === "hot" || t === "cold") return t as ActionId;
  if (has("hot flash", "im hot", "i am hot", "cool me")) return "hot";
  if (has("warmer", "warm up", "hotter", "+")) return "warmer";
  if (has("cooler", "cool down", "colder", "-")) return "cooler";
  if (has("off tonight", "turn off", "off")) return "off";
  if (has("back on", "turn on", "resume", "on")) return "on";
  if (has("pause")) return "pause";
  if (has("simple", "night screen")) return "simple";
  if (has("tonight", "open")) return "tonight";
  return null;
}

/** Launcher shortcuts / commands handed over by the Android app, once each. */
export function useLauncherActions(run: (a: ActionId, raw: string) => void) {
  useEffect(() => {
    if (!isApp()) return;
    const take = () => nativeTakeAction().then(r => {
      if (!r.action) return;
      // "ask:" = it came from another app or a link (share sheet, embertide://): confirm anything that changes the bed
      const ask = r.action.startsWith("ask:");
      const text = ask ? r.action.slice(4) : r.action;
      const a = parseCommand(text);
      if (!a) return;
      if (ask && !["tonight", "simple"].includes(a) && !window.confirm(`Do "${text}" on the bed?`)) return;
      run(a, text);
    }).catch(() => { /* older app build */ });
    void take();                                    // a shortcut that started the app
    window.addEventListener("et-action", take);     // a shortcut while it's open
    return () => window.removeEventListener("et-action", take);
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps
}

/** True when a key press belongs to a text field (never steal those keys). */
export function typingInto(e: KeyboardEvent): boolean {
  const el = (e.target as HTMLElement | null) ?? null;
  if (!el) return false;
  const tag = el.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable) return true;
  if (tag === "INPUT") {
    const type = (el as HTMLInputElement).type;
    return !["checkbox", "radio", "button", "submit", "range"].includes(type);
  }
  return false;
}
