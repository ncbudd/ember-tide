"""Night summaries for the Sleep screen: what a person actually wants to know the next morning,
computed from 15-second telemetry and the event log. Sleep is *estimated* from the bed sensors and labeled so."""

from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from ..models import Person
from ..store import Store
from .active import TEXT as ACTIVE_TEXT, inside, windows as active_windows
from .rhythm import pick_rhythm, resolve
from ..units import clock_style, fmt_clock

ROW_S = 15.0
LATE_S = 2 * 3600          # in bed this long after the usual bedtime: a late night


def night_window(person: Person, d: date, tz: ZoneInfo, now: float) -> tuple[float, float] | None:
    rh = pick_rhythm(person.rhythms, d)
    if not rh:
        return None
    n = resolve(rh, d, tz)
    start = n.precool - 3600
    end = max(n.end, n.wake + 3 * 3600)      # allow lie-ins
    return start, min(end, now)


PET_KINDS = ("pet_suspected", "pet_cleared")
ACTIVE_KINDS = ("active_started", "active_ended")


def _pet_label(e: dict, zone: str, pets: list[dict]) -> str | None:
    """The pet's name when only one pet on this side could have been it (same kind; for a heart-rate reading, any
    fast-hearted pet), otherwise None ("a pet"). Pets as stored in the household (dicts)."""
    here = [p for p in pets if p.get("sleeps_on_bed") != "no" and p.get("side", "either") in ("either", "both", zone)]
    named = next((p for p in here if p.get("name") == e.get("pet")), None)
    if not named:
        return None
    if e.get("evidence") == "heart" or "heart rate" in (e.get("why") or ""):
        same = [p for p in here if p.get("kind") == "cat" or float(p.get("weight_kg") or 99) < 12]
    else:
        same = [p for p in here if p.get("kind") == named.get("kind")]
    return named["name"] if len(same) == 1 else None


def pet_moments(events: list[dict], zone: str, pets: list[dict], until: float) -> list[dict]:
    """Pet appear/leave events on one side (stored without a person) as moments for that side's sleeper:
    "Looked like Biscuit on your side, ignored"."""
    out: list[dict] = []
    open_: dict | None = None
    for e in sorted((x for x in events if x.get("kind") in PET_KINDS and x.get("zone") == zone), key=lambda x: x["ts"]):
        if e["kind"] == "pet_suspected" and open_ is None:
            name = _pet_label(e, zone, pets)
            who = name or "a pet"
            # the story belongs to whoever sleeps on this side, so "your side" (no left/right: beds name sides differently)
            text = (f"Looked like {who} joined you" if e.get("with_person")
                    else f"Looked like {who} on your side, ignored")
            open_ = {"kind": "pet", "ts": e["ts"], "pet": name, "zone": zone, "with_person": bool(e.get("with_person")),
                     "ignored": True, "text": text, "source": "pets"}
            out.append(open_)
        elif e["kind"] == "pet_cleared" and open_ is not None:
            open_["end"] = e["ts"]
            open_["minutes"] = max(1, round((e["ts"] - open_["ts"]) / 60))
            open_ = None
    if open_ is not None:
        open_["minutes"] = max(1, round((until - open_["ts"]) / 60)) if until > open_["ts"] else None
    return out


def sleep_text(why: str | None, at: float, tz: ZoneInfo, clock: str = "24") -> str:
    """The story line for when an early night moved on from "Fall asleep"."""
    clk = fmt_clock(at, clock, tz)
    return {"settled": f"You settled around {clk}, so the night moved on",
            "bedtime_mode": f"Your phone's bedtime mode came on around {clk}, so the night moved on",
            "charger": f"Your phone went on the charger by the bed around {clk}, so the night moved on",
            "watch": f"Your watch said you were asleep around {clk}, so the night moved on",
            "lights_off": f"The lights went off around {clk}, so the night moved on",
            }.get(why or "", f"In bed early: the night moved on at your usual bedtime, {clk}")


def _moments(events: list[dict], tz: ZoneInfo, phase_names: dict[str, str] | None = None,
             zone: str | None = None, pets: list[dict] | None = None, until: float | None = None,
             clock: str = "24") -> list[dict]:
    """Collapse the raw log into a short story: flashes, grouped adjustments, starts, skips, and (with a zone) pets
    seen on that side."""
    out: list[dict] = []
    evs = sorted(events, key=lambda e: e["ts"])
    if zone:
        pm = pet_moments(evs, zone, pets or [], until if until is not None else (evs[-1]["ts"] if evs else 0))
        evs = [e for e in evs if e["kind"] not in PET_KINDS]
    else:
        pm = []
    burst: dict | None = None
    # the night was given up early and started again (engine/night.py): the start that was given up isn't the
    # night's start; the story says when it started again and since when they'd been up
    restart = max((e for e in evs if e["kind"] == "rhythm_restarted" and e.get("in_bed")), key=lambda e: e["ts"], default=None)
    for e in evs:
        k = e["kind"]
        if restart is not None:
            if k == "rhythm_started" and (e.get("restarted") or e["ts"] < restart["ts"]):
                continue
            if k == "rhythm_restarted" and e is not restart:
                continue
        if k == "rhythm_restarted":
            up = e.get("up_since")
            clk = fmt_clock(up, clock, tz) if up else None
            out.append({"kind": k, "ts": e["in_bed"], "source": e.get("source"), "in_bed": e["in_bed"],
                        "was_in_bed": e.get("was_in_bed"), "up_since": up,
                        "text": f"Started again from the beginning (you'd been up since {clk})" if clk
                        else "Started again from the beginning"})
            continue
        if k == "adjust":
            if burst and e["ts"] - burst["end"] <= 600 and burst.get("phase") == e.get("phase"):
                burst["end"] = e["ts"]
                burst["count"] += 1
                burst["delta_c"] += e.get("delta_c") or 0
                if e.get("temp_c") is not None:
                    burst["temp_c"] = e["temp_c"]
            else:
                burst = {"kind": "adjust", "ts": e["ts"], "end": e["ts"], "count": 1, "delta_c": e.get("delta_c") or 0,
                         "temp_c": e.get("temp_c"), "source": e.get("source"), "phase": e.get("phase"),
                         "phase_name": (phase_names or {}).get(e.get("phase") or "")}
                out.append(burst)
            continue
        if k in ("hot_flash", "cold_flash"):
            out.append({"kind": k, "ts": e["ts"], "minutes": e.get("minutes"), "extended": 0, "source": e.get("source")})
        elif k == "flash_extended":
            last = next((m for m in reversed(out) if m["kind"] in ("hot_flash", "cold_flash")), None)
            if last:
                last["extended"] += 1
        elif k == "flash_stopped":
            last = next((m for m in reversed(out) if m["kind"] in ("hot_flash", "cold_flash")), None)
            if last:
                last["stopped_at"] = e["ts"]
        elif k == "rhythm_changed" and out and out[-1]["kind"] == k and e["ts"] - out[-1].get("end", out[-1]["ts"]) <= 900:
            out[-1]["count"] = out[-1].get("count", 1) + 1
            out[-1]["end"] = e["ts"]
        elif k == "rhythm_started" and any(m["kind"] == k for m in out):
            out.append({"kind": "rhythm_restarted", "ts": e["ts"], "source": e.get("source")})
        elif k == "home_changed" and e.get("home") is not None:
            m = {"kind": "home_changed", "ts": e["ts"], "home": e.get("home"), "why": e.get("why"), "source": e.get("source")}
            prev = next((x for x in reversed(out) if x["kind"] == "home_changed"), None)
            if prev and prev["home"] == m["home"]:
                continue                                  # no change worth telling
            if prev and e["ts"] - prev["ts"] <= 600:
                out.remove(prev)                          # a flicker (phone napping, GPS jitter) cancels out
                continue
            out.append(m)
        elif k == "rhythm_started":
            m = {"kind": k, "ts": e["ts"], "source": e.get("source")}
            if e.get("fitted"):
                m["fitted"] = True                        # a late start: the rhythm was squeezed to fit before the wake
                m["text"] = "Later than usual: the rhythm was fitted into the time before your warm wake"
            out.append(m)
        elif k == "sleep_anchor" and e.get("at"):
            # in bed early: when (and why) the night moved on from "Fall asleep" (engine/night.py, engine/sleepsign.py)
            if restart is not None and e["ts"] < restart["ts"]:
                continue                                  # belonged to the start that was given up
            out.append({"kind": "sleep", "ts": e["at"], "why": e.get("why"), "source": "system",
                        "text": sleep_text(e.get("why"), e["at"], tz, clock)})
        elif k == "room_cold":
            if not any(m["kind"] == k and m.get("text") == e.get("text") for m in out):
                out.append({"kind": k, "ts": e["ts"], "text": e.get("text"), "phase": e.get("phase"), "source": "system"})
        elif k in ("tonight_skip", "tonight_end", "tonight_resume", "tonight_start_now", "tonight_pause", "rhythm_changed", "adjust_cleared"):
            out.append({"kind": k, "ts": e["ts"], "source": e.get("source")})
    if pm:
        out = sorted(out + pm, key=lambda m: m["ts"])
    return out


def household_pets(store: Store) -> list[dict]:
    return list((store.get("household") or {}).get("pets") or [])


_CACHE: dict[tuple, dict] = {}


def _store_token(store) -> str:
    """A key that's unique per store for its whole life (id() can be reused once a store is freed: tests make many)."""
    t = getattr(store, "_cache_token", None)
    if t is None:
        import uuid
        t = store._cache_token = uuid.uuid4().hex
    return t
_CACHE_MAX = 600


def summarize(store: Store, person: Person, d: date, tz: ZoneInfo, now: float, series: bool = False,
              raw: bool = False) -> dict | None:
    """One night. The person's own corrections ("Fix this night", engine/compare.py) are laid over the bed's numbers
    unless raw=True."""
    out = _summarize_cached(store, person, d, tz, now, series)
    if out is not None and not raw:
        from .compare import apply_fix, get_fix
        apply_fix(out, get_fix(store, person.id, d.isoformat()))
    return out


def _summarize_cached(store: Store, person: Person, d: date, tz: ZoneInfo, now: float, series: bool) -> dict | None:
    win = night_window(person, d, tz, now)
    if not win:
        return None
    start, end = win
    key = None
    if not series and end < now:
        # a finished night only changes if something is recorded inside it (an import, a late check-in) or its rhythm
        # changes: cache on a fingerprint of exactly that
        rh = pick_rhythm(person.rhythms, d)
        key = (_store_token(store), person.id, person.zone, d.isoformat(), str(tz), rh.model_dump_json() if rh else None,
               json.dumps(store.get(f"feedback:{person.id}:{d.isoformat()}"), sort_keys=True),
               store.window_version(person.zone, person.id, start, end),
               json.dumps(household_pets(store), sort_keys=True), getattr(person.settings, "up_after_min", None))
        hit = _CACHE.get(key)
        if hit is not None:
            return copy.deepcopy(hit)
    out = _summarize(store, person, d, tz, now, series, start, end)
    if key is not None and out is not None and not out.get("in_progress"):
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = copy.deepcopy(out)
    return out


def _lying_in(events: list[dict]) -> bool:
    """Past the usual end but still in bed: a lie-in started and the night isn't done yet."""
    last = max((e["ts"] for e in events if e["kind"] == "lie_in_started"), default=None)
    return last is not None and not any(e["kind"] == "night_done" and e["ts"] >= last for e in events)


def _summarize(store: Store, person: Person, d: date, tz: ZoneInfo, now: float, series: bool, start: float, end: float) -> dict | None:
    rows = store.history(person.zone, start, end, person=person.id)
    # the person's own events, plus pets seen on their side (stored without a person)
    allev = store.events_between(start - 3 * 3600, end, person=person.id)
    events = [e for e in allev if e["ts"] >= start and (e.get("person") == person.id
              or (e["kind"] in PET_KINDS and e.get("zone") == person.zone)
              or (e["kind"] in ACTIVE_KINDS and person.zone in (e.get("zones") or [])))]
    # awake and moving, not sleeping yet (engine/active.py): counted as time before sleep, not restless sleep
    act = [(max(a, start), b) for a, b in active_windows([e for e in allev if e["kind"] in ACTIVE_KINDS], person.zone, end)
           if b >= start]
    pets = household_pets(store)
    fb = store.get(f"feedback:{person.id}:{d.isoformat()}")
    rh = pick_rhythm(person.rhythms, d)
    usual = resolve(rh, d, tz) if rh else None
    full_end = usual.end if usual else 0
    usual_bed = usual.bedtime if usual else None
    base = {"date": d.isoformat(), "label": datetime(d.year, d.month, d.day).strftime("%a, %b ") + str(d.day),
            "in_progress": now < full_end or _lying_in(events), "feedback": fb,
            # the night before a work day or a day off, by this person's own work days (someone may work weekends)
            "before": "work" if (d.weekday() + 1) % 7 in person.settings.work_days else "off"}
    if not rows:
        return {**base, "has_data": False, "moments": _moments(events, tz, zone=person.zone, pets=pets, until=end,
                                                                   clock=clock_style(person.settings))}

    occ = [r for r in rows if r["occupied"] == 1]
    # each row stands for the time until the next row (capped), so sparse or dense sampling both add up right
    dur = {}
    for i, r in enumerate(rows):
        nxt = rows[i + 1]["ts"] if i + 1 < len(rows) else r["ts"] + ROW_S
        dur[id(r)] = max(0.0, min(nxt - r["ts"], 600.0))
    # in bed = stretches of at least a few minutes (the person's "lying down before the night starts", default 3):
    # someone sitting on the edge for 30 s, or the dog hopping up, isn't getting into bed (2026-11-09: 30 s at
    # 11:21 PM made "in bed 11:21" for a night that began at 12:55)
    min_run = (getattr(person.settings, "in_bed_after_min", None) or 3) * 60
    runs, cur = [], []
    for r in occ:
        if cur and r["ts"] - cur[-1]["ts"] > 360:          # a gap of 6+ min ends a stretch (rows can be minutes apart)
            runs.append(cur)
            cur = []
        cur.append(r)
    if cur:
        runs.append(cur)
    real = [x for x in runs if x[-1]["ts"] - x[0]["ts"] >= min_run] or runs
    # a night given up early and started again (engine/night.py, event rhythm_restarted): the stretch(es) before it
    # aren't the night. In bed counts from the new start; the earlier time is kept only as a short note
    earlier = None
    restart = max((e for e in events if e["kind"] == "rhythm_restarted" and e.get("in_bed")), key=lambda e: e["ts"], default=None)
    if restart is not None:
        before = [x for x in real if x[-1]["ts"] < restart["in_bed"] - 120]
        if len(before) < len(real):
            ids = {id(x) for x in before}
            real = [x for x in real if id(x) not in ids]
        was, up = restart.get("was_in_bed"), restart.get("up_since")
        if was and up and up > was:                   # (often before this summary's window: from the event itself)
            earlier = {"at": was, "minutes": round((up - was) / 60)}
    # "up" only counts while the night was running: from the engine's start (event rhythm_started) to the schedule's end
    # (when the engine goes to "done"; it records no event for that). Before the start, lying down and getting up again
    # isn't getting up in the night: the in-bed stretches before it are kept as the short "earlier in bed" note.
    # With no start event (older data) every gap between first in and last out counts as before.
    started = max((e["ts"] for e in events if e["kind"] == "rhythm_started"), default=None)
    # the engine's own "night_done" (an alarm can end the night before the schedule's end) wins over the schedule
    done_at = min((e["ts"] for e in events if e["kind"] == "night_done" and started is not None and e["ts"] > started),
                  default=None)
    cut_end = done_at or full_end
    if started is not None and earlier is None:
        pre = [x for x in real if x[-1]["ts"] < started - 120]
        if pre and len(pre) < len(real):
            earlier = {"at": pre[0][0]["ts"], "minutes": round(sum(x[-1]["ts"] - x[0]["ts"] for x in pre) / 60)}
    occ = [r for x in real for r in x]
    in_bed_s = sum(dur[id(r)] for r in occ)
    first_in = occ[0]["ts"] if occ else None
    last_out = occ[-1]["ts"] if occ else None
    # out-of-bed gaps of 3+ minutes (or the person's own "counts as getting up after") between first in and last out
    up_s = (getattr(person.settings, "up_after_min", None) or 3) * 60
    gaps, run_start, last_empty = [], None, None
    for r in rows:
        if first_in is None or r["ts"] < first_in or r["ts"] > (last_out or 0):
            continue
        if r["occupied"] == 0:
            run_start = run_start or r["ts"]
            last_empty = r["ts"]
        elif r["occupied"] is None:
            continue                                  # not known (sensor starting up): neither in nor out
        else:
            # no rows for a while (the bed or the server was off): the time out of bed is only what was seen
            to = r["ts"] if last_empty is None or r["ts"] - last_empty <= 120 else last_empty + ROW_S
            # (a hold during which the person's own radar still saw them was a misread, not getting up: up_misread)
            if run_start and to - run_start >= up_s and not any(a <= to and run_start <= b for a, b in act)                     and not any(m["kind"] == "up_misread" and run_start - 120 <= m.get("since", 0) <= to for m in events):
                # (a trip that began after the night started and before it ended counts whole, even if it ran past either end)
                if started is None or (started <= run_start and (not cut_end or run_start < cut_end)):
                    gaps.append({"from": run_start, "to": to, "minutes": round((to - run_start) / 60)})
            run_start = last_empty = None
    occ_calm = [r for r in occ if not inside(r["ts"], act)]
    hrs = [r["hr"] for r in occ_calm if r["hr"]]
    brs = [r["br"] for r in occ_calm if r["br"]]
    med = median(hrs) if hrs else None
    settled = [r for r in occ_calm if r["status"] == 0 and r["hr"] and med and r["hr"] <= med + 2]
    settled_s = sum(dur[id(r)] for r in settled)
    # fell asleep (est.): first 10-minute window after getting in with ≥ 85% settled rows (after any active
    # stretch: that was before sleep)
    fell = None
    if occ_calm and med:
        good = [1 if (x["status"] == 0 and x["hr"] and x["hr"] <= med + 2) else 0 for x in occ_calm]
        j, acc = 0, 0
        for i, r in enumerate(occ_calm):            # sliding 10-minute window
            while j < len(occ_calm) and occ_calm[j]["ts"] < r["ts"] + 600:
                acc += good[j]
                j += 1
            n = j - i
            span = occ_calm[j - 1]["ts"] - r["ts"] if j > i else 0
            if n >= 3 and span >= 480 and acc / n >= 0.85 and not any(a <= r["ts"] + 600 and r["ts"] <= b for a, b in act):
                fell = r["ts"]
                break
            acc -= good[i]
    # settling is counted from the end of an active stretch that came before falling asleep
    settle_from = first_in
    for a, b in act:
        if first_in is not None and fell is not None and b <= fell and b > (settle_from or 0):
            settle_from = b
    act_min = round(sum(max(0.0, min(b, end) - a) for a, b in act) / 60)
    # no rows at all for a while between getting in and getting up: the bed or the server was off (a power cut, a hub
    # or server outage). Rows come every 15 s while the bed reports; a hole longer than 10 min (the cap each row's
    # duration has above) is counted whole. Autopilot leaves a night with an hour or more of it out of its learning.
    outage_s = 0.0
    if first_in is not None:
        inside_rows = [r["ts"] for r in rows if first_in <= r["ts"] <= (last_out or first_in)]
        outage_s = sum(b - a for a, b in zip(inside_rows, inside_rows[1:]) if b - a > 600)
    # awake and moving while in bed, in 10-minute steps, by the same signs as engine/active.py (moving in 35%+ of the
    # readings with a heart rate of 90+). Unlike the active period this has no 2-hour limit: a fever night counts whole.
    # (Older history is thinned to a few rows per step, so two readings are enough to judge a step.)
    from .active import MOVING as _MV, START_HR, START_MOVE
    bins: dict[int, list[dict]] = {}
    for r in occ:
        bins.setdefault(int(r["ts"] // 600), []).append(r)
    awake_bins = 0
    for b_ in bins.values():
        st = [x["status"] for x in b_ if x["status"] is not None]
        hh = [x["hr"] for x in b_ if x["hr"]]
        if len(st) >= 2 and len(hh) >= 2 and sum(1 for s_ in st if s_ in _MV) / len(st) >= START_MOVE and median(hh) >= START_HR:
            awake_bins += 1
    on = [r for r in rows if r["on"] and r["measured"] is not None and r["target"] is not None]
    track = median(abs(r["measured"] - r["target"]) for r in on) if on else None   # median: flashes deliberately jump the target
    lows = [r["measured"] for r in occ if r["measured"] is not None]
    moments = _moments(events, tz, {ph.id: ph.name for ph in rh.phases} if rh else None, zone=person.zone, pets=pets, until=end,
                      clock=clock_style(person.settings))
    for g in gaps:
        moments.append({"kind": "out_of_bed", "ts": g["from"], "minutes": g["minutes"]})
    for a, b in act:
        moments.append({"kind": "active", "ts": a, "end": b, "minutes": max(1, round((b - a) / 60)), "text": ACTIVE_TEXT})
    moments.sort(key=lambda m: m["ts"])
    out = {
        **base, "has_data": True,
        "in_bed_at": first_in, "out_at": last_out, "in_bed_min": round(in_bed_s / 60),
        "earlier_in_bed": earlier,                # {"at", "minutes"}: in bed earlier, before the night started again
        # got in 2 h or more after the usual bedtime: an unusual night (Autopilot doesn't learn your normal from it)
        "late_by_min": (round((first_in - usual_bed) / 60) if first_in and usual_bed and first_in - usual_bed >= LATE_S else None),
        "settled_min": round(settled_s / 60), "fell_asleep_est": fell,
        "time_to_settle_min": round((fell - settle_from) / 60) if fell and settle_from else None,
        "active_min": act_min or None,
        "outage_min": round(outage_s / 60) or None,           # the bed or server off while in bed (no readings)
        "awake_moving_min": awake_bins * 10 or None,          # awake and moving in bed (fast heart), whole night
        "awake_moving_share": round(awake_bins / len(bins), 2) if bins else None,
        "times_up": len(gaps), "up_minutes": sum(g["minutes"] for g in gaps),
        "hr_avg": round(sum(hrs) / len(hrs)) if hrs else None, "hr_low": min(hrs) if hrs else None,
        "br_avg": round(sum(brs) / len(brs), 1) if brs else None,
        "tracking_c": round(track, 2) if track is not None else None,
        "coolest_c": round(min(lows), 1) if lows else None, "warmest_c": round(max(lows), 1) if lows else None,
        "hot_flashes": sum(1 for m in moments if m["kind"] == "hot_flash"),
        "cold_flashes": sum(1 for m in moments if m["kind"] == "cold_flash"),
        "adjustments": sum(m["count"] for m in moments if m["kind"] == "adjust"),
        "moments": moments,
    }
    if series:
        buckets: dict[int, list[dict]] = {}
        for r in rows:
            buckets.setdefault(int((r["ts"] - start) // 300), []).append(r)
        pts = []
        for k in sorted(buckets):
            b = buckets[k]
            avg = lambda key: (lambda v: round(sum(v) / len(v), 2) if v else None)([x[key] for x in b if x[key] is not None])  # noqa: E731
            pts.append({"ts": start + k * 300 + 150, "target": avg("target"), "measured": avg("measured"),
                        "hr": avg("hr"), "occupied": round(sum(1 for x in b if x["occupied"] == 1) / len(b), 2)})
        out["series"] = pts
    return out


def recent(store: Store, person: Person, tz: ZoneInfo, now: float, count: int = 7) -> list[dict]:
    today = datetime.fromtimestamp(now, tz)
    d0 = (today - timedelta(days=1)).date() if today.hour < 12 else today.date()
    out = []
    for i in range(count):
        s = summarize(store, person, d0 - timedelta(days=i), tz, now)
        if s and (s.get("has_data") or i == 0):
            out.append(s)
    return out
