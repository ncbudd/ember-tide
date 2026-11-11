import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { isApp } from "./api";
import { useAuth } from "./auth";
import { nativeTakeLink } from "./native";

/** The phone screens a notification can open. */
const PHONE = new Set(["/", "/rhythm", "/sleep", "/settings"]);
/** The Studio pages a notification can open (owners only); anything else in Studio opens its front page. */
const STUDIO = new Set(["devices", "notifications", "integrations", "data", "hub", "access", "autopilot", "me", "nights", "insights", "rhythm", "help"]);
/** What a Studio page is for someone who isn't an owner. */
const PHONE_FOR_STUDIO: Record<string, string> = { hub: "/settings#hub", devices: "/", notifications: "/settings", integrations: "/settings", data: "/settings" };

/** Where a link from a notification really goes: a known screen (with its #anchor), else the front page. */
export function resolveLink(raw: string, isOwner: boolean): string {
  if (!raw.startsWith("/") || raw.startsWith("//") || /[\\\u0000-\u001f\u007f:]/.test(raw)) return "/";
  const [rest, ...h] = raw.split("#");
  const anchor = h.join("#");
  const path = rest.split("?")[0].replace(/\/+$/, "") || "/";
  const tail = /^[A-Za-z0-9_-]{1,40}$/.test(anchor) ? `#${anchor}` : "";
  if (PHONE.has(path)) return path + tail;
  if (path === "/studio" || path.startsWith("/studio/")) {
    const page = path.slice("/studio/".length);
    if (!isOwner) return PHONE_FOR_STUDIO[page] ?? "/settings";
    return path === "/studio" || STUDIO.has(page) ? path + tail : "/studio";
  }
  return "/";
}

// Picked up as soon as the page loads, kept until someone is signed in (the sign-in screens come first) and App opens it.
let waiting: string | null = null;
async function collect() {
  if (!isApp()) return;
  try { const r = await nativeTakeLink(); if (r.link) { waiting = r.link; window.dispatchEvent(new CustomEvent("et-link-ready")); } } catch { /* older app build */ }
}
if (typeof window !== "undefined") {
  void collect();
  window.addEventListener("et-link", () => void collect());
}

/** Open the screen a tapped notification points at (inside the signed-in app). */
export function useNotificationLink() {
  const nav = useNavigate();
  const auth = useAuth();
  const owner = auth.isOwner || !auth.state.enabled;      // with sign-in off everyone counts as one
  useEffect(() => {
    const go = () => { if (waiting) { const to = resolveLink(waiting, owner); waiting = null; nav(to); } };
    go();                                               // a link that arrived before sign-in finished
    window.addEventListener("et-link-ready", go);
    return () => window.removeEventListener("et-link-ready", go);
  }, [owner, nav]);
}
