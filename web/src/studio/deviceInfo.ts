/**
 * Plain-language explanations for Studio → Devices: what Ember & Tide uses each kind of device for, how to
 * calibrate it, and what to add next. Kept in one place so the add flow, the device rows and Help agree.
 */

export interface Purpose { uses: string; votes: boolean; note?: string }

/** What each device type is used for. `votes` = takes part in the in-bed vote for its side. */
export const PURPOSE: Record<string, Purpose> = {
  radar: { votes: true, uses: "Votes on whether this side is in bed, so the night starts and stops at the right moment. It sees people lying still." },
  motion: { votes: true, uses: "A weak hint for the in-bed vote: it only sees movement, so it mostly helps notice someone getting in or up." },
  scale: { votes: true, uses: "The most reliable in-bed vote, and it can tell the two of you apart by weight." },
  pad: { votes: true, uses: "Votes on whether this side is in bed (a pressure strip or contact pad under the mattress)." },
  room: { votes: false, uses: "Autopilot's room signal: a warm or muggy bedroom counts as a reason to cool, and the tuning guide compares warm nights with the room." },
  wearable: { votes: false, uses: "Autopilot's body signals: skin temperature and heart rate during the night. It doesn't vote on in bed." },
  light: { votes: false, uses: "When you really went to bed (lights off) and gets-up in the night (light on). Feeds the tuning guide's bedtime advice and the night explorer's Context lane.",
           note: "It doesn't vote on in bed and doesn't change temperatures by itself. Nothing else to set up: after a few nights the tuning guide compares lights-off with your bedtime." },
  leak: { votes: false, uses: "Emergency off: stops pumping water to both sides immediately and alerts everyone at home when water is detected." },
  phone: { votes: false, uses: "Home or away: while away, that side's pre-condition waits so it doesn't run for nobody. A phone that just goes quiet counts as unknown, never away." },
  location: { votes: false, uses: "Away, or on the way home: pre-condition starts so the bed is ready when you arrive." },
  calendar: { votes: false, uses: "Evening events (the start-on-schedule fallback waits until you're home) and early starts tomorrow (Tonight offers an earlier wake)." },
  custom: { votes: false, uses: "Whatever you set it to measure: presence or weight vote on in bed; room readings feed Autopilot; others are recorded for the explorer." },
};

/** Short step-by-step calibration, per device type. */
export const HOWTO: Record<string, string[]> = {
  radar: ["Lie still on your side for a few minutes: it should say \"someone there\" most of the time.",
          "If it drops out while you're still, raise \"Keep counting as occupied for\" (try 60–90 s).",
          "If someone leaning over the bed sets it off, raise \"Only count once detected for\"."],
  motion: ["Motion only says someone moved recently. Keep its vote low (0.1–0.2) and its hold long (5–10 min)."],
  scale: ["With that side empty, press Zero.",
          "Enter each person's body weight under Body weight (or import it from Health Connect).",
          "Keep \"Counts as occupied above\" well over pets and pillows (20 kg, about 45 lb, suits most homes)."],
  pad: ["Lie on your side: it should say \"someone there\" within its settle time.",
        "If it reads the wrong way round, change what on and off mean under \"How it's read\"."],
  room: ["Put a trusted thermometer next to it for an hour.",
         "If this sensor reads 0.8° high, enter −0.8 as the calibration offset (in the sensor's own unit)."],
  wearable: ["Nothing to calibrate for heart rate. For skin temperature, an offset only matters if you compare two devices."],
  light: ["Switch the light on and off: the state here should follow within a few seconds.",
          "If \"on\" shows as off, change what on and off mean under \"How it's read\"."],
  leak: ["Test the sensor with a damp cloth or its test button: it should show \"wet\" within seconds and stop the bed.",
         "Dry the sensor, check for water and clear the alert on Tonight or Studio.",
         "If wet shows as dry, change what readings mean under \"How it's read\"."],
  phone: ["Leave home with the phone: after \"Count as away after\" minutes it should say not on home Wi-Fi.",
          "If a sleeping phone looks away at night, raise that number (30–45 min)."],
  location: ["Check the distance reads close to 0 at home. If it wobbles, raise \"Counts as home within\".",
             "\"Have the bed ready\" sets how early pre-condition starts on the way back (30 min suits most beds)."],
  calendar: ["Add an evening event to that calendar: within about 15 minutes it should show \"event until …\".",
             "Set the usual travel time home after an event."],
  custom: ["Set what the reading means (scale/offset), and for presence or weight how it compares with the threshold."],
};

/** One-line explanation of what changing a tuning value does, keyed by field. Shown under the field's own help. */
export const EFFECT: Record<string, string> = {
  weight: "Higher: this device decides more of the in-bed vote. Lower: it only nudges.",
  hold_s: "Longer: fewer false \"empty\" moments, but it takes longer to notice you got up.",
  min_on_s: "Longer: ignores brief visits (a pet, leaning over), but notices you a little later.",
  threshold_kg: "Higher: harder to trigger by pets or a laundry basket; too high and a light sleeper won't count.",
  hysteresis_kg: "Bigger: steadier while you shift around near the threshold.",
  invert: "Use this when \"on\" or \"occupied\" shows the wrong way round.",
  offset: "Added to every reading to correct a sensor that reads high or low.",
  away_after_min: "Longer: a sleeping phone won't look away; shorter: pre-condition pauses sooner after you leave.",
  home_radius_km: "Bigger: GPS wobble at home won't look like leaving.",
  ready_min: "Bigger: pre-condition starts earlier on the way home.",
  speed_kmh: "Only used when the drive speed can't be measured.",
  travel_min: "How long after an event ends you'd usually be home.",
  ignore_health: "On: never set aside, even when it floods, jumps or freezes. Use only for a device you know is fine.",
};
