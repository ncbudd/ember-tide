"""Autopilot: notices when a sleeper looks too warm or too cold, says why, and (only in Act mode) nudges the side.

Design rules
- One decision function, `evaluate()`, used live every minute and for replaying past nights. It only reads data up to
  the moment it's evaluating, so a replay shows exactly what it would have done then.
- Every decision explains itself: each signal reports its value, what it compares against, and how much it pushed.
- Guardrails are enforced here, not in the UI: allowed phases, floor/ceiling, max change per night, max changes,
  gap between changes, quiet time before wake, never during a flash, and a person's own change always wins for a while.
- Suggest mode logs what it would do; Act mode applies it. Both write the same records.

Signals (each scores from −1 = "too cold" to +1 = "too warm", times its weight)
- feedback  : the person's own "too hot / too cold" taps in the last 20 minutes (strongest)
- skin_temp : wearable skin temperature against this night's settled baseline
- movement  : share of the last 10 minutes the bed sensor saw movement / turning over
- hr        : heart rate against this night's settled baseline (only counts while restless)
- flashes   : a hot or cold flash in the last 45 minutes
- pad       : the bed running warmer than plan for a while (can't keep up)
- room      : bedroom temperature / humidity from any room sensor, against this person's usual nights
Restlessness on its own is ambiguous, so it leans on the other signals, or on the person's type (Tide runs warm).
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field

from .wearables import RELEARN_NIGHTS  # noqa: F401  (re-exported)

MOVING = {4, 6, 7}
LABELS = {
    "feedback": "Your too hot / too cold taps",
    "skin_temp": "Skin temperature (wearable)",
    "movement": "Restlessness",
    "hr": "Heart rate",
    "flashes": "Recent flashes",
    "pad": "Bed warmer than plan",
    "room": "Room temperature and humidity",
}
WEIGHTS = {"feedback": 1.2, "skin_temp": 0.9, "movement": 0.5, "hr": 0.5, "flashes": 0.6, "pad": 0.3, "room": 0.35}
THRESHOLD = {"low": 1.4, "medium": 1.0, "high": 0.7}
ALL_SIGNALS = list(LABELS)


@dataclass
class Reason:
    signal: str
    label: str
    value: str
    detail: str
    push: float                       # weighted contribution: + warm, − cold


@dataclass
class Decision:
    ts: float
    person: str
    phase: str | None
    score: float
    threshold: float
    direction: str                    # "cooler" | "warmer" | "none"
    delta_c: float
    reasons: list[Reason] = field(default_factory=list)
    status: str = "watching"          # watching | suggested | applied | blocked
    blocked_by: list[str] = field(default_factory=list)
    target_before: float | None = None
    target_after: float | None = None
    signals: dict = field(default_factory=dict)      # raw numbers, for the timeline

    def to_dict(self) -> dict:
        d = asdict(self)
        d["reasons"] = [asdict(r) for r in self.reasons]
        return d


@dataclass
class Window:
    """Everything evaluate() may look at, already limited to `now` and earlier."""
    now: float
    rows: list[dict]                  # telemetry for this person, oldest first (ts, target, measured, hr, status, phase, occupied)
    in_bed: float | None
    state: str                        # night state at `now`
    phase: str | None
    flash_active: bool
    events: list[dict]                # this person's events (feel, flashes, adjust, autopilot), any order
    skin: list[tuple[float, float]]   # wearable skin temperature samples (°C), oldest first
    persona: str
    wake: float | None
    room: list[tuple[float, float]] = field(default_factory=list)       # room temperature samples (°C)
    humidity: list[tuple[float, float]] = field(default_factory=list)   # room humidity samples (%)
    room_usual: float | None = None                                     # this person's typical bedroom temp on their nights
    wear_hr: list[tuple[float, float]] = field(default_factory=list)    # wearable heart rate (bpm), oldest first
    baseline: dict | None = None                                        # learned from the calibration nights (see baseline())
    pet_windows: list[tuple[float, float]] = field(default_factory=list)   # a pet was on this side: bed readings ignored
    # "active, not sleeping yet" stretches (engine/active.py): nothing from them counts, bed or wearable
    active_windows: list[tuple[float, float]] = field(default_factory=list)
    # the pad can't reach a warm target because the room is cold (engine/padlag.py): a bed below its usual range
    # is the room, not a reason to change the plan
    room_short: bool = False
    # readings only from a watch or ring whose level isn't learned yet (engine/wearables.py): "skin" / "hr" here are
    # never compared with the normal range (they still count within the night)
    unlearned: list[str] = field(default_factory=list)


def _median(xs):
    return statistics.median(xs) if xs else None


def _quiet(w: Window) -> Window:
    """The window without the "active, not sleeping yet" stretches: no bed rows, skin or wearable heart rate from
    them (they'd read as a very restless, hot sleeper)."""
    if not w.active_windows:
        return w
    from dataclasses import replace
    out = lambda t: not any(a <= t <= b for a, b in w.active_windows)  # noqa: E731
    return replace(w, rows=[r for r in w.rows if out(r["ts"])], skin=[x for x in w.skin if out(x[0])],
                   wear_hr=[x for x in w.wear_hr if out(x[0])], active_windows=[])


def signals_at(w: Window) -> dict:
    """The raw numbers. Kept separate so the timeline can plot them every few minutes."""
    w = _quiet(w)
    now = w.now
    # while a pet was on this side, the strip's movement and heart rate are the pet's, not the sleeper's
    rows = [r for r in w.rows if not any(a <= r["ts"] <= b for a, b in w.pet_windows)] if w.pet_windows else w.rows
    last10 = [r for r in rows if r["ts"] >= now - 600]
    occ10 = [r for r in last10 if r.get("status") is not None]
    restless = (sum(1 for r in occ10 if r["status"] in MOVING) / len(occ10)) if len(occ10) >= 4 else None
    settle_from = (w.in_bed or 0) + 1200
    base_hr = [r["hr"] for r in rows if r.get("hr") and settle_from <= r["ts"] < now - 600]
    now_hr = [r["hr"] for r in rows if r.get("hr") and r["ts"] >= now - 300]
    hr_rise = (statistics.mean(now_hr) - _median(base_hr)) if len(base_hr) >= 20 and len(now_hr) >= 3 else None
    wb = [v for t, v in w.wear_hr if settle_from <= t < now - 600]
    wn = [v for t, v in w.wear_hr if t >= now - 300]
    wear_rise = (statistics.mean(wn) - _median(wb)) if len(wb) >= 10 and len(wn) >= 1 else None
    base_skin = [v for t, v in w.skin if settle_from <= t < now - 900]
    now_skin = [v for t, v in w.skin if t >= now - 600]
    skin_delta = (_median(now_skin) - _median(base_skin)) if len(base_skin) >= 5 and len(now_skin) >= 2 else None
    pads = [r["measured"] - r["target"] for r in last10 if r.get("measured") is not None and r.get("target") is not None]
    pad_over = statistics.mean(pads) if len(pads) >= 4 else None
    feel = [e for e in w.events if e["kind"] == "feel" and now - 1200 < e["ts"] <= now]
    feel_last = max(feel, key=lambda e: e["ts"]) if feel else None
    fl = [e for e in w.events if e["kind"] in ("hot_flash", "cold_flash") and now - 2700 <= e["ts"] <= now]
    flash_last = max(fl, key=lambda e: e["ts"])["kind"] if fl else None
    room_now = [v for t, v in w.room if t >= now - 1800]
    hum_now = [v for t, v in w.humidity if t >= now - 1800]
    room_c = statistics.mean(room_now) if room_now else None
    return {"room_c": None if room_c is None else round(room_c, 1),
            "room_vs_usual": None if room_c is None or w.room_usual is None else round(room_c - w.room_usual, 1),
            "humidity": None if not hum_now else round(statistics.mean(hum_now)),
            "restless": None if restless is None else round(restless, 2), "hr_rise": None if hr_rise is None else round(hr_rise, 1),
            "wear_hr_rise": None if wear_rise is None else round(wear_rise, 1),
            "skin_delta": None if skin_delta is None else round(skin_delta, 2), "pad_over": None if pad_over is None else round(pad_over, 2),
            "feel": feel_last["feel"] if feel_last else None, "feel_ts": feel_last["ts"] if feel_last else None, "flash": flash_last}


def merge_hr(bed: float | None, wear: float | None, tb: float, tw: float) -> tuple[float | None, float, str]:
    """Combine the bed's and the wearable's heart-rate rise. Returns (rise, trust multiplier, where it came from).
    When both report and roughly agree, a trust-weighted average; when they disagree by 6 bpm or more, the more
    trusted source alone (a tie goes to the wearable, which measures at the skin)."""
    have = [(v, t, n) for v, t, n in ((bed, tb, "bed"), (wear, tw, "watch")) if v is not None and t > 0]
    if not have:
        return None, 1.0, ""
    if len(have) == 1:
        v, t, n = have[0]
        return v, t, n
    (bv, bt, _), (wv, wt, _) = have
    if abs(bv - wv) >= 6:
        return (bv, bt, "bed; the watch disagrees") if bt > wt else (wv, wt, "watch; the bed disagrees")
    return (bv * bt + wv * wt) / (bt + wt), (bt + wt) / 2, "bed and watch agree"


# The comfort band: this person's normal range, learned from the calibration nights (Autopilot.baseline()["band"]).
# Inside it Autopilot stays quiet: no live nudges, nothing logged, no evidence for suggestions. It acts only when a
# reading has been outside the band for a while (both the last 5 minutes and the 10 minutes before), or when the
# person says so themselves (a too hot / too cold tap, a flash).
BAND_PAD = {"bed_c": 0.4, "hr": 3.0, "skin": 0.25, "restless": 0.05}     # tolerance around the learned spread
def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[int(q * (len(xs) - 1))]


def band_check(w: Window, sig: dict) -> dict | None:
    """Which readings are outside this person's normal range in a sustained way. None = no band learned yet."""
    band = (w.baseline or {}).get("band")
    if not band:
        return None
    w = _quiet(w)
    now = w.now
    rows = [r for r in w.rows if not any(a <= r["ts"] <= b for a, b in w.pet_windows)] if w.pet_windows else w.rows

    def two(vals: list[tuple[float, float]]):
        recent = [v for t, v in vals if now - 300 <= t <= now]
        before = [v for t, v in vals if now - 900 <= t < now - 300]
        return (_median(recent), _median(before)) if recent and before else (None, None)
    series = {
        "bed_c": [(r["ts"], r["measured"]) for r in rows if r.get("measured") is not None],
        "hr": [(r["ts"], r["hr"]) for r in rows if r.get("hr")] or ([] if "hr" in w.unlearned else list(w.wear_hr)),
        "skin": [] if "skin" in w.unlearned else list(w.skin),
    }
    out, checked = [], []
    for k, vals in series.items():
        lo_hi = band.get(k)
        if not lo_hi:
            continue
        a, b = two(vals)
        if a is None:
            continue
        if k == "bed_c" and w.room_short:
            continue
        lo, hi = lo_hi[0] - BAND_PAD[k], lo_hi[1] + BAND_PAD[k]
        checked.append(k)
        if a > hi and b > hi:
            out.append({"what": k, "side": "high", "value": round(a, 1)})
        elif a < lo and b < lo:
            out.append({"what": k, "side": "low", "value": round(a, 1)})
    if band.get("restless") is not None and sig.get("restless") is not None:
        prev = [r for r in rows if now - 1200 <= r["ts"] < now - 600 and r.get("status") is not None]
        rb = (sum(1 for r in prev if r["status"] in MOVING) / len(prev)) if len(prev) >= 4 else None
        checked.append("restless")
        lim = band["restless"] + BAND_PAD["restless"]
        if sig["restless"] > lim and rb is not None and rb > lim:
            out.append({"what": "restless", "side": "high", "value": sig["restless"]})
    return {"out": out, "checked": checked}


def evaluate(w: Window, policy, last_target: float | None, history: list[dict]) -> Decision:
    """Decide for one person at one moment. `history` = tonight's earlier autopilot records (for the limits)."""
    sens = getattr(policy, "sensitivity", "medium")
    thr = THRESHOLD.get(sens, 1.0)
    use = set(getattr(policy, "signals", None) or ALL_SIGNALS)
    trust = getattr(policy, "trust", None) or {}
    tb, tw, tr = (float(trust.get(k, 1.0)) for k in ("bed", "wearable", "room"))
    from .compare import PREFER
    pb, pw = PREFER.get(getattr(policy, "trust_more", "balanced"), (1.0, 1.0))
    tb, tw = tb * pb, tw * pw
    source_trust = {"movement": tb, "pad": tb, "skin_temp": tw, "room": tr}
    if "bed_hr" in use:
        use.add("hr")                                   # older settings name
    sig = signals_at(w)
    reasons: list[Reason] = []

    def add(name, push, value, detail, mult=None):
        m = source_trust.get(name, 1.0) if mult is None else mult       # how much you trust where this came from
        if name in use and push and m > 0:
            reasons.append(Reason(name, LABELS[name], value, detail, round(push * WEIGHTS[name] * m, 2)))

    used = sig.get("feel_ts") is not None and any(h["status"] in ("applied", "suggested") and h["ts"] >= sig["feel_ts"] for h in history)
    if sig["feel"] in ("too_hot", "too_cold") and not used:          # one tap leads to at most one change
        add("feedback", 1 if sig["feel"] == "too_hot" else -1, "too hot" if sig["feel"] == "too_hot" else "too cold", "tapped in the last 20 min")
    bc = band_check(w, sig)
    sd = sig["skin_delta"]
    if sd is not None and abs(sd) >= 0.3:
        add("skin_temp", max(-1.0, min(1.0, sd / 0.6)), f"{sd:+.1f} °C", "against tonight's settled skin temperature")
    else:
        # warm (or cool) from the start, so nothing changed against tonight: compare with your learned normal instead
        sk = next((x for x in (bc or {}).get("out", []) if x["what"] == "skin"), None)
        if sk:
            lo, hi = w.baseline["band"]["skin"]
            off = sk["value"] - (hi + BAND_PAD["skin"]) if sk["side"] == "high" else sk["value"] - (lo - BAND_PAD["skin"])
            off += 0.3 if off > 0 else -0.3
            add("skin_temp", max(-1.0, min(1.0, off / 0.6)), f"{sk['value']:.1f} °C",
                f"{'above' if off > 0 else 'below'} your normal skin temperature ({lo:.1f}–{hi:.1f} °C)")
    if sig["flash"]:
        add("flashes", 1 if sig["flash"] == "hot_flash" else -1, sig["flash"].replace("_", " "), "in the last 45 min")
    po = sig["pad_over"]
    if po is not None and po >= 1.0:
        add("pad", min(1.0, po / 2), f"{po:+.1f} °C", "bed above plan for 10 min")
    rv, rc, hu = sig["room_vs_usual"], sig["room_c"], sig["humidity"]
    if rv is not None and abs(rv) >= 1.5:
        add("room", max(-1.0, min(1.0, rv / 3)), f"{rc:.1f} °C ({rv:+.1f})", "bedroom against your usual nights")
    elif rc is not None and (rc >= 25 or rc <= 17):
        # outside a comfortable bedroom range, however usual it has become (a hot week mustn't look normal)
        push = min(1.5, (rc - 24) / 1.5) if rc >= 25 else -min(1.5, (18 - rc) / 1.5)
        add("room", push, f"{rc:.1f} °C", "the bedroom is warmer than is comfortable for sleep" if rc >= 25
            else "the bedroom is colder than is comfortable for sleep")
    if hu is not None and hu >= 65 and (rc or 0) >= 22:
        add("room", 0.4, f"{hu}% humidity", "humid and warm makes it harder to shed heat")
    lean = sum(r.push for r in reasons)
    rs = sig["restless"]
    bl = w.baseline or {}
    # Learned from the calibration week: a naturally restless sleeper needs more movement before it means anything.
    # ...unless the room is outside the comfortable range: then those "usual" nights may have been uncomfortable too
    room_off = rc is not None and (rc >= 25 or rc <= 17)
    r_thr = 0.3 if room_off else max(0.3, min(0.7, (bl.get("restless_p90") or 0) + 0.05))
    if rs is not None and rs >= r_thr and tb > 0:
        direction = 1 if lean > 0 else -1 if lean < 0 else (-1 if w.persona == "ember" else 1)
        why = "and the other signals point the same way" if lean else f"and {'Ember runs cold' if w.persona == 'ember' else 'you tend to run warm'}"
        add("movement", direction * min(1.0, rs / (r_thr * 2)), f"{round(rs * 100)}% of 10 min", f"moving or turning over, {why}")
        hr, mult, src = merge_hr(sig["hr_rise"], sig.get("wear_hr_rise"), tb, tw)
        if hr is not None and hr >= 5:
            add("hr", direction * min(1.0, hr / 12), f"+{hr:.0f} bpm", f"above tonight's settled heart rate ({src}), while restless", mult)
    score = round(sum(r.push for r in reasons), 2)
    tap_r = next((r for r in reasons if r.signal == "feedback"), None)
    if tap_r and any(r.push * tap_r.push < 0 for r in reasons):
        # your own word sets the direction: signals that disagree are noted but set aside, never a veto
        score = round(sum(r.push for r in reasons if r.push * tap_r.push > 0), 2)
        tap_r.detail += " (other signals disagree; going with you)"
    d = Decision(w.now, "", w.phase, score, thr, "none", 0.0, reasons, signals=sig, target_before=last_target)
    if bc is not None:
        sig["outside_band"] = [x["what"] for x in bc["out"]]
    own = any(r.signal in ("feedback", "flashes") for r in reasons)
    if bc is not None and not bc["out"] and not own:
        # inside this person's normal range: nothing to act on, however the signals add up
        d.blocked_by.append("inside your normal range: staying quiet")
        return d
    if abs(score) < thr:
        return d
    # independent evidence: restlessness and the heart-rate rise that comes with it are one body signal, not two
    groups = {("body" if r.signal in ("movement", "hr") else r.signal) for r in reasons if r.push * score > 0}
    if len(groups) < 2 and not tap_r:
        d.blocked_by.append("only one kind of evidence; waiting for a second to agree")
        d.status = "watching"
        return d
    step = 0.56 if abs(score) < 2 * thr else 1.11            # 1 °F, or 2 °F when the evidence is strong
    d.direction = "cooler" if score > 0 else "warmer"
    d.delta_c = -step if score > 0 else step

    # ---- guardrails ----
    b = d.blocked_by
    if w.state != "rhythm" or not w.in_bed:
        b.append("only while the night is running and someone is in bed")
    if w.phase not in (policy.phases or []):
        b.append(f"not allowed to change the {w.phase or 'current'} phase")
    if w.flash_active:
        b.append("a flash is running")
    mine = [e for e in w.events if e["kind"] in ("adjust", "adjust_cleared", "hot_flash", "cold_flash") and e.get("source") != "autopilot"
            and w.now - getattr(policy, "respect_manual_min", 30) * 60 <= e["ts"] <= w.now]
    if mine:
        b.append(f"you changed it yourself in the last {getattr(policy, 'respect_manual_min', 30)} min")
    if w.wake and w.now >= w.wake - policy.quiet_before_wake_min * 60:
        b.append(f"quiet time before wake ({policy.quiet_before_wake_min} min)")
    acted = [h for h in history if h["status"] in ("applied", "suggested")]
    if acted and w.now - max(h["ts"] for h in acted) < policy.min_gap_min * 60:
        b.append(f"less than {policy.min_gap_min} min since the last change")
    if len(acted) >= policy.max_actions:
        b.append(f"already {len(acted)} changes tonight (limit {policy.max_actions})")
    total = sum(h["delta_c"] for h in acted)
    if abs(total + d.delta_c) > policy.max_delta_c + 0.01:
        b.append(f"would go past the ±{policy.max_delta_c:.1f} °C limit for tonight")
    if last_target is not None:
        new = last_target + d.delta_c
        if new < policy.floor_c - 0.01 or new > policy.ceiling_c + 0.01:
            b.append("would go outside your never-below / never-above limits")
        d.target_after = round(new, 2)
    if b:
        d.status = "blocked"
    else:
        d.status = "applied" if policy.mode == "act" else "suggested"
    return d


class Autopilot:
    """Runs evaluate() every minute for each person, logs signals and decisions, applies them in Act mode."""

    EVERY_S = 60
    SIGNAL_LOG_S = 300

    def __init__(self, controller) -> None:
        self.c = controller
        self._last: dict[str, float] = {}
        self._last_log: dict[str, float] = {}
        self._last_logged_decision: dict[str, float] = {}

    def window(self, p, now: float, since: float | None = None) -> Window:
        e = self.c.engine_for(p.id)
        n = e.night
        start = since if since is not None else now - 4 * 3600
        rows = self.c.store.history(p.zone, start, now, person=p.id)
        evs = [x for x in self.c.store.events(since=now - 4 * 3600, limit=2000, person=p.id) if x["ts"] <= now and x.get("person") == p.id]
        f = e.s.flash
        room, hum = self.room(now - 4 * 3600, now)
        skin, skin_new = self.wear_info(p, "skin_temp", now - 6 * 3600, now)
        whr, hr_new = self.wear_info(p, "hr", now - 6 * 3600, now)
        return Window(now=now, rows=rows, in_bed=e.s.in_bed, state=e.s.state, phase=e.s.phase_id, room=room, humidity=hum,
                      room_usual=self.room_usual(p, now),
                      flash_active=bool(f and now < f.end + f.ease_s), events=evs, skin=skin,
                      pet_windows=self.pet_windows(p, now - 6 * 3600, now),
                      active_windows=self.active_windows(p, now - 6 * 3600, now),
                      room_short=bool(getattr(self.c, "room_note", lambda _p: None)(p.id)),
                      persona=p.persona, wake=n.wake if n else None, wear_hr=whr,
                      unlearned=[k for k, x in (("skin", skin_new), ("hr", hr_new)) if x], baseline=self.baseline(p, now))

    # ---------- calibration ----------
    def _nights(self, p, now: float) -> list[dict]:
        """Finished nights with at least 3 hours in bed that started near the usual bedtime (cached per hour)."""
        from .summary import recent
        hour = int(now // 3600)
        cache = self.__dict__.setdefault("_nights_cache", {})
        if p.id not in cache or cache[p.id][0] != hour:
            since = p.settings.autopilot.learn_since or 0          # "Start over: recalibrate" forgets the nights before
            ns = [n for n in recent(self.c.store, p, self.c.clock.tz, now, 45)
                  if n.get("has_data") and not n.get("in_progress") and (n.get("in_bed_min") or 0) >= 180
                  and (n.get("in_bed_at") or 0) >= since]
            # nights they weren't in their side of the bed (the morning check, or away for a while: engine/away.py)
            from .away import away_periods, excluded_nights
            gone, periods = excluded_nights(self.c.store, p.id), away_periods(self.c.store, p.id, now)
            ns = [n for n in ns if n.get("date") not in gone
                  and not any(x <= (n.get("in_bed_at") or 0) <= y for x, y in periods)]
            ns = self._usual_hours(ns)
            # a night the ring says ran a degree or more warmer than normal (illness, alcohol) teaches nothing
            from .ring import unusual
            ns = [n for n in ns if not (n.get("in_bed_at") and n.get("out_at") and unusual(self.c.store, p.id, n["in_bed_at"], n["out_at"]))]
            # and a night the rhythm mostly didn't run (off tonight, paused, run by hand) isn't this person's normal
            ns = [n for n in ns if self._rhythm_share(p, n) >= 0.6]
            cache[p.id] = (hour, ns)
        return cache[p.id][1]

    LATE_S = 2 * 3600

    def _usual_hours(self, nights: list[dict]) -> list[dict]:
        """Leave out nights that started 2 h or more later than this person usually gets in (a 3 AM night isn't
        their normal). "Usually" = the median in-bed clock time of these nights, so a rhythm whose bedtime is set
        differently from real life doesn't throw everything out."""
        from datetime import datetime
        tz = self.c.clock.tz

        def eve(t: float) -> float:                     # minutes after 18:00 (a 1 AM start is 420)
            d = datetime.fromtimestamp(t, tz)
            return ((d.hour - 18) % 24) * 60 + d.minute
        starts = [eve(n["in_bed_at"]) for n in nights if n.get("in_bed_at")]
        if len(starts) < 3:
            return [n for n in nights if not n.get("late_by_min")]     # too few to know "usual": the rhythm's bedtime
        usual = statistics.median(starts)
        return [n for n in nights if not n.get("in_bed_at") or eve(n["in_bed_at"]) - usual < self.LATE_S / 60]

    def learning(self, p, now: float) -> dict:
        """The calibration week: Autopilot watches and learns this person's normal before it suggests anything."""
        pol = p.settings.autopilot
        have = len(self._nights(p, now))
        need = pol.learn_nights
        done = have >= need or pol.learn_skip
        from .away import is_away
        paused = (not done) and is_away(self.c.store, p.id, now)    # away for a while: the week waits for them
        return {"need": need, "have": min(have, need), "done": done, "skipped": bool(pol.learn_skip and have < need),
                "paused": paused}

    def baseline(self, p, now: float) -> dict | None:
        """This person's normal, from their recent finished nights: how restless, settled heart rate, skin temperature."""
        hour = int(now // 3600)
        cache = self.__dict__.setdefault("_base_cache", {})
        if p.id in cache and cache[p.id][0] == hour:
            return cache[p.id][1]
        nights = self._nights(p, now)[:14]
        windows, hrs, brs, skins, beds, felt = [], [], [], [], [], 0
        for n in nights:
            if not n.get("in_bed_at") or not n.get("out_at"):
                continue
            a, b = n["in_bed_at"] + 1200, n["out_at"]
            # only real sleep teaches: the person in bed with their rhythm running. Not the empty bed, not after the
            # pad switched off, not awake-and-moving stretches, not the pet (detected, or marked in Fix this night)
            act = self.active_windows(p, n["in_bed_at"], b) + self.pet_windows(p, n["in_bed_at"], b) + self._fix_pet(p, n)
            rows = [r for r in self.c.store.history(p.zone, a, b, step_s=15, person=p.id) if r.get("status") is not None
                    and r.get("occupied") == 1 and r.get("target") is not None
                    and not any(x <= r["ts"] <= y for x, y in act)]
            for i in range(0, len(rows), 40):                   # roughly 10-minute windows at 15 s rows
                chunk = rows[i:i + 40]
                if len(chunk) >= 20:
                    windows.append(sum(1 for r in chunk if r["status"] in MOVING) / len(chunk))
            hrs += [r["hr"] for r in rows if r.get("hr")]
            brs += [r["br"] for r in rows if r.get("br")]
            beds += [r["measured"] for r in rows if r.get("measured") is not None]
            sk, new = self.wear_info(p, "skin_temp", a, b)
            if not new:                                         # a device still being learned teaches nothing yet
                skins += [v for t, v in sk if not any(x <= t <= y for x, y in act)]
            fb = (n.get("feedback") or {}).get("temp")
            taps = [e for e in self.c.store.events_between(n["in_bed_at"], b, person=p.id)
                    if e["kind"] == "feel" and e.get("feel") in ("too_hot", "too_cold")]
            felt += 1 if (fb in ("warm", "cold") or taps) else 0
        out = None
        if len(nights) >= 3 and windows:
            ws = sorted(windows)
            out = {"nights": len(nights), "restless_p90": round(ws[int(0.9 * (len(ws) - 1))], 2),
                   "restless_median": round(statistics.median(ws), 2),
                   "hr": round(statistics.median(hrs), 1) if hrs else None,
                   "skin": round(statistics.median(skins), 2) if skins else None}
            # the normal range: the middle 80% of each reading over these nights, plus how often a night felt off
            band = {"restless": out["restless_p90"], "feel_rate": round(felt / len(nights), 2)}
            for k, xs in (("bed_c", beds), ("hr", hrs), ("br", brs), ("skin", skins)):
                if len(xs) >= 50:
                    band[k] = [round(_pct(xs, 0.1), 2), round(_pct(xs, 0.9), 2)]
            out["band"] = band
        cache[p.id] = (hour, out)
        return out

    def _rhythm_share(self, p, n: dict) -> float:
        """Of the minutes this person was in bed that night, the share with their rhythm running (a target set)."""
        if not n.get("in_bed_at") or not n.get("out_at"):
            return 1.0
        rows = [r for r in self.c.store.history(p.zone, n["in_bed_at"], n["out_at"], step_s=60, person=p.id) if r.get("occupied") == 1]
        if len(rows) < 30:
            return 1.0                                   # too little to judge: leave it to the other checks
        return sum(1 for r in rows if r.get("target") is not None) / len(rows)

    def _fix_pet(self, p, n: dict) -> list[tuple[float, float]]:
        """Stretches the person marked "that was the pet" in Fix this night."""
        from .compare import get_fix
        fix = get_fix(self.c.store, p.id, n.get("date") or "") or {}
        return [(x["start"], x["end"]) for x in fix.get("stretches") or [] if x.get("who") == "pet"]

    def active_windows(self, p, since: float, until: float) -> list[tuple[float, float]]:
        """When the people in bed were awake and moving, not sleeping yet (engine/active.py)."""
        from .active import windows
        evs = [e for e in self.c.store.events(since=since - 3 * 3600, limit=500) if e["kind"] in ("active_started", "active_ended")]
        return windows(evs, p.zone, until)

    def pet_windows(self, p, since: float, until: float) -> list[tuple[float, float]]:
        """When a pet was on this person's side (from the non-personal pet events)."""
        if not getattr(self.c.household, "pets", None):
            return []
        from .pets import pet_windows
        evs = [e for e in self.c.store.events(since=since, limit=500) if e["kind"] in ("pet_suspected", "pet_cleared")]
        return pet_windows(evs, p.zone, until)

    def wear_hr(self, p, since: float, until: float, level: bool = True) -> list[tuple[float, float]]:
        """Wearable heart rate from every device for this person, as one series on one level (engine/wearables.py).
        level=False: the raw readings (for comparing a device with the bed)."""
        return self.wear_info(p, "hr", since, until, level)[0]

    def wear_info(self, p, metric: str, since: float, until: float, level: bool = True) -> tuple[list, bool]:
        """(points, unlearned) for "skin_temp" or "hr". unlearned = only from a device whose level isn't known yet."""
        from . import wearables as W
        series = W.device_series(self.c, p, metric, since, until)
        if not level:
            return sorted({(round(t), round(v, 1)) for x in W._dedupe(series).values() for t, v in x}), False
        return W.merge(series, self.levels(p, None).get(metric))

    def levels(self, p, now: float | None) -> dict:
        """Each watch or ring's level over the learning nights (cached with those nights). now=None: whichever
        learning nights were worked out last (so a replay or baseline at another hour doesn't thrash that cache)."""
        from . import wearables as W
        known = self.__dict__.get("_nights_cache", {}).get(p.id)
        nights = known[1] if (now is None and known) else self._nights(p, self.c.clock.now_ts() if now is None else now)
        cache = self.__dict__.setdefault("_lvl_cache", {})
        if p.id not in cache or cache[p.id][0] is not nights:
            cache[p.id] = (nights, W.levels(self.c, p, nights[:14]))
        return cache[p.id][1]

    def wearable_status(self, p, now: float) -> list[dict]:
        """Watches and rings seen lately: learning a new one, lined up, or gone quiet (with a plain sentence)."""
        from . import wearables as W
        return W.status(self.c, p, self.levels(p, now), now, self.learning(p, now)["done"])

    def room(self, since: float, until: float) -> tuple[list, list]:
        t, h = [], []
        mgr = self.c.sources
        for cfg in (mgr.configs if mgr else []):
            if not cfg.enabled:
                continue
            if cfg.role == "room_temp":
                t += self.c.store.samples(cfg.id, since, until)
            elif cfg.role == "room_humidity":
                h += self.c.store.samples(cfg.id, since, until)
        return sorted(t), sorted(h)

    def room_usual(self, p, now: float) -> float | None:
        """Median bedroom temperature over this person's in-bed hours of the last 14 nights (cached per hour)."""
        hour = int(now // 3600)
        cache = self.__dict__.setdefault("_room_cache", {})
        if p.id in cache and cache[p.id][0] == hour:
            return cache[p.id][1]
        vals = []
        temps, _ = self.room(now - 14 * 86400, now - 6 * 3600)
        if temps:
            rows = self.c.store.history(p.zone, now - 14 * 86400, now - 6 * 3600, step_s=600, person=p.id)
            occupied = [r["ts"] for r in rows if r.get("occupied") == 1]
            j = 0
            for ts, v in temps:
                while j < len(occupied) and occupied[j] < ts - 600:
                    j += 1
                if j < len(occupied) and abs(occupied[j] - ts) <= 600:
                    vals.append(v)
        out = statistics.median(vals) if len(vals) >= 30 else None
        cache[p.id] = (hour, out)
        return out

    def skin(self, p, since: float, until: float) -> list[tuple[float, float]]:
        """Skin temperature from every wearable for this person, as one series. Devices read at different levels
        (a ring at 35.6 °C, a watch at 34.0 °C, a relative import on a nominal 34), so each is put onto the level of
        the device the normal range was learned on (engine/wearables.py): changes WITHIN a device survive, the jump
        when coverage switches devices (or a new device's different level) doesn't become a fake change."""
        return self.wear_info(p, "skin_temp", since, until)[0]

    def unlearned(self, p, since: float, until: float) -> list[str]:
        out = []
        for metric, key in (("skin_temp", "skin"), ("hr", "hr")):
            if self.wear_info(p, metric, since, until)[1]:
                out.append(key)
        return out

    def tonight(self, pid: str, now: float) -> list[dict]:
        e = self.c.engine_for(pid)
        start = (e.night.precool if e.night else now - 12 * 3600)
        return [x for x in self.c.store.events(since=start, limit=500, person=pid) if x["kind"] == "autopilot" and x.get("person") == pid]

    def tick(self, now: float) -> None:
        for p in self.c.household.people:
            if now - self._last.get(p.id, 0) < self.EVERY_S and now >= self._last.get(p.id, 0):
                continue
            self._last[p.id] = now
            try:
                self._tick_person(p, now)
            except Exception:  # noqa: BLE001  (one person's trouble never stops the other's)
                import logging
                logging.getLogger(__name__).exception("autopilot failed for %s", p.id)

    def _tick_person(self, p, now: float) -> None:
        pol = p.settings.autopilot
        e = self.c.engine_for(p.id)
        if e.s.state == "done" and pol.ai_review and e.night and getattr(self.c, "review_hook", None):
            key = f"ai_review_started:{p.id}:{e.night.date.isoformat()}"
            if not self.c.store.get(key):
                self.c.store.put(key, now)
                self.c.review_hook(p, e.night.date.isoformat())      # runs in the background; result kept for the page
        if pol.mode == "off":
            return
        if e.s.state not in ("rhythm", "out_of_bed_hold") or not e.s.in_bed:
            return
        w = self.window(p, now, since=e.s.in_bed - 600)
        d = evaluate(w, pol, e.s.target_c, self.tonight(p.id, now))
        d.person = p.id
        if getattr(self.c, "active_since", None) is not None and not any(r.signal == "feedback" for r in d.reasons):
            return                                    # awake and moving, not sleeping yet: only a tap counts
        lr = self.learning(p, now)
        if not lr["done"] and d.status in ("suggested", "applied"):
            d.status = "learning"                       # calibration week: note it, change nothing, suggest nothing
            d.blocked_by.append(f"still learning your nights ({lr['have']} of {lr['need']})")
        if now - self._last_log.get(p.id, 0) >= self.SIGNAL_LOG_S or now < self._last_log.get(p.id, 0):
            self._last_log[p.id] = now
            for k, v in (("score", d.score), *[(k, v) for k, v in d.signals.items() if isinstance(v, (int, float))]):
                if v is not None:
                    self.c.store.sample(now, f"ap:{p.id}:{k}", float(v))
        if d.direction == "none":
            return
        # record a candidate at most once per gap, so a "blocked" or "suggested" doesn't repeat every minute
        if now - self._last_logged_decision.get(p.id, -1e18) < pol.min_gap_min * 60 and d.status != "applied":
            return
        self._last_logged_decision[p.id] = now
        data = d.to_dict()
        data.pop("ts")
        data.pop("person")
        self.c.store.event(now, "autopilot", p.id, "autopilot", **data)
        if d.status == "applied":
            e.adjust(now, delta_c=d.delta_c, until="next_phase", source="autopilot")

    def replay(self, p, start: float, end: float, policy, step_s: float = 60, wake: float | None = None) -> list[dict]:
        """What it would have done over a past stretch, with these settings. Reads only; writes nothing."""
        rows_all = self.c.store.history(p.zone, start - 600, end, person=p.id)
        evs_all = [x for x in self.c.store.events(since=start - 4 * 3600, limit=5000, person=p.id) if x.get("person") == p.id and x["ts"] <= end]
        skin_all = self.skin(p, start - 6 * 3600, end)
        whr_all = self.wear_hr(p, start - 6 * 3600, end)
        pets_all = self.pet_windows(p, start - 6 * 3600, end)
        act_all = self.active_windows(p, start - 6 * 3600, end)
        new_all = self.unlearned(p, start - 6 * 3600, end)
        base = self.baseline(p, start)
        room_all, hum_all = self.room(start - 3600, end)
        usual = self.room_usual(p, start)
        started = [x for x in evs_all if x["kind"] == "rhythm_started"]
        in_bed = min((x.get("in_bed") or x["ts"]) for x in started) if started else start
        again = [x["in_bed"] for x in evs_all if x["kind"] == "rhythm_restarted" and x.get("in_bed") and start - 4 * 3600 <= x["in_bed"] <= end]
        if again:
            in_bed = max(again)                       # given up early and started again: the night is from the new start
        out: list[dict] = []
        hist: list[dict] = []
        last_logged = -1e18
        t = max(start, in_bed + 600)
        while t <= end:
            rows = [r for r in rows_all if r["ts"] <= t]
            if not rows:
                t += step_s
                continue
            cur = rows[-1]
            occupied = cur.get("occupied") == 1
            flashes = [x for x in evs_all if x["kind"] in ("hot_flash", "cold_flash") and x["ts"] <= t]
            fa = bool(flashes) and t - max(x["ts"] for x in flashes) < (max(flashes, key=lambda x: x["ts"]).get("minutes") or 15) * 60 + 300
            w = Window(now=t, rows=[r for r in rows if r["ts"] >= t - 4 * 3600], in_bed=in_bed,
                       state="rhythm" if (cur.get("target") is not None and occupied) else "other",
                       phase=cur.get("phase"), flash_active=fa, events=[x for x in evs_all if x["ts"] <= t and x["kind"] != "autopilot"],
                       skin=[s for s in skin_all if s[0] <= t], persona=p.persona, wake=wake,
                       room=[s for s in room_all if t - 4 * 3600 <= s[0] <= t], humidity=[s for s in hum_all if t - 4 * 3600 <= s[0] <= t],
                       room_usual=usual, wear_hr=[s for s in whr_all if s[0] <= t], baseline=base,
                       pet_windows=[x for x in pets_all if x[0] <= t],
                       active_windows=[(a, min(b, t)) for a, b in act_all if a <= t], unlearned=new_all)
            d = evaluate(w, policy, cur.get("target"), hist)
            d.person = p.id
            if d.direction != "none" and (t - last_logged >= policy.min_gap_min * 60):
                last_logged = t
                rec = d.to_dict()
                out.append(rec)
                if d.status in ("applied", "suggested"):
                    hist.append(rec)
            t += step_s
        return out
