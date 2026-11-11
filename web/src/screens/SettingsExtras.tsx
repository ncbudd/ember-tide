import { useState } from "react";
import { api, isApp } from "../api";
import Fold from "../components/Fold";
import WorkDays, { workDaysText } from "../components/WorkDays";
import { isIOS, webPushSupport } from "../pwa";
import { BRANDS } from "../wearables";
import OuraConnect from "./Oura";
import WearablePicker from "./WearablePicker";
import WebNotifications, { IphoneLimits } from "./WebNotifications";

/** Settings sections added with first-time setup, kept here so Settings.tsx only gains a line each. */

export function SettingsWorkDays({ value, onChange }: { value: number[]; onChange: (v: number[]) => void }) {
  return (
    <Fold id="settings.workdays" title="Work days" summary={workDaysText(value)}>
      <div className="card">
        <WorkDays value={value} onChange={onChange} />
        <p className="faint" style={{ fontSize: 12.5, marginTop: 8 }}>Your work-night and day-off rhythms follow these days. Set their times on the Rhythm tab.</p>
      </div>
    </Fold>
  );
}

/** Browser / iPhone Home Screen app: Web Push. (The Android app has its own native section.) */
export function SettingsWebPush({ person }: { person: string }) {
  const sup = webPushSupport();
  const [on, setOn] = useState(false);
  return (
    <Fold id="settings.webpush" title={isIOS() ? "Notifications on this iPhone" : "Notifications in this browser"}
      summary={sup === "install" ? "Add to Home Screen first" : sup === "ok" ? (on ? "On" : "Off") : "Not available"} attention={false}>
      <div className="card"><WebNotifications person={person} onState={setOn} /></div>
    </Fold>
  );
}

export function SettingsWearable({ person, value, onChange }: { person: string; value: string; onChange: (v: string) => void }) {
  const name = BRANDS.find(b => b.id === value)?.name;
  if (isApp()) {
    // the Android app has Health Connect above; Oura's direct connection is the fallback
    return (
      <Fold id="settings.oura" title="Oura ring, direct" summary="Through Oura's cloud">
        <div className="card">
          <p className="mute" style={{ fontSize: 13 }}>Only needed if Health Connect doesn't bring your ring's temperature or HRV. Readings that arrive both ways are counted once.</p>
          <OuraConnect person={person} />
        </div>
      </Fold>
    );
  }
  return (
    <Fold id="settings.wearable" anchor="watch" title="Watch or ring" summary={name ?? "None picked"}>
      <div className="card" style={{ display: "grid", gap: 12 }}>
        <WearablePicker person={person} value={value} onChange={onChange} />
        {isIOS() && <IphoneLimits />}
      </div>
    </Fold>
  );
}

export function SetupAgain({ person }: { person: string }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="card list">
      <button type="button" className="row rowbtn" disabled={busy} onClick={async () => {
        setBusy(true);
        try { await api.patch(`/api/people/${person}/settings`, { welcomed: false, setup_steps: [] }); } finally { setBusy(false); }
      }}>
        <span>Run first-time setup again<br /><span className="mute" style={{ fontSize: 12 }}>The same short questions. Your answers so far are kept as the starting point.</span></span>
        <span className="mute">›</span>
      </button>
    </div>
  );
}
