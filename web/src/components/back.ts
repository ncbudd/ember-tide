/**
 * Android's back button: the newest open sheet (or other overlay) closes first; with nothing open it goes back to
 * Tonight, and from Tonight it leaves the app the usual way. In a browser this does nothing.
 */
const stack: (() => void)[] = [];

export function pushBack(close: () => void): () => void {
  stack.push(close);
  return () => { const i = stack.lastIndexOf(close); if (i >= 0) stack.splice(i, 1); };
}

/** Returns true when it handled the press (closed something or moved), false when the app should go to the background. */
export function handleBack(): boolean {
  const top = stack.pop();
  if (top) { top(); return true; }
  if (location.pathname !== "/" && !location.pathname.startsWith("/join")) {
    window.history.length > 1 ? window.history.back() : location.replace("/");
    return true;
  }
  return false;
}
