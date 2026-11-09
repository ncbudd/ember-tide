# Menstrual cycle: plan (not built yet)

Why it matters for a temperature-controlled bed: after ovulation (the luteal phase, roughly the last two weeks of
a cycle) core and skin temperature sit about 0.3–0.5 °C higher, and many people sleep hotter and more restlessly
in the days before a period. A bed that knows the phase can run that side a little cooler then, and Autopilot can
stop reading those warm nights as "you've changed" or "the plan is wrong".

## Where the data can come from

| Source | What it gives | Status |
|---|---|---|
| Oura API v2 | **No cycle data.** Checked against Oura's OpenAPI spec v1.43 (Nov 2026): no cycle, period or ovulation endpoint. | — |
| Oura temperature | `temperature_deviation` (nightly, vs the person's normal) and `temperature_trend_deviation` (3-day trend), already stored as `temp_dev` / `temp_trend`. The trend alone shows the biphasic pattern after a couple of cycles. | **Stored now**, shown as the month chart on the ring card |
| Apple Health (iPhone app) | `HKCategoryTypeIdentifierMenstrualFlow` (period days, flow), `IntermenstrualBleeding`, `OvulationTestResult`, `HKQuantityTypeIdentifierBasalBodyTemperature`, wrist temperature (`appleSleepingWristTemperature`) from Apple Watch. Cycle predictions themselves aren't exposed. | Planned with the iPhone app (IOS-PLAN.md) |
| Health Connect (Android app) | `MenstruationPeriodRecord`, `MenstruationFlowRecord`, `OvulationTestRecord`, `BasalBodyTemperatureRecord`. | Possible in the Android app; read permission per type |
| By hand | "My period started today" from the phone (one tap), as a fallback for anyone without the above. | Planned |

## What we'd store

Only what's needed, on the household's own server: period start dates (and end dates if known), and the derived
phase per night (`follicular` / `luteal` / `period` / `unknown`). No flow detail, symptoms or notes. Off by default,
switched on by the person themselves (never by the owner for someone else), visible only to that person (not the
partner, not the shared Studio pages), and deletable in one tap.

## How it would be used

1. **Phase per night**: from period starts (cycle day) when known; otherwise from the temperature trend (a sustained
   rise of ~0.2 °C+ for 3+ nights marks the luteal phase; the drop marks the next period).
2. **Autopilot**: keeps a separate "normal" for luteal nights, so a warm luteal week isn't flagged as unusual and
   doesn't drag the normal range for the rest of the month. Suggestions can be phase-aware ("in the week before
   your period you've run warm 4 of 5 nights: 1° cooler for those nights?").
3. **A gentle, opt-in pre-adjust**: run the person's side about 1 °F / 0.5 °C cooler during predicted luteal nights,
   within Autopilot's usual limits, explained in the morning report.
4. **Plain words**: the ring card and morning report can say "Warmer than your normal; you're in the second half of
   your cycle, when that's expected" instead of a bare number.

## Order

1. Done: store temperature vs normal and its trend; show a month of it; leave ≥ 1 °C spike nights out of learning.
2. Next: one-tap "period started" in the phone app + a private per-person setting; phase per night from that.
3. Then: Apple Health / Health Connect period data; temperature-only phase detection as the fallback.
4. Then: phase-aware Autopilot normal range and suggestions; the optional luteal pre-adjust.
