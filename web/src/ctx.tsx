import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, useLive } from "./api";
import { useAuth } from "./auth";
import { nativeSignedIn, nativeSnapshot } from "./native";
import type { PersonView, Snapshot } from "./types";

interface Ctx {
  snap: Snapshot | null;
  connected: boolean;
  meId: string | null;
  setMe: (id: string) => void;
  me: PersonView | undefined;
  partner: PersonView | undefined;
  act: (path: string, body?: unknown) => Promise<void>;
  put: (path: string, body: unknown) => Promise<void>;
  toast: string | null;
  say: (msg: string) => void;
}
const C = createContext<Ctx | null>(null);

export function Provider({ children }: { children: ReactNode }) {
  const { snap, connected, apply } = useLive();
  const auth = useAuth();
  // Signed in as someone with a side: that's "me". The owner (or an open install) can still pick, e.g. ?me=p2 to try the partner's view.
  const locked = auth.state.enabled && auth.me?.role !== "owner" && !!auth.me?.person;
  const [meId, setMeId] = useState<string | null>(() => {
    if (locked) return auth.me!.person;
    const q = new URLSearchParams(location.search).get("me");
    try { if (q) localStorage.setItem("et-me", q); return q ?? (auth.state.enabled ? auth.me?.person ?? null : null) ?? localStorage.getItem("et-me"); } catch { return q ?? auth.me?.person ?? null; }
  });
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<number | undefined>(undefined);
  const say = (m: string) => { setToast(m); window.clearTimeout(toastTimer.current); toastTimer.current = window.setTimeout(() => setToast(null), 2600); };
  const setMe = (id: string) => { if (locked) return; setMeId(id); try { localStorage.setItem("et-me", id); } catch { /* private mode */ } };
  const me = snap?.people.find(p => p.id === meId);
  useEffect(() => { nativeSignedIn(auth.me?.person ?? meId); }, [auth.me?.person, meId]);
  useEffect(() => { if (snap) nativeSnapshot(snap, auth.me?.person ?? meId); }, [snap, auth.me?.person, meId]);
  const partner = snap?.people.find(p => p.id !== meId);
  const act = async (path: string, body?: unknown) => {
    try { apply(await api.post<Snapshot>(path, body)); } catch (e) { say(`Couldn't do that: ${(e as Error).message}`); }
  };
  const put = async (path: string, body: unknown) => {
    try { apply(await api.put<Snapshot>(path, body)); say("Saved"); } catch (e) { say(`Couldn't save: ${(e as Error).message}`); }
  };
  return <C.Provider value={{ snap, connected, meId, setMe, me, partner, act, put, toast, say }}>{children}</C.Provider>;
}
export function useApp(): Ctx {
  const c = useContext(C);
  if (!c) throw new Error("no provider");
  return c;
}
