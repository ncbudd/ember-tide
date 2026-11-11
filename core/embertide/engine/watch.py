"""Live heart rate from the person's own watch: "is it me in bed, or not me?"

The watch (any Wear OS watch, e.g. a Galaxy Watch) collects heart rate passively at night and hands it to the phone
in batches every few minutes; the phone posts it to POST /api/people/{pid}/watch-hr. The samples are stored as
`wear:<pid>:hr:watch_live` (so Autopilot's wearable heart rate sees them too) and a little state in kv
`watch_live:<pid>` (last report, recent step counts).

identity() lines the watch's readings up with the heart rate the bed pad on that person's side reads:
- "me":      the two agree (median difference ≤ 4 bpm, at least 2 of 3 pairs within 6) over a few minutes;
- "not_me":  they're clearly apart (median > 12 bpm, nearly every pair > 8) while the pad reads steadily, or the
             watch counted footsteps while the pad was steadily occupied (you can't walk around and lie in bed);
- "unsure":  in between, or not enough pairs yet;
- "stale":   nothing from the watch lately (not worn, switched off, out of range, the night isn't on). No vote.
The controller turns this into a modest vote for that person's side only (controller._watch_vote); a scale or
radar that sees a person always wins during a running night, and a person already settled in bed is never voted out.
Outside a running night, with a pet that may lie on that side, "not me" makes it the pet even when a radar sees
"someone" (it sees the dog too: engine/pets.py), and the watch is asked for heart rate for the first 20 minutes of a
stretch on that side by day or in the evening (day_ask).
"""

from __future__ import annotations

import statistics

SOURCE = "wear:{pid}:hr:watch_live"
FRESH_S = 15 * 60          # newest watch reading at most this old (batches every ~5 min; a still watch may sample every 10 min)
WINDOW_S = 20 * 60         # readings compared over this long
PAIR_S = 90                # a watch reading pairs with the pad's readings within this much either side
MIN_PAIRS = 3
MIN_SPAN_S = 180
ME_BPM = 4.0
FAR_BPM = 12.0
STEPS_AWAY = 40            # steps in a few minutes while the pad was steadily occupied: not you in bed
PAD_STEADY_SD = 6.0


def source(pid: str) -> str:
    return SOURCE.format(pid=pid)


def record(store, pid: str, samples: list[tuple[float, float]], steps: list[tuple[float, float]], now: float) -> int:
    """Store a batch from the watch. Returns how many new readings were kept (repeats of a resent batch are skipped)."""
    st = store.get(f"watch_live:{pid}") or {}
    last = float(st.get("last_ts") or 0)
    rows = [(float(t), source(pid), float(v)) for t, v in sorted(samples) if float(t) > last]
    if rows:
        store.samples_many(rows)
        st["last_ts"] = rows[-1][0]
    keep = [x for x in (st.get("steps") or []) if now - x[0] <= 3600]
    seen = {round(x[0]) for x in keep}
    keep += [[float(t), float(n)] for t, n in steps if round(t) not in seen and now - t <= 3600]
    st["steps"] = sorted(keep)[-240:]
    st["seen"] = now
    store.put(f"watch_live:{pid}", st)
    return len(rows)


def recent(store, pid: str, now: float, span: float = WINDOW_S) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    st = store.get(f"watch_live:{pid}") or {}
    if not st.get("seen"):
        return [], []                                # the switch is off (or never on): nothing live from the watch
    hr = store.samples(source(pid), now - span, now + 60)
    steps = [(t, n) for t, n in (st.get("steps") or []) if now - span <= t <= now + 60]
    return hr, steps


def identity(watch: list[tuple[float, float]], pad: list[tuple[float, float]], now: float,
             steps: list[tuple[float, float]] | None = None) -> dict:
    """Compare the watch with the pad. watch/pad = [(ts, bpm)], oldest first; pad only while the pad says occupied."""
    watch = [(t, v) for t, v in watch if now - WINDOW_S <= t <= now + 60]
    out = {"state": "stale", "pairs": 0, "diff": None, "watch": None, "pad": None, "span_s": 0, "why": "nothing from the watch lately"}
    if not watch or now - watch[-1][0] > FRESH_S:
        return out
    out["watch"] = round(watch[-1][1])
    padw = [(t, v) for t, v in pad if now - WINDOW_S - PAIR_S <= t <= now + 60 and v]
    if padw:
        out["pad"] = round(padw[-1][1])
    # footsteps while the pad was steadily occupied over the same minutes
    if steps and padw:
        walked = 0.0
        for t, n in steps:
            near = [p for p in padw if abs(p[0] - t) <= 150]
            if len(near) >= 2:
                walked += n
        if walked >= STEPS_AWAY:
            out.update(state="not_me", why=f"your watch counted {int(walked)} steps while the bed read someone lying still")
            return out
    pairs = []
    for t, v in watch:
        near = [p[1] for p in padw if abs(p[0] - t) <= PAIR_S]
        if len(near) >= 2:
            pairs.append((t, v, statistics.median(near)))
    out["pairs"] = len(pairs)
    if len(pairs) < MIN_PAIRS or pairs[-1][0] - pairs[0][0] < MIN_SPAN_S:
        out.update(state="unsure", why="not enough heart-rate readings to compare yet")
        return out
    out["span_s"] = round(pairs[-1][0] - pairs[0][0])
    d = [abs(w - p) for _, w, p in pairs]
    med = statistics.median(d)
    out["diff"] = round(med, 1)
    padv = [v for t, v in padw if pairs[0][0] - PAIR_S <= t <= pairs[-1][0] + PAIR_S]
    steady = len(padv) >= 4 and statistics.pstdev(padv) <= PAD_STEADY_SD
    # say it with the numbers it decided on (medians over the last few minutes), not the latest single readings,
    # which can be far apart for a moment ("62 and 76 agree" read as nonsense)
    out["watch_med"] = round(statistics.median(w for _, w, _ in pairs))
    out["pad_med"] = round(statistics.median(p for _, _, p in pairs))
    mins = max(1, round(out["span_s"] / 60))
    if med <= ME_BPM and sum(1 for x in d if x <= 6) * 3 >= 2 * len(d):
        out.update(state="me", why=f"over the last {mins} min your watch ({out['watch_med']}) and the bed ({out['pad_med']}) agree")
    elif med > FAR_BPM and sum(1 for x in d if x > 8) >= len(d) - 1 and steady:
        out.update(state="not_me", why=f"over the last {mins} min your watch read about {out['watch_med']}, the bed {out['pad_med']}: not you")
    else:
        out.update(state="unsure", why=f"over the last {mins} min your watch ({out['watch_med']}) and the bed ({out['pad_med']}) are about {med:.0f} apart")
    return out


def wanted(controller, p, now: float) -> bool:
    """Should the watch be collecting heart rate for this person right now? Only at night AND while someone is on
    their side of the bed (the pad says occupied, or did within the last 10 min): that's when "is it me?" matters.
    Night = a night running, or from half an hour before the evening's pre-condition until the night ends. The answer
    goes back to the watch with every batch it sends, so it stops within one batch of getting out of bed."""
    e = controller.engine_for(p.id)
    if p.zone in controller.household.guest:
        return False
    night = bool(e.s.started and e.s.state not in ("done", "day"))
    if not night:
        try:
            n = e.projected(now)
        except Exception:  # noqa: BLE001
            n = None
        night = bool(n and n.precool - 1800 <= now <= n.end)
    if not night:
        return day_ask(controller, p, now)
    pf = getattr(controller, "presence", None)
    side = pf.sides.get(p.zone) if pf is not None else None
    if side is None or side.raw_occupied is None:
        return True                                   # can't tell (no pad reading): keep the night's default
    if side.raw_occupied:
        return True
    return side.since is not None and now - side.since < 600   # just got up: a short grace, no flapping


DAY_ASK_S = 20 * 60          # outside the night: heart rate for up to this long after the side gets occupied (the
                             # phone asks every 15 min at most, so a shorter window could be missed altogether)
DAY_ASKS_PER_H = 3           # and for at most this many stretches an hour (battery)


def day_ask(controller, p, now: float) -> bool:
    """Outside the night: someone just lay down on this person's side (the dog, by day or in the evening). With a pet
    that may be on that side and the watch connected (as far as the phone says), ask the watch for live heart rate for
    the first DAY_ASK_S of the stretch, for at most DAY_ASKS_PER_H stretches an hour, so "is it me?" can be answered."""
    hh = controller.household
    if p.zone in hh.guest or not [x for x in getattr(hh, "pets", []) or []
                                  if x.sleeps_on_bed != "no" and x.side in ("either", "both", p.zone)]:
        return False
    from . import phone_context as PC
    cur = PC.latest(controller.store, p.id)
    if cur and PC.fresh(cur, "watch_connected", "watch", now) is False:
        return False                                  # the phone says the watch isn't with it
    ep = (controller.__dict__.get("_eps") or {}).get(p.zone)
    if not ep or now - ep["since"] > DAY_ASK_S:
        return False
    asks = controller.__dict__.setdefault("_watch_day_asks", {}).setdefault(p.id, [])
    asks[:] = [t for t in asks if now - t <= 3600]
    if ep["since"] not in asks:
        if len(asks) >= DAY_ASKS_PER_H:
            return False
        asks.append(ep["since"])
    return True
