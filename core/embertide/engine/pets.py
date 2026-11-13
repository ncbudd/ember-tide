"""Pets on the bed: recognise readings that are a cat or dog rather than a person, so they don't start a night,
don't end an out-of-bed hold, and don't feed Autopilot.

Evidence, most reliable first (all conservative: when in doubt it's a person):
- Bed scale / load cells: a side's weight (above the scale's learned empty-bed zero) well below the lightest person,
  and within reach of a pet's weight, is a pet, but only when something alive is there too (the bed strip or a
  radar sees it): a duvet, pillows, a weighted blanket or laundry weigh the same as a cat and are dead still.
  It must last PET_CONFIRM_S: a cat jumping on and straight off is "not sure yet", never a pet event.
  Person + pet on the scale is still a person (and a pet-sized jump that lasts while someone is in bed marks
  "pet with you" so restlessness from it isn't blamed on the sleeper).
- Bed strip heart rate (only when there are cats or small dogs: their resting heart rate, ~110-200 bpm, is far
  above a sleeping person's): occupied with a heart rate above 105 bpm for two minutes, with no scale or radar
  saying a person is there, is a pet. In the first half minute after the strip becomes occupied (heart rate not
  known yet, or high) it's "not sure yet" so a cat jumping up can't end an out-of-bed hold.
- Watch mismatch (engine/watch.py): the sleeper's own watch reads clearly differently from the pad (or counted
  footsteps) for several minutes: whoever is there isn't them. With a pet that may be on that side it's a pet.
- Outside the person's normal range (only once Autopilot's learning week produced one): on an otherwise empty side,
  heart rate or breathing far outside the sleeper's own range for most of 10 minutes is "probably <pet>".
- A learned pet signature (engine/petq.py, from "Yes" answers): readings clearly closer to the pet's signature than
  to the person's range, for most of 10 minutes.
- The sleeper is away (engine/away.py, not a guest's side): the strip occupied for 3 minutes is the pet ("you're away,
  so this is probably Biscuit"), unless a radar on that side sees someone for most of the stretch (it may be a person:
  left for the morning away check) or their own watch says it's them.
A watch that agrees with the pad ("it's me") switches off every heart-rate based pet guess.
Nothing here is stored about the pet except a short, non-personal event per side ("looked like Biscuit").
"""

from __future__ import annotations

PET_HR = 105            # bpm: above this, sustained, a strip reading is a cat / small dog
HR_CONFIRM_S = 120
SETTLE_S = 30
PET_CONFIRM_S = 60      # a pet-sized load must stay this long before it's a pet (a quick hop on and off isn't)
RANGE_WINDOW_S = 600    # outside-range / signature: judged over this long
RANGE_COVER_S = 480     # readings must cover at least this much of it
RANGE_SHARE = 0.8       # and this share of them must look like the pet
AWAY_CONFIRM_S = 180    # the sleeper away: lying there this long (the usual 3-minute "in bed" confirm) is the pet
PHONE_HOLD_S = 300      # the phone saying "not by the bed" must hold this long (before bedtime only)


class PetGuard:
    def __init__(self) -> None:
        self.state: dict[str, dict] = {}

    def assess(self, zone: str, now: float, household, side, weights: list[float], radar: list[float],
               person_kg: list[float], watch: str | None = None, rng: dict | None = None, sigs: dict | None = None,
               empty_side: bool = False, away: bool = False, phone: str | None = None, pre_start: bool = False,
               early: bool = False, crosstalk: str | None = None) -> dict | None:
        """side = the strip's SideState; weights = fresh kg readings from this side's scales; radar = votes (0/1)
        from presence devices on this side; person_kg = known body weights. None when pets don't apply.
        watch = the sleeper's watch verdict ("me", "not_me" once held long enough, or None); rng = the sleeper's
        learned range {hr: [lo, hi], br: [lo, hi]}; sigs = learned pet signatures {pet id: petq.sig_view};
        empty_side = this occupancy began on an otherwise empty side (not the sleeper back from a short trip);
        away = the side's sleeper is marked away (engine/away.py) and the side isn't lent to a guest;
        phone = their phone's "in" / "out" / None (phone_context.bedside_state); pre_start = their night isn't running;
        early = and it's before their usual bedtime (phone evidence alone only counts then);
        crosstalk = on an away side with someone on the other side: how the pad follows theirs ("tracks" / "apart" /
        "unknown", presence.py). Not "apart" means it may be their heartbeat bleeding across, not a pet."""
        pets = [p for p in getattr(household, "pets", []) if p.sleeps_on_bed != "no" and p.side in ("either", "both", zone)]
        if not pets or not getattr(household, "pets_ignore", True):
            self.state.pop(zone, None)
            return None
        st = self.state.setdefault(zone, {"scale_person": False, "base": None, "high_since": None, "occ_since": None,
                                          "pet_since": None, "with_since": None})
        st.setdefault("hist", [])
        heaviest = max(p.weight_kg for p in pets)
        lightest = min(person_kg) if person_kg else None
        out = {"suspected": False, "candidate": False, "with_person": False, "why": "", "pet": None, "scale_person": None}

        # ---- scale ----
        if weights:
            w = max(weights)
            if lightest:
                margin = max(8.0, 0.15 * lightest)
                limit = lightest - margin - (5.0 if st["scale_person"] else 0.0)        # hysteresis once a person
                person = w >= limit
            else:
                person = w >= max(30.0, heaviest * 1.3 + 5.0)
            st["scale_person"] = person
            out["scale_person"] = person
            if person:
                if st["base"] is None or w < st["base"]:
                    st["base"] = w
                jump = w - st["base"]
                match = _closest(pets, jump)
                if match and abs(jump - match.weight_kg) <= max(1.0, 0.3 * match.weight_kg) and jump >= 1.5:
                    st["with_since"] = st["with_since"] or now
                    if now - st["with_since"] >= PET_CONFIRM_S:
                        out.update(with_person=True, pet=match.name, evidence="scale",
                                   why=f"about {jump:.0f} kg more than when you got in: looks like {match.name} joined you")
                else:
                    st["with_since"] = None
            else:
                st["base"], st["with_since"] = None, None
                match = _closest(pets, w)
                alive = bool(side and side.occupied) or any(v >= 0.5 for v in radar)
                # near a pet's weight AND something alive there: bedding, laundry or drift on an empty bed is not the cat
                if (alive and match and 1.5 <= w <= heaviest * 1.3 + 3.0
                        and abs(w - match.weight_kg) <= max(1.5, 0.4 * match.weight_kg)):
                    st["pet_since"] = st["pet_since"] or now
                    if now - st["pet_since"] >= PET_CONFIRM_S:
                        out.update(suspected=True, pet=match.name, evidence="scale",
                                   why=f"{w:.0f} kg on the scale: {match.name}, not a person")
                    else:
                        out.update(candidate=True, pet=match.name, why="checking whether it's a person or a pet")
                    return out
                st["pet_since"] = None

        # ---- bed strip heart rate (cats / small dogs only) ----
        fast = [p for p in pets if p.fast_heart]
        occupied = bool(side and side.occupied)
        if occupied:
            st["occ_since"] = st["occ_since"] or now
            if not getattr(side, "echo", False) and (side.hr is not None or side.br is not None):
                h = st["hist"]
                if not h or now - h[-1][0] >= 10:
                    h.append((now, side.hr, side.br))
                while h and now - h[0][0] > RANGE_WINDOW_S:
                    h.pop(0)
        else:
            st["occ_since"], st["high_since"] = None, None
            st["hist"], st["range_on"] = [], False
        if not occupied or out["scale_person"]:
            st["away_radar"] = [0, 0]
            return out
        if away and watch != "me" and not getattr(side, "echo", False):
            # the side's sleeper is away (and it isn't lent to a guest): whatever lies there for a few minutes isn't
            # them. A radar that sees someone there for most of the stretch could be a person (a house guest, the
            # partner back early): then it's left alone for the morning away check
            ar = st.setdefault("away_radar", [0, 0])
            if radar:
                ar[0] += 1
                ar[1] += any(v >= 0.5 for v in radar)
            if not (ar[0] >= 3 and ar[1] >= 0.5 * ar[0]) and not (ar[0] < 3 and any(v >= 0.5 for v in radar)):
                pet = self._which(pets, side.hr, side.br, sigs)
                label = pet.name if pet else "a pet"
                if crosstalk in ("tracks", "unknown"):
                    # someone is on the other side and this pad is not (yet) clearly a different heartbeat: their own
                    # signal across the mattress (2026-11-12). Not a pet; it stays unlabelled
                    return out
                if now - st["occ_since"] >= AWAY_CONFIRM_S:
                    out.update(suspected=True, pet=pet.name if pet else None, evidence="away",
                               why=f"you're away, so this is probably {label}")
                else:
                    out.update(candidate=True, pet=pet.name if pet else None, why="checking whether it's a person or a pet")
                return out
        idr = self._identity(st, now, pets, side, watch, phone, pre_start, early, rng, sigs)
        if idr:
            out.update(idr)
            return out
        if any(v >= 0.5 for v in radar):
            return out                       # a radar sees someone there too: don't second-guess with heart rate alone
        if watch == "me":
            return out                       # the sleeper's own watch agrees with the pad: it's them
        if watch == "not_me" and not getattr(side, "echo", False):
            pet = self._which(pets, side.hr, side.br, sigs)
            label = pet.name if pet else "a pet"
            out.update(suspected=True, pet=pet.name if pet else None, evidence="watch",
                       why=f"your watch doesn't match the bed: probably {label}")
            return out
        ro = self._range(st, now, pets, rng, sigs, empty_side)
        if ro:
            out.update(ro)
            return out
        if not fast:
            return out
        hr = side.hr
        if hr is not None and hr > PET_HR:
            st["high_since"] = st["high_since"] or now
        elif hr is not None:
            st["high_since"] = None
        name = fast[0].name if len(fast) == 1 else "a pet"    # a heart rate can't tell two cats apart
        if st["high_since"] is not None and now - st["high_since"] >= HR_CONFIRM_S:
            out.update(suspected=True, pet=name if len(fast) == 1 else None, evidence="heart", why=f"heart rate {hr} bpm on the bed strip: {name}, not a person")
        elif (st["high_since"] is not None) or (now - st["occ_since"] < SETTLE_S and hr is None):
            out.update(candidate=True, pet=name if len(fast) == 1 else None, why="checking whether it's a person or a pet")
        return out


    def _identity(self, st, now, pets, side, watch, phone, pre_start, early, rng, sigs) -> dict | None:
        """Outside a running night: the sleeper's own watch and phone say whether it's them (a radar sees the dog as
        "someone", so it can't veto this). Watch "not me" alone is enough; the phone ("not by the bed") only before the
        usual bedtime, with the readings like the pet's signature or no learned range yet, for PHONE_HOLD_S."""
        if not pre_start or getattr(side, "echo", False) or watch == "me":
            st["phone_since"] = None
            return None
        pet = self._which(pets, side.hr, side.br, sigs)
        label = pet.name if pet else "a pet"
        if watch == "not_me":
            why = (f"your watch and phone say you're not in bed, so this is probably {label}" if phone == "out"
                   else f"your watch doesn't match the bed: probably {label}")
            return {"suspected": True, "pet": pet.name if pet else None, "evidence": "watch", "why": why}
        from .petq import closer_to_pet
        fits = rng is None or bool(sigs and closer_to_pet(side.hr, side.br, rng, sigs))
        if phone == "out" and early and fits:
            st["phone_since"] = st.get("phone_since") or now
            if now - st["phone_since"] >= PHONE_HOLD_S:
                return {"suspected": True, "pet": pet.name if pet else None, "evidence": "phone",
                        "why": f"your phone says you're not in bed, so this is probably {label}"}
            return {"candidate": True, "pet": pet.name if pet else None, "why": "checking whether it's a person or a pet"}
        st["phone_since"] = None
        return None

    def _which(self, pets, hr, br, sigs):
        """The pet it most likely is: the one whose learned signature fits; else the only pet; else the only
        slow-hearted (big) one when the heart rate is in a person's range. None = can't tell."""
        if sigs:
            from .petq import closer_to_pet
            pid = closer_to_pet(hr, br, None, sigs)
            hit = next((p for p in pets if p.id == pid), None)
            if hit:
                return hit
        if len(pets) == 1:
            return pets[0]
        slow = [p for p in pets if not p.fast_heart]
        if hr is not None and hr <= PET_HR and len(slow) == 1:
            return slow[0]
        return None

    def _range(self, st, now, pets, rng, sigs, empty_side) -> dict | None:
        """Outside the sleeper's learned range, or closer to a learned pet signature, for most of 10 minutes on an
        otherwise empty side. "Not sure yet" (unknown) while the last 2 minutes look that way."""
        if not empty_side or not (rng or sigs):
            return None
        from .petq import closer_to_pet, far_outside
        h = st["hist"]
        if not h:
            return None

        def judge(r):
            if rng and far_outside(r[1], r[2], rng):
                return "range"
            if sigs and closer_to_pet(r[1], r[2], rng, sigs):
                return "signature"
            return None
        marks = [(r[0], judge(r)) for r in h]
        last2 = [m for t, m in marks if now - t <= 120]
        recent_pet = len(last2) >= 3 and sum(1 for m in last2 if m) >= RANGE_SHARE * len(last2)
        share = sum(1 for _, m in marks if m) / len(marks)
        # once it's the pet, it stays the pet while at least half the readings still say so (no flip-flopping)
        need = 0.5 if st.get("range_on") else RANGE_SHARE
        st["range_on"] = now - h[0][0] >= RANGE_COVER_S and share >= need
        if st["range_on"]:
            kinds = [m for _, m in marks if m]
            ev = "signature" if kinds.count("signature") > kinds.count("range") else "range"
            hrs = sorted(r[1] for r in h if r[1] is not None)
            brs = sorted(r[2] for r in h if r[2] is not None)
            hr = round(hrs[len(hrs) // 2]) if hrs else None
            br = round(brs[len(brs) // 2]) if brs else None
            pet = self._which(pets, hr, br, sigs if ev == "signature" else None)
            label = pet.name if pet else "a pet"
            why = (f"heart rate {hr}, breathing {br}: far from your usual, probably {label}" if ev == "range"
                   else f"heart rate {hr}, breathing {br}: like {label}, not you")
            return {"suspected": True, "pet": pet.name if pet else None, "evidence": ev, "why": why}
        if recent_pet:
            return {"candidate": True, "why": "checking whether it's a person or a pet"}
        return None


def _closest(pets, kg: float):
    return min(pets, key=lambda p: abs(p.weight_kg - kg)) if pets else None


def pet_windows(events: list[dict], zone: str, until: float) -> list[tuple[float, float]]:
    """Intervals a pet was suspected on this side (alone or with the sleeper), from the pet events."""
    out, start = [], None
    for e in sorted((x for x in events if x["ts"] <= until and x.get("kind") in ("pet_suspected", "pet_cleared") and x.get("zone") == zone),
                    key=lambda x: x["ts"]):
        if e["kind"] == "pet_suspected" and start is None:
            start = e["ts"]
        elif e["kind"] == "pet_cleared" and start is not None:
            out.append((start, e["ts"]))
            start = None
    if start is not None:
        out.append((start, until))
    return out
