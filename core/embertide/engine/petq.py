"""Ask the owner about a pet, and learn from the answer.

When something lay on a side for a while and the system thinks it was a pet (any path: scale, heart rate, watch
mismatch, outside the person's normal range, learned pet signature), or something lay on an empty side with nobody's
night running (a daytime nap on the bed), the sleeper of that side gets one plain question:
    "Was Rex on the bed around 12:40 AM–1:10 AM?"   Yes · No, it was me · Not sure
- at most one open question per person; episodes from the same night (noon to noon) join one question;
- "Yes" teaches that pet's heart-rate / breathing signature (running mean and spread, kv `pet_sigs`);
- "No, it was me" widens the person's learned range (kv `person_range:<pid>`), so the same readings count as them;
- "Not sure" just closes it. A person can turn the questions off (PersonSettings.pet_questions).
Stored in kv `pet_questions` (latest 60). Readings are kept as 30 s averages, only while the side was occupied.
"""

from __future__ import annotations

import math
import time
import uuid
from datetime import datetime, timedelta
from ..units import fmt_clock

MIN_EPISODE_S = 10 * 60       # a stay this long is worth noting (added to a question that's already open: no new ping)
ASK_AFTER_S = 30 * 60         # only a stay this long starts a new question: a quick hop on and off isn't worth a ping
MAX_KEEP = 60
SIG_MIN_N = 20                 # 30 s readings (10 min) before a pet's signature is used
HR_BOUNDS = (30.0, 220.0)
BR_BOUNDS = (5.0, 60.0)


# ---------- signatures ----------
def _merge(sig: dict | None, xs: list[float], key: str) -> dict:
    """Running mean / M2 (Welford, parallel form) for one reading."""
    sig = dict(sig or {})
    n0, m0, q0 = sig.get("n_" + key, 0), sig.get(key + "_mean", 0.0), sig.get(key + "_m2", 0.0)
    if not xs:
        return sig
    n1, m1 = len(xs), sum(xs) / len(xs)
    q1 = sum((x - m1) ** 2 for x in xs)
    n = n0 + n1
    d = m1 - m0
    sig["n_" + key] = n
    sig[key + "_mean"] = round(m0 + d * n1 / n, 3)
    sig[key + "_m2"] = round(q0 + q1 + d * d * n0 * n1 / n, 3)
    return sig


def sig_view(sig: dict | None) -> dict | None:
    """{hr: (mean, sd), br: (mean, sd), n} or None when too little is known."""
    if not sig or sig.get("n_hr", 0) < SIG_MIN_N:
        return None
    out = {"n": sig["n_hr"]}
    for k in ("hr", "br"):
        n = sig.get("n_" + k, 0)
        if n >= SIG_MIN_N:
            out[k] = (sig[k + "_mean"], math.sqrt(sig[k + "_m2"] / max(1, n - 1)))
    return out


def pet_sigs(store) -> dict:
    return store.get("pet_sigs") or {}


def learn_pet(store, pet_id: str, readings: list[list[float]], at: float | None = None) -> dict:
    sigs = pet_sigs(store)
    hr = [r[1] for r in readings if r[1] is not None and HR_BOUNDS[0] <= r[1] <= HR_BOUNDS[1]]
    br = [r[2] for r in readings if len(r) > 2 and r[2] is not None and BR_BOUNDS[0] <= r[2] <= BR_BOUNDS[1]]
    s = _merge(sigs.get(pet_id), hr, "hr")
    s = _merge(s, br, "br")
    if hr or br:                                      # for the Pets summary: how many stays, and when it last changed
        s["stays"] = s.get("stays", 0) + 1
        s["updated"] = round(at if at is not None else time.time())
    sigs[pet_id] = s
    store.put("pet_sigs", sigs)
    return s


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))]


def learned_range(store, pid: str) -> dict:
    return store.get(f"person_range:{pid}") or {}


def widen_person(store, pid: str, readings: list[list[float]]) -> dict:
    """"No, it was me": this person's range now covers the middle 80% of those readings."""
    cur = learned_range(store, pid)
    for i, k, (lo_b, hi_b) in ((1, "hr", (35.0, 130.0)), (2, "br", (6.0, 35.0))):
        xs = [r[i] for r in readings if len(r) > i and r[i] is not None]
        if len(xs) < 6:
            continue
        lo, hi = max(lo_b, _pct(xs, 0.1)), min(hi_b, _pct(xs, 0.9))
        if k in cur:
            lo, hi = min(lo, cur[k][0]), max(hi, cur[k][1])
        cur[k] = [round(lo, 1), round(hi, 1)]
    store.put(f"person_range:{pid}", cur)
    return cur


def person_range(band: dict | None, learned: dict | None) -> dict | None:
    """The person's sleeping range: Autopilot's learned band (middle 80% of the learning nights) joined with what
    "No, it was me" answers added. None until there is a heart-rate range."""
    out: dict = {}
    for k in ("hr", "br"):
        a = (band or {}).get(k)
        b = (learned or {}).get(k)
        if a and b:
            out[k] = [min(a[0], b[0]), max(a[1], b[1])]
        elif a or b:
            out[k] = list(a or b)
    return out if "hr" in out else None


def far_outside(hr: float | None, br: float | None, rng: dict) -> bool:
    """Far outside the person's sleeping range: beyond the range by its own width (at least 10 bpm / 5 breaths)."""
    if hr is not None and rng.get("hr"):
        lo, hi = rng["hr"]
        m = max(10.0, hi - lo)
        if hr > hi + m or hr < lo - m:
            return True
    if br is not None and rng.get("br"):
        lo, hi = rng["br"]
        m = max(5.0, hi - lo)
        if br > hi + m or br < lo - m:
            return True
    return False


def _dist_sig(hr, br, sig) -> float | None:
    parts = []
    if hr is not None and sig.get("hr"):
        parts.append(abs(hr - sig["hr"][0]) / max(4.0, sig["hr"][1]))
    if br is not None and sig.get("br"):
        parts.append(abs(br - sig["br"][0]) / max(2.0, sig["br"][1]))
    return sum(parts) / len(parts) if parts else None


ADULT_SLEEP = {"hr": [48.0, 72.0], "br": [11.0, 18.0]}   # a sleeping adult, when the person's own range lacks one


def _dist_range(hr, br, rng) -> float | None:
    parts = []
    for v, k, floor in ((hr, "hr", 4.0), (br, "br", 2.0)):
        r = (rng or {}).get(k) or ADULT_SLEEP[k]
        if v is not None and r:
            lo, hi = r
            parts.append(abs(v - (lo + hi) / 2) / max(floor, (hi - lo) / 2))
    return sum(parts) / len(parts) if parts else None


def distinct(sig: dict, rng: dict | None) -> bool:
    """Whether a pet's learned signature is told apart from the person's range at all: not when the pet's middle heart
    rate AND breathing both sit inside the person's range (a dog asleep at 61 bpm / 14 breaths is a sleeping adult)."""
    inside = []
    for k in ("hr", "br"):
        r = (rng or {}).get(k) or ADULT_SLEEP[k]
        if sig.get(k):
            inside.append(r[0] <= sig[k][0] <= r[1])
    return not inside or not all(inside)


def closer_to_pet(hr, br, rng: dict | None, sigs: dict[str, dict]) -> str | None:
    """The pet whose learned signature is clearly closer than the person's range, or None."""
    best, best_d = None, None
    for pid, sig in sigs.items():
        d = _dist_sig(hr, br, sig)
        if d is not None and (best_d is None or d < best_d):
            best, best_d = pid, d
    if best is None or best_d > 2.5:
        return None
    if not distinct(sigs[best], rng):
        return None                                  # this pet sleeps like the person: its signature can't tell them apart
    dp = _dist_range(hr, br, rng)
    if dp is not None and best_d >= dp * 0.7:
        return None
    return best


# ---------- questions ----------
def _group(ts: float, tz) -> str:
    """The night an episode belongs to: noon to noon, named by the evening's date."""
    return (datetime.fromtimestamp(ts, tz) - timedelta(hours=12)).date().isoformat()


def questions(store) -> list[dict]:
    return store.get("pet_questions") or []


def _save(store, qs: list[dict]) -> None:
    store.put("pet_questions", qs[-MAX_KEEP:])


def fitting_pets(household, zone: str) -> list:
    return [p for p in getattr(household, "pets", []) if p.sleeps_on_bed != "no" and p.side in ("either", "both", zone)]


def consider(controller, zone: str, ep: dict, now: float) -> dict | None:
    """An occupancy episode on `zone` just ended (or ran long). Ask about it when it looks like a pet, or when it was
    unexplained (nobody's night in bed). Returns the question it created or joined, else None."""
    hh = controller.household
    pets = fitting_pets(hh, zone)
    person = next((p for p in hh.people if p.zone == zone), None)
    if not pets or person is None or zone in hh.guest or not getattr(person.settings, "pet_questions", True):
        return None
    dur = ep["last_on"] - ep["since"]
    if dur < MIN_EPISODE_S:
        return None
    from . import away
    if away.is_away(controller.store, person.id, now):
        # they're away: whatever lay there wasn't them. Keep it to teach as the pet later, ask nobody
        # (with several pets, only when the stay was told apart as one of them)
        pet_id = pets[0].id if len(pets) == 1 else next((p.id for p in pets if p.id == ep.get("pet_id")), None)
        if pet_id and not ep.get("with_person") and not ep.get("active"):
            away.hold(controller, person.id, zone, ep, pet_id)
        return None
    pet_ticks, ticks, in_bed = ep.get("pet_ticks", 0), max(1, ep.get("ticks", 0)), ep.get("in_bed_ticks", 0)
    if ep.get("with_person"):
        return None                                  # the pet joined you: nothing to learn about either of you
    if ep.get("active"):
        return None                                  # people awake and moving (engine/active.py): not a pet stretch
    if pet_ticks:
        kind = "pet"
    elif in_bed / ticks < 0.3 and not ep.get("me_seen"):
        kind = "unexplained"
    else:
        return None
    names = [p for p in pets if p.id == ep.get("pet_id")] or pets
    span = {"start": round(ep["since"]), "end": round(ep["last_on"]), "evidence": ep.get("evidence") or None,
            "readings": [r for r in ep.get("readings", [])][-480:]}
    qs = questions(store := controller.store)
    grp = _group(ep["since"], controller.clock.tz)
    for q in reversed(qs):
        if q["person"] == person.id and q["status"] == "open" and q["group"] == grp and q["zone"] == zone:
            q["spans"].append(span)
            q["kind"] = "pet" if "pet" in (q["kind"], kind) else kind
            if len(names) == 1:
                q["pets"] = [names[0].id]
            _link(controller, qs, q, now)
            _save(store, qs)
            return q
    if dur < ASK_AFTER_S:
        return None                                  # short, and nothing open to add it to: no question, no ping
    for q in qs:                                     # one question at a time: an older open one is set aside
        if q["person"] == person.id and q["status"] == "open":
            q["status"] = "skipped"
    q = {"id": uuid.uuid4().hex[:10], "person": person.id, "zone": zone, "group": grp, "kind": kind, "created": now,
         "pets": [p.id for p in names][:3], "spans": [span], "status": "open", "answer": None, "answered_at": None,
         "pushed": False}
    qs.append(q)
    _link(controller, qs, q, now)
    _save(store, qs)
    controller.store.event(now, "pet_question", person.id, "pets", zone=zone, question=q["id"], about=kind)
    return q


# ---------- one stretch, both sides ----------
LINK_GAP_S = 15 * 60          # stretches this close in time on the two sides are the same visit (a dog across the bed)
LEARN_COVER = 0.7             # an answer from the other side teaches from this side's readings only if it covers this much


def _covered(spans: list[dict], by: list[dict], gap: float = 0) -> float:
    """How much of `spans` (by time) lies inside `by` (each widened by `gap`), 0..1."""
    tot = sum(max(0.0, x["end"] - x["start"]) for x in spans)
    if tot <= 0:
        return 0.0
    hit = 0.0
    for x in spans:
        for y in by:
            hit += max(0.0, min(x["end"], y["end"] + gap) - max(x["start"], y["start"] - gap))
    return min(1.0, hit / tot)


def _touch(a: list[dict], b: list[dict]) -> bool:
    return any(x["start"] <= y["end"] + LINK_GAP_S and y["start"] <= x["end"] + LINK_GAP_S for x in a for y in b)


def _settle(controller, q: dict, ans: str, pet_id: str | None, by: str, source_spans: list[dict], now: float, why: str,
            learn: bool = True) -> None:
    """Answer `q` on someone else's word (the partner answered the same visit, or the person fixed the night by hand).
    Learns from q's own readings only when that answer clearly covers them."""
    if q["status"] != "open":
        return
    readings = [r for x in q["spans"] for r in x.get("readings", [])]
    if ans == "yes":
        pet_id = pet_id if pet_id in q["pets"] else (q["pets"][0] if len(q["pets"]) == 1 else pet_id)
        if learn and pet_id and _covered(q["spans"], source_spans, LINK_GAP_S) >= LEARN_COVER:
            learn_pet(controller.store, pet_id, readings)
            q["learned"] = "pet"
    q.update(status="answered", answer=ans, answered_pet=pet_id if ans == "yes" else None, answered_at=now, answered_by=by, settled=why)
    for x in q["spans"]:
        x.pop("readings", None)
    controller.store.event(now, "pet_answer", q["person"], "app", question=q["id"], answer=ans,
                           pet=pet_id if ans == "yes" else None, by=by, settled=why)


def _link(controller, qs: list[dict], q: dict, now: float) -> None:
    """The same visit on the other side (same night, overlapping in time): link the two questions so one answer settles
    both. If the other side already said "yes, the pet" (or "not sure") about it, this one is settled at once."""
    for o in qs:
        if o is q or o["zone"] == q["zone"] or o["group"] != q["group"] or not _touch(o["spans"], q["spans"]):
            continue
        q.setdefault("linked", [])
        o.setdefault("linked", [])
        if o["id"] not in q["linked"]:
            q["linked"].append(o["id"])
        if q["id"] not in o["linked"]:
            o["linked"].append(q["id"])
        if o["status"] == "answered" and o.get("answer") in ("yes", "unsure") and q["status"] == "open":
            _settle(controller, q, o["answer"], o.get("answered_pet"), o["person"], o["spans"], now, "partner")


def settle_from_fix(controller, pid: str, stretches: list[dict], now: float) -> int:
    """"Fix this night" marked stretches as the pet (or as me): open questions for this person that those stretches
    cover are answered with it, and their linked questions on the other side too. Returns how many were settled."""
    store = controller.store
    qs = questions(store)
    n = 0
    for q in qs:
        if q["person"] != pid or q["status"] != "open":
            continue
        for who in ("pet", "me"):
            mine = [x for x in stretches if x.get("who") == who]
            if mine and _covered(q["spans"], mine, 5 * 60) >= 0.5:
                pet = next((x.get("pet") for x in mine if x.get("pet")), None)
                ans = "yes" if who == "pet" else "me"
                _settle(controller, q, ans, pet, pid, mine, now, "fix", learn=False)    # the fix itself already taught it (compare.learn_from_fix)
                if ans == "yes":
                    _propagate(controller, qs, q, now)
                n += 1
                break
    if n:
        _save(store, qs)
    return n


def _propagate(controller, qs: list[dict], q: dict, now: float) -> None:
    """q was just answered: settle its linked questions on the other side with the same answer ("yes" / "not sure";
    "it was me" speaks only for the person's own side)."""
    if q.get("answer") not in ("yes", "unsure"):
        return
    for o in qs:
        if o["id"] in (q.get("linked") or []) and o["status"] == "open":
            _settle(controller, o, q["answer"], q.get("answered_pet"), q["person"], q["spans"], now, "partner")


def open_for(store, pid: str, now: float) -> dict | None:
    """The one open question for this person (newest), dropping ones older than 3 days."""
    qs = questions(store)
    changed = False
    out = None
    for q in qs:
        if q["status"] == "open" and now - q["created"] > 3 * 86400:
            q["status"], changed = "expired", True
        elif q["status"] == "open" and q["person"] == pid:
            out = q
    if changed:
        _save(store, qs)
    return out


def _clock(ts: float, tz, style: str = "24") -> str:
    return fmt_clock(ts, style, tz)


def view(q: dict, household, tz, clock: str = "24") -> dict:
    pets = {p.id: p for p in household.pets}
    names = [pets[i].name for i in q["pets"] if i in pets]
    a, b = q["spans"][0]["start"], q["spans"][-1]["end"]
    when = f"{_clock(a, tz, clock)}–{_clock(b, tz, clock)}"
    who = names[0] if len(names) == 1 else "one of your pets"
    side = "your side of the bed" if q["zone"] in ("left", "right") else "the bed"   # asked of that side's sleeper
    them = names[0] if len(names) == 1 else "your pets"
    choices = ([{"answer": "yes", "pet": pets[i].id, "label": f"Yes, it was {pets[i].name}"}
                for i in q["pets"] if i in pets]
               + [{"answer": "me", "label": "No, it was me"}, {"answer": "unsure", "label": "Not sure"}])
    return {"id": q["id"], "zone": q["zone"], "kind": q["kind"], "status": q["status"], "answer": q["answer"],
            "title": f"Was {who} on the bed around {when}?", "who": who, "when": when,     # (who / when: voice.py rewords it)
            "detail": ((f"Something on {side} didn't seem like you." if q["kind"] == "pet"
                        else f"Something was on {side} while it was off.")
                       + f" Your answer helps tell you and {them} apart next time."),
            "start": a, "end": b, "spans": [{"start": s["start"], "end": s["end"], "evidence": s.get("evidence")} for s in q["spans"]],
            "choices": choices}


def answer(controller, qid: str, pid: str, ans: str, pet_id: str | None, now: float) -> dict:
    """Store the answer and learn from it. Raises KeyError for an unknown question, ValueError for a bad answer."""
    if ans not in ("yes", "me", "unsure"):
        raise ValueError("answer is yes, me or unsure")
    store = controller.store
    qs = questions(store)
    q = next((x for x in qs if x["id"] == qid and x["person"] == pid), None)
    if q is None:
        raise KeyError(qid)
    if q["status"] == "answered":
        return q
    readings = [r for s in q["spans"] for r in s.get("readings", [])]
    if ans == "yes":
        pet_id = pet_id or (q["pets"][0] if len(q["pets"]) == 1 else None)
        if pet_id not in q["pets"]:
            raise ValueError("which pet?")
        learn_pet(store, pet_id, readings)
        q["learned"] = "pet"
    elif ans == "me":
        widen_person(store, pid, readings)
        q["learned"] = "person"
    q.update(status="answered", answer=ans, answered_pet=pet_id if ans == "yes" else None, answered_at=now)
    spans_kept = [{"start": x["start"], "end": x["end"]} for x in q["spans"]]
    for x in q["spans"]:
        x.pop("readings", None)                       # learned from; the raw numbers aren't kept
    _propagate(controller, qs, {**q, "spans": spans_kept}, now)    # the same visit on the other side: settled too
    _save(store, qs)
    store.event(now, "pet_answer", pid, "app", question=qid, answer=ans, pet=pet_id if ans == "yes" else None)
    return q


def signature_summary(store, household) -> list[dict]:
    out = []
    sigs = pet_sigs(store)
    for p in household.pets:
        v = sig_view(sigs.get(p.id))
        out.append({"pet": p.id, "name": p.name, "learned": bool(v), "minutes": round(sigs.get(p.id, {}).get("n_hr", 0) / 2),
                    "hr": round(v["hr"][0]) if v and v.get("hr") else None,
                    "br": round(v["br"][0]) if v and v.get("br") else None})
    return out


# ---------- what has been learned about each pet (read-only, for the Pets section) ----------
RECENT_VISITS = 10


def _person_band(controller, p, now: float) -> dict | None:
    try:
        lr = controller.autopilot.learning(p, now)
        band = (controller.autopilot.baseline(p, now) or {}).get("band") if lr["have"] >= max(1, lr["need"]) else None
    except Exception:  # noqa: BLE001
        band = None
    return person_range(band, learned_range(controller.store, p.id)) if band else None


def pet_report(controller, now: float, mine: str | None = None) -> list[dict]:
    """Per pet: the learned signature (typical heart rate / breathing with spread, readings, stays, last update),
    whether it tells the pet from the people in the house, the latest confirmed visits ("Yes" answers, also those a
    partner's answer or a fixed night settled) and stays held while someone is away. `mine` = a person id: only that
    person's visits and held stays (the other side is theirs to see, like their nights); None = everyone's."""
    from . import away
    store, hh = controller.store, controller.household
    sigs = pet_sigs(store)
    qs = [q for q in questions(store) if q["status"] == "answered" and q.get("answer") == "yes" and (mine is None or q["person"] == mine)]
    out = []
    for p in hh.pets:
        raw = sigs.get(p.id) or {}
        v = sig_view(raw)
        sig = None
        if v:
            sig = {"hr": round(v["hr"][0]) if v.get("hr") else None, "hr_sd": round(v["hr"][1], 1) if v.get("hr") else None,
                   "br": round(v["br"][0]) if v.get("br") else None, "br_sd": round(v["br"][1], 1) if v.get("br") else None,
                   "readings": v["n"], "minutes": round(v["n"] / 2), "stays": raw.get("stays"), "updated": raw.get("updated")}
        # told apart from the people it could be mistaken for: their own learned range, else a sleeping adult's
        people = [x for x in hh.people if p.side in ("either", "both", x.zone)] or [None]
        same = None if not v else any(not distinct(v, _person_band(controller, x, now) if x else None) for x in people)
        visits = []
        for q in qs:
            if q.get("answered_pet") != p.id or not q["spans"]:
                continue
            sp = q["spans"]
            visits.append({"start": sp[0]["start"], "end": sp[-1]["end"], "zone": q["zone"],
                           "seconds": round(sum(max(0, s["end"] - s["start"]) for s in sp))})
        visits.sort(key=lambda x: x["start"], reverse=True)
        held = []
        for x in hh.people:
            if mine is None or x.id == mine:
                held += [s for s in (store.get(away._spans_key(x.id)) or []) if s.get("pet") == p.id]
        out.append({"id": p.id, "name": p.name, "learned": sig, "distinct": None if same is None else not same,
                    "visits": visits[:RECENT_VISITS],
                    "waiting": {"count": len(held), "next_at": min((s["end"] + away.LEARN_AFTER_S for s in held), default=None)},
                    "partial": mine is not None})
    return out


def forget_pet(store, household, pet_id: str) -> None:
    """Drop what has been learned about one pet: its signature, and its stays still waiting to be learned."""
    from . import away
    sigs = pet_sigs(store)
    if sigs.pop(pet_id, None) is not None:
        store.put("pet_sigs", sigs)
    for x in household.people:
        spans = store.get(away._spans_key(x.id)) or []
        keep = [s for s in spans if s.get("pet") != pet_id]
        if len(keep) != len(spans):
            store.put(away._spans_key(x.id), keep)


