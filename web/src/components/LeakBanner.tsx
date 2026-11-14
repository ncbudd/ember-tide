import { useState } from "react";
import { useAuth } from "../auth";
import { useApp } from "../ctx";
import { clock } from "../format";

/**
 * High-visibility calm banner shown on Tonight and Studio home while `snap.leak_hold` is active:
 * explains which sensor stopped the bed, what to check, and lets an owner or sleeper clear the hold.
 */
export default function LeakBanner() {
  const { snap, connected, act } = useApp();
  const auth = useAuth();
  const [busy, setBusy] = useState(false);
  const hold = snap?.leak_hold;
  if (!snap || !hold) return null;

  const canClear = !auth.state.enabled || auth.me?.role !== "viewer";
  const clear = async () => {
    setBusy(true);
    try {
      await act("/api/leak/clear");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="alert"
      style={{
        display: "grid",
        gap: 8,
        padding: "14px 16px",
        borderRadius: 14,
        background: "color-mix(in srgb, var(--bad) 10%, var(--card, var(--bg)))",
        border: "1px solid color-mix(in srgb, var(--bad) 55%, transparent)",
        color: "var(--text)",
      }}
    >
      <div style={{ display: "grid", gap: 4 }}>
        <b style={{ fontWeight: 600 }}>Possible leak: both sides turned off</b>
        <span style={{ fontSize: 13.5, lineHeight: 1.45 }}>
          {hold.sensor} reported water at {clock(hold.since, snap.tz)}.{" "}
          {hold.wet
            ? "It is still reading wet. Check around the mattress cover, hoses and hub for leaks, then dry the sensor before clearing this."
            : "Check around the mattress cover, hoses and hub to make sure everything is dry before turning the bed back on."}
        </span>
      </div>
      {canClear ? (
        <div>
          <button
            type="button"
            className="btn pri"
            disabled={!connected || busy}
            onClick={clear}
          >
            {busy ? "Clearing…" : "I've checked, turn the bed back on"}
          </button>
        </div>
      ) : (
        <span className="mute" style={{ fontSize: 12.5 }}>
          An owner or sleeper needs to check the bed and clear this alert.
        </span>
      )}
    </div>
  );
}
