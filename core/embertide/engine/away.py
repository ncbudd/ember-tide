"""Away for a while (a trip, a week at a friend's): that person's nights pause until they're back.

kv `away:<pid>` = {"since": ts, "by": pid, "until": ts | None}

While someone is away:
- their rhythm doesn't start, even if something lies on their side (state "away"; the side stays off);
- Autopilot's calibration week is paused: no night of theirs counts (nothing of theirs ran) and it says so;
- no morning summary and no "was that the pet?" questions go to them;
- a stay on their side is almost certainly the pet: its readings are held (kv `away_spans:<pid>`) and taught to the
  pet's signature later, once the time is safely behind us (LEARN_AFTER_S) and they are still away. If they turn out
  to have been back (they say so, or use their side), the held stretches from the hours before are dropped, not learned.

They are back when they (or the owner) tap "I'm back", turn their side on or start the night by hand (app, dial,
voice), or the end date they set passes. Their side then runs as usual from that moment.
"""

from __future__ import annotations

LEARN_AFTER_S = 18 * 3600     # a held stretch is taught as the pet only this long after it ended, still away
DROP_BEFORE_BACK_S = 18 * 3600  # back: held stretches that ended this close before are dropped (could have been them)
MAX_SPANS = 60
MAX_READINGS = 480


def _key(pid: str) -> str:
    return f"away:{pid}"


def _spans_key(pid: str) -> str:
    return f"away_spans:{pid}"


def get(store, pid: str, now: float | None = None) -> dict | None:
    """The away record, or None. An end date that has passed counts as back (cleared on the next tick)."""
    rec = store.get(_key(pid))
    if not isinstance(rec, dict) or not rec.get("since"):
        return None
    if now is not None and rec.get("until") and now >= rec["until"]:
        return None
    return rec


def is_away(store, pid: str, now: float | None = None) -> bool:
    return get(store, pid, now) is not None


def start(controller, pid: str, now: float, by: str | None = None, until: float | None = None) -> dict:
    rec = {"since": now, "by": by or pid, "until": until}
    controller.store.put(_key(pid), rec)
    controller.store.event(now, "away_started", pid, by or "app", until=until)
    return rec


def end(controller, pid: str, now: float, reason: str, by: str | None = None) -> bool:
    """Back home. Returns False when they weren't away."""
    rec = controller.store.get(_key(pid))
    if not isinstance(rec, dict) or not rec.get("since"):
        return False
    controller.store.put(_key(pid), None)
    log = (controller.store.get(f"away_log:{pid}") or [])[-40:]
    controller.store.put(f"away_log:{pid}", log + [[rec["since"], now]])
    learned = _settle(controller, pid, now, back=True)
    controller.store.event(now, "away_ended", pid, by or reason, reason=reason, since=rec["since"], learned=learned)
    return True


def hold(controller, pid: str, zone: str, ep: dict, pet_id: str | None) -> None:
    """A stay on an away person's side: keep its readings to teach as the pet later (no question, no ping)."""
    if not pet_id:
        return
    rt = ep.get("radar_ticks", 0)
    if rt >= 4 and ep.get("radar_empty", 0) / rt >= 0.5 and ep.get("evidence") != "away":
        # the radar saw nobody there: the partner's movement leaking across. (Unless the strip itself was judged the pet
        # for the stretch, "you're away, so this is probably …": a radar zone may not see a dog lying flat)
        return
    if ep.get("me_seen") or ((getattr(controller, "context", None) or {}).get(pid) or {}).get("home") is True:
        return                                        # their own watch matched the bed, or their phone says home: them
    spans = controller.store.get(_spans_key(pid)) or []
    spans.append({"start": round(ep["since"]), "end": round(ep["last_on"]), "zone": zone, "pet": pet_id,
                  "readings": list(ep.get("readings") or [])[-MAX_READINGS:]})
    controller.store.put(_spans_key(pid), spans[-MAX_SPANS:])


def _settle(controller, pid: str, now: float, back: bool = False) -> int:
    """Teach the held stretches that are safely in the past as the pet; when they're back, drop the recent ones."""
    store = controller.store
    spans = store.get(_spans_key(pid)) or []
    if not spans:
        return 0
    from .petq import learn_pet
    # a "Welcome back?" asked about a night: its stays wait for the answer (not taught while asked), and are dropped
    # when the answer is "back" (they could have been them)
    asked = _back_nights(controller, pid, ("asked",))
    said_back = _back_nights(controller, pid, ("asked", "back"))
    keep, learned = [], 0
    for s in spans:
        age = now - s["end"]
        if back:
            if any(s["start"] < b and s["end"] > a for a, b in said_back):
                continue
            if age >= DROP_BEFORE_BACK_S and s.get("readings"):
                learn_pet(store, s["pet"], s["readings"])
                learned += 1
            continue                                  # recent ones could have been them: dropped
        if any(s["start"] < b and s["end"] > a for a, b in asked):
            keep.append(s)
            continue
        if age >= LEARN_AFTER_S:
            if s.get("readings"):
                learn_pet(store, s["pet"], s["readings"])
                learned += 1
        else:
            keep.append(s)
    store.put(_spans_key(pid), keep)
    if learned and not back:
        store.event(now, "away_learned", pid, "pets", stretches=learned)
    return learned


def _back_nights(controller, pid: str, statuses: tuple[str, ...]) -> list[tuple[float, float]]:
    """6 PM-noon spans of the nights with a "Welcome back?" check in one of these statuses."""
    from datetime import date
    out = []
    for k, v in checks(controller.store, pid).items():
        if v.get("kind") == "back" and v.get("status") in statuses:
            try:
                out.append(_night_span(date.fromisoformat(k), controller.clock.tz))
            except ValueError:
                continue
    return out


BACK_KINDS = {"tonight_start_now", "tonight_resume"}


def back_by_hand(ev: dict) -> bool:
    """An event that means the person is using their own side: they're back."""
    kind, src = ev.get("kind"), ev.get("source")
    if src in ("autopilot", "rhythm", None):
        return False
    return kind in BACK_KINDS or (kind == "adjust" and (ev.get("temp_c") is not None or ev.get("delta_c") is not None))


def tick(controller, p, now: float, events: list[dict]) -> None:
    """Each tick, for someone with an away record: back by hand / end date passed -> back; teach old held stretches."""
    store = controller.store
    rec = store.get(_key(p.id))
    if not isinstance(rec, dict) or not rec.get("since"):
        return
    if rec.get("until") and now >= rec["until"]:
        end(controller, p.id, now, "end date")
        return
    for ev in events:
        if ev.get("ts", 0) >= rec["since"] and back_by_hand(ev):
            end(controller, p.id, now, "used their side", by=ev.get("source"))
            return
    last = controller.__dict__.setdefault("_away_settled", {})
    if now - last.get(p.id, 0) >= 600 or now < last.get(p.id, 0):
        last[p.id] = now
        _settle(controller, p.id, now)


def view(store, pid: str, now: float) -> dict | None:
    rec = get(store, pid, now)
    if rec is None:
        return None
    return {"since": rec["since"], "until": rec.get("until"), "by": rec.get("by"),
            "held": len(store.get(_spans_key(pid)) or [])}


# ---------- the morning check: were they in their side of the bed last night? ----------
# kv `away_check:<pid>` = {"<night date>": {"kind": "away" | "back", "status": "asked" | "away" | "night" | "home" |
#                          "back" | "still" | "auto", "why": str, "at": ts}}
# kind "away": not marked away, but they clearly weren't in bed -> asked: away for a few days / just that night / home.
# kind "back": marked away, but their ring shows them asleep in their side -> asked: back / still away.
CHECK_FROM_H, CHECK_UNTIL_H = 7, 21          # asked during the day after the night
EXCLUDE = {"asked", "away", "night", "auto"}  # nights left out of Autopilot's learning (while asked: until answered)
KNOWN_COVER = 0.5                            # the bed must have known presence for this share of the span to judge


def _night_span(d, tz) -> tuple[float, float]:
    from datetime import datetime, timedelta
    a = datetime(d.year, d.month, d.day, 18, 0, tzinfo=tz)
    return a.timestamp(), (a + timedelta(hours=18)).timestamp()       # 6 PM to noon


def checks(store, pid: str) -> dict:
    v = store.get(f"away_check:{pid}")
    return v if isinstance(v, dict) else {}


def _save_checks(store, pid: str, v: dict) -> None:
    store.put(f"away_check:{pid}", dict(sorted(v.items())[-60:]))


def _corr(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 8:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def _hr_follows(c, p, a: float, b: float) -> tuple[bool | None, str]:
    """Does the bed's heart rate on their side follow their own ring/watch (same level, and rising and falling
    together in 5-minute steps)? A dog with the same resting rate matches the level but not the ups and downs.
    None = not enough readings to say."""
    rows = [r for r in c.store.history(p.zone, a, b, step_s=60, person=p.id) if r.get("hr") and r.get("occupied") == 1]
    wear = [(t, v) for src in c.store.sample_sources(f"wear:{p.id}:hr:") for t, v in c.store.samples(src, a, b)]
    if len(rows) < 40 or len(wear) < 8:
        return None, "not enough heart-rate readings"
    bins_b: dict[int, list[float]] = {}
    bins_w: dict[int, list[float]] = {}
    for r in rows:
        bins_b.setdefault(int(r["ts"] // 300), []).append(r["hr"])
    for t, v in wear:
        bins_w.setdefault(int(t // 300), []).append(v)
    keys = sorted(set(bins_b) & set(bins_w))
    if len(keys) < 8:
        return None, "too few readings at the same times"
    xb = [sum(bins_b[k]) / len(bins_b[k]) for k in keys]
    xw = [sum(bins_w[k]) / len(bins_w[k]) for k in keys]
    gap = abs(sum(xb) / len(xb) - sum(xw) / len(xw))
    r = _corr(xb, xw)
    if gap <= 5 and r is not None and r >= 0.5:
        return True, f"the bed's heart rate followed theirs (r {r:.2f})"
    if gap > 8 or (r is not None and r < 0.2):
        return False, "the bed's heart rate didn't follow theirs"
    return None, "unclear"


def night_verdict(c, p, d) -> tuple[str | None, str]:
    """Night `d` (the evening's date): "away" when we can be sure they weren't in their side of the bed, "bed" when
    their own ring/watch shows them asleep there, None when it can't be told (yet)."""
    from . import sleeps
    a, b = _night_span(d, c.clock.tz)
    rings = [x for x in sleeps.all_for(c.store, p.id) if x.get("kind") == "night" and a <= x["start"] < b]
    if rings:
        x = max(rings, key=lambda x: x["end"] - x["start"])
        # only minutes the bed knew about count: no rows (bed or server down) or presence unknown isn't "empty"
        rows = [r for r in c.store.history(p.zone, x["start"], x["end"], step_s=60, person=p.id) if r.get("occupied") is not None]
        if len(rows) * 60 < KNOWN_COVER * (x["end"] - x["start"]):
            return None, "the bed didn't report for much of the night"
        occ = sum(1 for r in rows if r.get("occupied") == 1) / len(rows)
        if occ < 0.2:
            return "away", "your ring had you asleep while your side of the bed was empty"
        ok, why = _hr_follows(c, p, x["start"], x["end"])
        if ok is True and occ >= 0.5:
            return "bed", "your ring and your side of the bed agree"
        if ok is False:
            return "away", "something was on your side, but its heart rate didn't follow your ring's"
        return None, why
    rows = [r for r in c.store.history(p.zone, a, b, step_s=60, person=p.id) if r.get("occupied") is not None]
    if len(rows) >= max(600, KNOWN_COVER * (b - a) / 60) and sum(1 for r in rows if r.get("occupied") == 1) / len(rows) < 0.03:
        return "away", "your side of the bed was empty all night"
    return None, "no ring night yet"


def morning_check(c, p, now: float) -> dict | None:
    """During the day after a night: if they weren't in bed (and aren't marked away), or they're marked away but
    their ring shows them asleep in their side, open a question (once per night). Returns a newly opened question."""
    from datetime import datetime, timedelta
    dt = datetime.fromtimestamp(now, c.clock.tz)
    if not CHECK_FROM_H <= dt.hour < CHECK_UNTIL_H:
        return None
    d = (dt - timedelta(days=1)).date()
    key = d.isoformat()
    cs = checks(c.store, p.id)
    if key in cs:
        return None
    rec = c.store.get(_key(p.id))
    away_then = isinstance(rec, dict) and bool(rec.get("since")) and rec["since"] <= _night_span(d, c.clock.tz)[1]
    verdict, why = night_verdict(c, p, d)
    if verdict is None:
        return None                                   # can't be sure (yet): ask nothing, try again later
    if away_then:
        if verdict == "away":
            cs[key] = {"kind": "away", "status": "auto", "why": why, "at": now}       # as expected: nothing to ask
            _save_checks(c.store, p.id, cs)
            return None
        q = {"kind": "back", "status": "asked", "why": why, "at": now}
    else:
        if verdict != "away":
            return None                               # in bed as usual
        q = {"kind": "away", "status": "asked", "why": why, "at": now}
    cs[key] = q
    _save_checks(c.store, p.id, cs)
    _forget_learning(c, p.id)
    c.store.event(now, "away_check", p.id, "away", night=key, about=q["kind"], why=why)
    return {**q, "night": key}


def open_check(store, pid: str) -> dict | None:
    cs = checks(store, pid)
    for k in sorted(cs, reverse=True):
        if cs[k].get("status") == "asked":
            return {**cs[k], "night": k}
    return None


ANSWERS = {"away": {"away", "night", "home"}, "back": {"back", "still"}}


def answer_check(c, pid: str, night: str, ans: str, now: float) -> dict:
    """kind "away": away (for a few days) / night (just that night) / home. kind "back": back / still (away)."""
    cs = checks(c.store, pid)
    q = cs.get(night)
    if q is None:
        raise KeyError(night)
    if ans not in ANSWERS[q["kind"]]:
        raise ValueError("not an answer to that question")
    if q["status"] != "asked":
        return {**q, "night": night}
    q.update(status=ans, answered_at=now)
    _save_checks(c.store, pid, cs)
    from datetime import date
    a, b = _night_span(date.fromisoformat(night), c.clock.tz)
    if ans in ("away", "night"):
        _settle_pet(c, pid, a, b, now)                # whatever lay on their side that night was the pet
        if ans == "away" and not is_away(c.store, pid):
            start(c, pid, now, by=pid)
            rec = c.store.get(_key(pid))
            rec["since"] = a                          # away since that evening
            c.store.put(_key(pid), rec)
    elif ans == "back":
        end(c, pid, now, "back (morning check)", by=pid)
    _forget_learning(c, pid)
    c.store.event(now, "away_check_answer", pid, "app", night=night, answer=ans)
    return {**q, "night": night}


def _settle_pet(c, pid: str, a: float, b: float, now: float) -> None:
    from . import petq
    p = next((x for x in c.household.people if x.id == pid), None)
    pets = petq.fitting_pets(c.household, p.zone) if p else []
    if len(pets) == 1:
        petq.settle_from_fix(c, pid, [{"start": a, "end": b, "who": "pet", "pet": pets[0].id}], now)


def _forget_learning(c, pid: str) -> None:
    ap = getattr(c, "autopilot", None)
    if ap is not None:
        for k in ("_nights_cache", "_base_cache"):
            ap.__dict__.get(k, {}).pop(pid, None)


def excluded_nights(store, pid: str) -> set[str]:
    """Nights left out of Autopilot's learning: they weren't in bed (answered so, or still being asked)."""
    return {k for k, v in checks(store, pid).items() if v.get("status") in EXCLUDE}


def away_periods(store, pid: str, now: float) -> list[tuple[float, float]]:
    """Every stretch they were marked away (past ones from `away_log:<pid>`, plus now)."""
    out = [(x[0], x[1]) for x in (store.get(f"away_log:{pid}") or []) if isinstance(x, list) and len(x) == 2]
    rec = store.get(_key(pid))
    if isinstance(rec, dict) and rec.get("since"):
        out.append((rec["since"], now))
    return out


def away_share(periods: list[tuple[float, float]], start: float | None, end: float | None) -> float:
    """How much of a night (start..end) fell inside away periods, 0..1. A night that began just before they were
    marked away (something on their side at the time) is still an away night: most of it happened while they were gone."""
    if not start:
        return 0.0
    if not end or end <= start:
        return 1.0 if any(x <= start <= y for x, y in periods) else 0.0
    covered = sum(max(0.0, min(end, y) - max(start, x)) for x, y in periods)
    return min(1.0, covered / (end - start))


def hide_away_night(store, pid: str, night: dict | None, now: float) -> dict | None:
    """A night that mostly fell inside an away period is not this person's (something else lay on their side): for the
    nights/night summaries the Sleep screen shows, swap its numbers for a plain "away" marker. Same rule as Autopilot's
    learning (away_share)."""
    if not night or not night.get("has_data"):
        return night
    if away_share(away_periods(store, pid, now), night.get("in_bed_at"), night.get("out_at")) < 0.5:
        return night
    keep = ("date", "label", "in_progress", "before", "feedback")
    return {**{k: night[k] for k in keep if k in night}, "has_data": False, "away": True, "moments": []}
