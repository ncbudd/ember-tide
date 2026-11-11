"""Signs that someone who got into bed early is now going to sleep (engine/night.py waits for one before the in-bed
phases after "Fall asleep" start counting).

The earliest of these, each only after they got in (a sign from before, such as bedtime mode switched on by a
schedule while the bed was empty, never counts):
- "bedtime_mode": their phone's bedtime mode came on (the phone app's charging/bedtime share);
- "charger": their phone went on the charger by their own bedside dial (the same "phone by the bed" evidence the
  in-bed vote uses: phone_context.bedside_side);
- "settled": the bed itself shows them settling: still (no movement in at least 90% of the readings) for the last
  30 minutes, with the heart rate in each 5 minutes of the last 15 at least 4 bpm below where it was just before that still
  stretch began. Reading quietly is still too, but the heart rate holds; falling asleep brings it down. Judged no
  earlier than 30 minutes after getting in, and never while "active, not sleeping yet" (engine/active.py);
- "watch": a watch or ring sending sleep stages in real time says asleep (a recent segment that began after getting in);
- "lights_off": a bedroom light (Studio → Devices) went off while they were in bed.
The usual bedtime is the backstop (night.py). Nothing here starts a night or ends one.
"""

from __future__ import annotations

from statistics import median

STILL_S = 30 * 60            # still for this long
STILL_SHARE = 0.9            # no movement in this share of the readings
HR_NOW_S = 15 * 60           # the heart rate now: the last 15 minutes (it has to stay down that long)
HR_BEFORE_S = 15 * 60        # against the 15 minutes before the still stretch (from getting in, at the earliest)
HR_DROP = 4.0                # at least this much lower
MIN_ROWS = 12                # readings in the still stretch, with no hole over 10 min (the bed writes one every 15 s)
MIN_HR = 5                   # heart-rate readings in each of the two stretches compared
EVERY_S = 60                 # the bed check is worked out at most once a minute
WATCH_RECENT_S = 15 * 60     # a sleep segment this recent counts as "now"

WHY_TEXT = {"bedtime_mode": "your phone's bedtime mode came on", "charger": "your phone went on the charger by the bed",
            "settled": "you settled", "watch": "your watch said you were asleep", "lights_off": "the lights went off",
            "bedtime": "it was your usual bedtime"}


def phone_sign(store, p, in_bed: float, now: float) -> tuple[float, str] | None:
    from . import phone_context as PC
    cur = PC.latest(store, p.id)
    if not cur:
        return None
    out = []
    bs = cur.get("bedtime_since")
    if bs and PC.fresh(cur, "bedtime_mode", "bedtime", now) is True and in_bed <= bs <= now:
        out.append((float(bs), "bedtime_mode"))
    cs = cur.get("charging_since")
    if (cs and PC.fresh(cur, "charging", "charging", now) is True and in_bed <= cs <= now
            and PC.bedside_side(store, cur, now) == p.zone):
        out.append((float(cs), "charger"))
    return min(out) if out else None


def watch_sign(store, p, in_bed: float, now: float) -> tuple[float, str] | None:
    best = None
    for src, d in store.get_stages(p.id, in_bed, now + 1).items():
        if src == "orion":
            continue                                   # the bed's own estimate is the "settled" check
        for g in d.get("segments") or []:
            if (g.get("stage") not in (None, "awake") and g["start"] >= in_bed and g["end"] >= now - WATCH_RECENT_S
                    and g["start"] <= now):
                best = min(best or g["start"], g["start"])
    return (float(best), "watch") if best is not None else None


def settled(rows: list[dict], in_bed: float, now: float) -> bool:
    """The bed check (see the module notes). `rows`: this person's telemetry from getting in until now."""
    if now - in_bed < STILL_S:
        return False
    still = [r for r in rows if r["ts"] >= now - STILL_S]
    seen = [r for r in still if r.get("occupied") == 1 and r.get("status") is not None]
    if len(seen) < MIN_ROWS or len(seen) < 0.8 * len(still) or seen[0]["ts"] > now - STILL_S + 300:
        return False                                   # not in bed the whole time, or the strip wasn't reading
    if any(b["ts"] - a["ts"] > 600 for a, b in zip(seen, seen[1:])) or now - seen[-1]["ts"] > 300:
        return False                                   # a hole in the readings: can't say they were still throughout
    if sum(1 for r in seen if r["status"] == 0) / len(seen) < STILL_SHARE:
        return False
    hr_now = [r["hr"] for r in seen if r["ts"] >= now - HR_NOW_S and r.get("hr")]
    a = max(in_bed, now - STILL_S - HR_BEFORE_S)
    hr_before = [r["hr"] for r in rows if a <= r["ts"] < now - STILL_S and r.get("hr") and r.get("occupied") == 1]
    if len(hr_now) < MIN_HR or len(hr_before) < MIN_HR:
        return False
    # down in each third of those 15 minutes, not just on average (a few minutes of it isn't "settled")
    lim = median(hr_before) - HR_DROP
    for k in range(3):
        part = [r["hr"] for r in seen if now - HR_NOW_S + k * HR_NOW_S / 3 <= r["ts"] < now - HR_NOW_S + (k + 1) * HR_NOW_S / 3 + 1
                and r.get("hr")]
        if not part or median(part) > lim:
            return False
    return True


class SleepSigns:
    """Per controller: works out tonight's earliest sign for each person waiting for one."""

    def __init__(self) -> None:
        self.bed: dict[str, tuple[float, float | None]] = {}      # pid → (checked at, settled at)
        self.lights: dict[str, bool | None] = {}                    # pid → the bedroom light last time
        self.lights_off: dict[str, float] = {}                      # pid → when it went off

    def sign(self, c, p, e, now: float, lights_on: bool | None) -> tuple[float, str] | None:
        prev = self.lights.get(p.id)
        self.lights[p.id] = lights_on
        if prev is True and lights_on is False:
            self.lights_off[p.id] = now
        s = e.s
        if not (s.started and s.in_bed is not None and s.sleep_at is None and e.waiting_for_sleep()):
            self.bed.pop(p.id, None)
            if s.sleep_at is not None or not s.started:
                self.lights_off.pop(p.id, None)
            return None
        cands = []
        ps = phone_sign(c.store, p, s.in_bed, now)
        if ps:
            cands.append(ps)
        ws = watch_sign(c.store, p, s.in_bed, now)
        if ws:
            cands.append(ws)
        lo = self.lights_off.get(p.id)
        if lo is not None and lo >= s.in_bed:
            cands.append((lo, "lights_off"))
        checked, at = self.bed.get(p.id, (-1e18, None))
        if at is None and (now - checked >= EVERY_S or now < checked):
            rows = c.store.history(p.zone, max(s.in_bed, now - STILL_S - HR_BEFORE_S - 60), now, person=p.id)
            if getattr(c, "active_since", None) is None and settled(rows, s.in_bed, now):
                at = now
            self.bed[p.id] = (now, at)
        if at is not None:
            cands.append((at, "settled"))
        return min(cands) if cands else None
