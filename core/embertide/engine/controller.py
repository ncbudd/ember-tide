"""The main loop: bed telemetry → presence → each person's night → commands, with safety limits."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from ..clock import Clock
from ..drivers.base import BedDriver, CommandError
from ..models import Household, Person
from ..store import Store
from ..units import clamp
from .night import NightEngine
from .presence import STATUS_TEXT, PresenceFilter

log = logging.getLogger(__name__)
TICK_S = 1.0
MIN_CMD_GAP_S = 8.0          # per zone, for automatic changes (user actions go immediately)
TELEMETRY_EVERY_S = 15.0     # virtual seconds


def sched_confirm(p: Person, e: NightEngine) -> float:
    from .rhythm import pick_rhythm
    rh = pick_rhythm(p.rhythms, e.night.date) if e.night else None
    from .night import confirm_min
    return (confirm_min(p, rh.schedule) if rh else 3) * 60


class Controller:
    def __init__(self, household: Household, driver: BedDriver, store: Store, clock: Clock, sources=None, demo: bool = False) -> None:
        self.sources = sources
        self.demo = demo
        self.household = household
        self.driver = driver
        self.store = store
        self.clock = clock
        self.presence = PresenceFilter()
        self.engines: dict[str, NightEngine] = {p.id: NightEngine(p, clock.tz) for p in household.people}
        self.home: dict[str, bool | None] = {p.id: None for p in household.people}      # manual switch (wins)
        self._person_errors: dict[str, int] = {}
        self.context: dict[str, dict] = {}                                                # from context devices
        self._ctx_home: dict[str, bool | None] = {}
        self._last_cmd: dict[str, float] = {}
        self._inflight: dict[str, asyncio.Task] = {}
        self._inflight_key: dict[str, tuple[bool, float | None]] = {}
        self._pending_force: set[str] = set()
        self._pending_desired: dict[str, tuple[Person, float | None]] = {}
        self._last_tel = 0.0
        self._task: asyncio.Task | None = None
        self.listeners: list[Callable[[dict], None]] = []
        self.last_snapshot: dict = {}
        self.fused: dict[str, dict] = {}
        self.leak_hold: dict | None = None
        self.notifier = None          # set by the app; tests run without one
        from .autopilot import Autopilot
        self.autopilot = Autopilot(self)
        self._restore()

    # ---------- persistence ----------
    def _restore(self) -> None:
        """Pick tonight up where it was after a restart (or a power cut): started, in-bed time, phase anchors, a
        flash or change, off/paused all come back from the store. Time the server was down doesn't count towards an
        out-of-bed hold (someone up when the power went is not "gone for good" when it comes back), and the bed's
        presence is judged afresh; until it reports, a running night carries on on the clock."""
        saved = dict(self.store.get("nights", {}) or {})
        self.leak_hold = dict(lh) if isinstance((lh := self.store.get("leak_hold")), dict) else None
        saved_at = saved.pop("_saved_at", None)
        down = max(0.0, self.clock.now_ts() - saved_at) if isinstance(saved_at, (int, float)) else 0.0
        from .night import Flash, NightState, Override
        for pid, d in saved.items():
            e = self.engines.get(pid)
            if not e or not isinstance(d, dict):
                continue
            f, o = d.pop("flash", None), d.pop("override", None)
            d.pop("events", None)
            try:
                e.s = NightState(**{k: v for k, v in d.items() if k in NightState.__dataclass_fields__})
                e.s.flash = Flash(**f) if f else None
                e.s.override = Override(**o) if o else None
            except TypeError:
                log.warning("could not restore night for %s", pid)
                continue
            if e.s.absent_since is not None and down > 0:
                e.s.absent_since += down
            if e.s.empty_on_since is not None and down > 0:
                e.s.empty_on_since += down
            e.s.unknown_since = None                         # presence is unknown afresh from now, not from before
        if down > 0:
            log.info("picked up tonight after %.0f min down", down / 60)

    def _persist(self) -> None:
        data: dict = {pid: e.s.to_dict() for pid, e in self.engines.items()}
        data["_saved_at"] = self.clock.now_ts()
        self.store.put("nights", data)

    def person(self, pid: str) -> Person:
        for p in self.household.people:
            if p.id == pid:
                return p
        raise KeyError(pid)

    def engine_for(self, pid: str) -> NightEngine:
        return self.engines[pid]

    def reload_household(self, household: Household) -> None:
        self.household = household
        for p in household.people:
            if p.id in self.engines:
                self.engines[p.id].person = p
            else:
                self.engines[p.id] = NightEngine(p, self.clock.tz)
        self.store.put("household", household.model_dump())

    # what the bed has seen on a side tonight (someone got in, the night started, they got up): it belongs to whoever
    # is lying on that side, so it moves to the person who now has it when two people swap sides
    SIDE_FIELDS = ("started", "in_bed", "presence_since", "absent_since", "unknown_since", "started_by", "out_since", "reanchor")

    def swap_sides(self) -> None:
        """Swap which side each of the two people sleeps on (POST /api/household/swap-sides).
        - plans, settings, history, a flash or a change made tonight stay with the person;
        - tonight's in-bed state stays with the side of the bed: usually the sides were simply set up the wrong way
          round, so the person really lying there takes over the night that started there (no out-of-bed hold on an
          empty side, no new wait on the occupied one);
        - a person's own devices (watch, phone, location) follow them, and so do the bedside dials (each dial's
          firmware already controls its own person, so its side label and Bluetooth beacon follow too);
        - bed sensors (radar, scale, pad) stay with their side of the bed."""
        from ..models import person_color
        from . import phone_context as PC
        a, b = self.household.people
        if self.sources is not None:
            self.sources.migrate(self.household.people)      # watches stored with only a side get their owner first
        for p in (a, b):   # colours follow the person, not the side
            p.settings.color = person_color(p)
        sa, sb = self.engines[a.id].s, self.engines[b.id].s
        if sa.key == sb.key:
            for f in self.SIDE_FIELDS:
                va, vb = getattr(sa, f), getattr(sb, f)
                setattr(sa, f, vb)
                setattr(sb, f, va)
        else:                                                  # different nights (shouldn't happen): start presence fresh
            for s in (sa, sb):
                s.presence_since = s.absent_since = None
        a.zone, b.zone = b.zone, a.zone
        self.reload_household(self.household)
        if self.sources is not None and self.sources.follow_owners(self.household.people):
            self.sources.save()
        flip = {"left": "right", "right": "left"}
        sides = self.store.get("dial_sides") or {}
        if sides:
            self.store.put("dial_sides", {k: flip.get(v, v) for k, v in sides.items()})
        names = {"Left dial": "Right dial", "Right dial": "Left dial"}
        self.store.put("dial_beacons", [{**x, "side": flip.get(x.get("side"), x.get("side")), "name": names.get(x.get("name"), x.get("name"))}
                                        for x in PC.dial_beacons(self.store)])
        self._persist()

    # ---------- presence fusion ----------
    def fuse(self, zone: str) -> bool | None:
        """Weighted vote of the bed's own (filtered) presence and any adopted presence/weight sensors."""
        bed = self.presence.present(zone)
        if self.sources is not None and hasattr(self.sources, "hint_side"):
            self._scale_hint(zone, bed)
        votes: list[tuple[str, float, float]] = []      # (signal, weight, value)
        w = self.sources.calibration.weights if self.sources else {"bed": 1.0}
        if bed is not None:
            votes.append(("bed", w.get("bed", 0.5), 1.0 if bed else 0.0))
        if self.sources:
            for key, v in self.sources.zone_signals(zone).items():
                cfg = self.sources.get(key.split(":", 1)[1])
                votes.append((key, self.sources.vote_weight(cfg) if cfg else w.get(key.split(":")[0], 0.3), v))   # each device's own weight
        # PHONE BY THE BED (phone_context.py): the person's phone lying by this side's dial at night, a weak hint
        from .phone_context import BEDSIDE_WEIGHT, bedside_vote
        bv = bedside_vote(self.store, self.household, zone, self.clock.now_ts())
        if bv is not None:
            votes.append(("phone:bedside", BEDSIDE_WEIGHT, bv))
        votes, crosstalk = self._crosstalk(zone, votes)
        ep = self._episode(zone)
        wv = self._watch_vote(zone, votes, ep)
        if wv is not None:
            votes.append(wv)
        pg = self._pet_check(zone, votes, ep)
        if ep is not None and pg:
            if pg.get("suspected"):
                ep["pet_ticks"] = ep.get("pet_ticks", 0) + 1
                ep["evidence"] = pg.get("evidence") or ep.get("evidence")
                hit = next((p for p in self.household.pets if p.name == pg.get("pet")), None)
                if hit:
                    ep["pet_id"] = hit.id
            if pg.get("with_person"):
                ep["with_person"] = True
        # what the radar zones on this side said, before a pet guard zeroes them (_episode's "radar saw nobody" count)
        self.__dict__.setdefault("_radar_raw", {})[zone] = [v for k, _, v in votes if k.startswith("presence:") and v is not None]
        if pg and pg["suspected"]:
            # the occupancy is explained by a pet: the strip and the scale (and a radar that sees the pet) say nothing
            # about a person; a radar saying empty stays as it is
            votes = [(k, wt, 0.0) for k, wt, v in votes]
        total = sum(x[1] for x in votes)
        if not (pg and pg["suspected"]) and self._watch_pending(zone, votes, ep):
            # the sleeper's watch is on and reporting, but hasn't said yet whether this is them: wait a few minutes
            # (nothing starts, no out-of-bed hold ends), at most WATCH_WAIT_S from when the side got occupied
            self.fused[zone] = {"present": None, "score": None, "votes": [], "watch": {**{k: ep["ident"].get(k) for k in ("state", "watch", "pad", "diff", "why")}, "held": "checking"}}
            return None
        if pg and pg["candidate"] and not pg["suspected"]:
            # not sure yet whether it's a person or the cat: unknown, so nothing starts and no out-of-bed hold ends
            self.fused[zone] = {"present": None, "score": None, "votes": [], "pet": pg}
            return None
        # what's left must carry some weight: a lone motion sensor (0.15) after the strip and radar dropped out
        # is a hint, not an answer; then it's unknown and the schedule fallback takes over
        if not votes or total < 0.25:
            self.fused[zone] = {"present": None, "score": None, "votes": []}
            return None
        score = sum(x[1] * x[2] for x in votes) / total
        present = score >= 0.5
        self.fused[zone] = {"present": present, "score": round(score, 2), "votes": [{"signal": k, "weight": round(wt, 2), "value": v} for k, wt, v in votes]}
        if crosstalk:
            self.fused[zone]["crosstalk"] = crosstalk
        if pg is not None:
            self.fused[zone]["pet"] = pg
        if ep is not None and ep.get("ident"):
            self.fused[zone]["watch"] = {k: ep["ident"].get(k) for k in ("state", "watch", "pad", "diff", "why")}
            self.fused[zone]["watch"]["held"] = "not_me" if ep.get("not_me") else ("me" if ep.get("me_seen") else None)
        return present

    def _personal_signals(self, zone: str) -> dict | None:
        """The person's own phone and watch for the night engine (lie-in only): do they vouch for this side, and do the
        watch and the pad clearly disagree? None when presence is unknown or the person is moving about."""
        f = self.fused.get(zone) or {}
        if f.get("present") is None or self.active_since is not None:
            return None
        from . import watch as W
        voting = any(v["signal"] in ("watch:me", "phone:bedside") and (v["value"] or 0) >= 0.5 for v in f.get("votes") or [])
        w = f.get("watch") or {}
        diff = w.get("diff")
        return {"voting": voting, "disagree": w.get("state") == "not_me" or (diff is not None and diff > W.ME_BPM)}

    # ---------- occupancy episodes (watch identity, pet questions) ----------
    EPISODE_GAP_S = 120          # the strip empty this long ends an episode
    EPISODE_MAX_S = 4 * 3600     # a very long one is handed to the questions in pieces

    def _episode(self, zone: str) -> dict | None:
        """The current stretch of the bed strip saying someone (not an echo) lies on this side, with what's needed
        to judge who it is. Ended episodes go to the pet questions (engine/petq.py)."""
        now = self.clock.now_ts()
        eps = self.__dict__.setdefault("_eps", {})
        last_end = self.__dict__.setdefault("_ep_last_end", {})
        on = self.presence.present(zone) is True
        ep = eps.get(zone)
        people = [p for p in self.household.people if p.zone == zone]
        e = self.engines.get(people[0].id) if people else None
        if ep is not None and (not on and now - ep["last_on"] >= self.EPISODE_GAP_S or now - ep["since"] >= self.EPISODE_MAX_S):
            self._episode_done(zone, ep, now)
            eps.pop(zone, None)
            last_end[zone] = ep["last_on"]
            ep = None
            if on:                                       # ran long: carry on as a fresh piece
                ep = eps[zone] = self._new_episode(zone, now, e, last_end.get(zone), cont=True)
        if ep is None:
            if not on:
                return None
            ep = eps[zone] = self._new_episode(zone, now, e, last_end.get(zone))
        if on:
            ep["last_on"] = now
            if self.active_since is not None:
                ep["active"] = True                      # not a stretch to ask "was that the pet?" about
            s = self.presence.sides.get(zone)
            ep["ticks"] += 1
            # what a radar zone on this side says meanwhile: a pad lit while the radar sees nobody is the partner's
            # weight or movement leaking across, not someone (or the pet) lying there. Read from the votes as they came in
            # (last tick), not as fused: a suspected pet zeroes them there, and a radar that sees the pet isn't "nobody"
            radar = (self.__dict__.get("_radar_raw") or {}).get(zone) or []
            if radar:
                ep["radar_ticks"] = ep.get("radar_ticks", 0) + 1
                if max(radar) < 0.5:
                    ep["radar_empty"] = ep.get("radar_empty", 0) + 1
            if e is not None and e.s.in_bed and e.s.started:
                ep["in_bed_ticks"] += 1
            if s is not None and s.hr:
                pad = ep["pad"]
                if not pad or now - pad[-1][0] >= 5:
                    pad.append((now, s.hr))
                while pad and now - pad[0][0] > 1500:
                    pad.pop(0)
            if s is not None and (s.hr or s.br):
                r = ep["readings"]
                if not r or now - r[-1][0] >= 30:
                    r.append([round(now), s.hr, s.br])
        return ep

    def _new_episode(self, zone: str, now: float, e, last_end: float | None, cont: bool = False) -> dict:
        # "otherwise empty": not the sleeper coming back from a short trip (in bed, side empty under 20 min)
        back_soon = bool(e and e.s.in_bed and e.s.started and (last_end is None or now - last_end < 1200))
        return {"since": now, "last_on": now, "ticks": 0, "in_bed_ticks": 0, "pet_ticks": 0, "pad": [], "readings": [],
                "empty_side": not back_soon and not cont, "me_seen": False, "not_me": False, "nm_since": None,
                "ident": None, "ident_at": 0.0}

    def _episode_done(self, zone: str, ep: dict, now: float) -> None:
        try:
            from .petq import consider
            consider(self, zone, ep, now)
        except Exception:  # noqa: BLE001
            log.exception("pet question failed")

    # live identity-vote weights by "Which to trust more" (wearable / balanced / bed)
    WATCH_ME_W = {"wearable": 0.3, "balanced": 0.25, "bed": 0.15}
    WATCH_NOT_ME_W = {"wearable": 0.7, "balanced": 0.6, "bed": 0.4}
    NOT_ME_HOLD_S = 300
    NOT_ME_EARLY_S = 45 * 60     # "not me" can only take hold in the first 45 min of a stretch: a settled sleeper stays

    WATCH_WAIT_S = 8 * 60

    def _watch_pending(self, zone: str, votes: list, ep: dict | None) -> bool:
        if ep is None or not ep.get("empty_side") or ep.get("me_seen") or ep.get("not_me") or self.active_since is not None:
            return False
        st, age = (ep.get("ident") or {}).get("state"), self.clock.now_ts() - ep["since"]
        # still comparing: up to 8 min; already looking like "not me" but not held long enough yet: up to 15 min
        if not ((st == "unsure" and age < self.WATCH_WAIT_S) or (st == "not_me" and age < 2 * self.WATCH_WAIT_S - 60)):
            return False
        # a radar or scale already sees a person. (With a pet that may lie on this side, a radar seeing "someone" may be
        # the pet, so only a scale ends the wait then)
        pets_here = [x for x in getattr(self.household, "pets", []) or [] if x.sleeps_on_bed != "no" and x.side in ("either", "both", zone)]
        if any(v >= 0.5 for k, _, v in votes if k.startswith(("weight:",) if pets_here else ("presence:", "weight:"))):
            return False
        p = next((x for x in self.household.people if x.zone == zone), None)
        return bool(p and not self.engines[p.id].s.started)

    def _watch_vote(self, zone: str, votes: list, ep: dict | None) -> tuple[str, float, float] | None:
        """The sleeper's own watch: agrees with the pad → a small "in bed" vote; clearly different (held 5 min, early in
        the stretch, never after it agreed) → a "not me" vote. Nothing when the watch is quiet. A scale or radar that
        sees a person always outvotes "not me" (the vote is left out)."""
        if ep is None or zone in self.household.guest:
            return None
        p = next((x for x in self.household.people if x.zone == zone), None)
        if p is None:
            return None
        if self.active_since is not None:
            # moving about, heart racing, maybe two people on one side: the watch and the pad won't line up and
            # that means nothing. No new verdict, and a "not me" isn't started from it
            ep["nm_since"] = None
            return None
        now = self.clock.now_ts()
        from . import watch as W
        if now - ep["ident_at"] >= 15 or ep["ident"] is None:
            hr, steps = W.recent(self.store, p.id, now)
            ep["ident"] = W.identity(hr, ep["pad"], now, steps) if hr else {"state": "stale", "why": "nothing from the watch lately"}
            ep["ident_at"] = now
            st = ep["ident"]["state"]
            if st == "me":
                ep["me_seen"], ep["not_me"], ep["nm_since"] = True, False, None
            elif st == "not_me" and not ep["me_seen"]:
                ep["nm_since"] = ep["nm_since"] or now
                held = now - ep["nm_since"] >= self.NOT_ME_HOLD_S or (ep["ident"].get("span_s") or 0) >= 360 or ep["ident"].get("pairs", 0) == 0
                if held and (ep["not_me"] or now - ep["since"] <= self.NOT_ME_EARLY_S):
                    ep["not_me"] = True
            elif st == "unsure":
                ep["nm_since"] = None
        trust = getattr(p.settings.autopilot, "trust_more", "wearable")
        if ep["ident"]["state"] == "me":
            return ("watch:me", self.WATCH_ME_W.get(trust, 0.25), 1.0)
        if ep["not_me"]:
            if any(v >= 0.5 for k, _, v in votes if k.startswith(("presence:", "weight:"))):
                ep["outvoted"] = True
                return None
            return ("watch:me", self.WATCH_NOT_ME_W.get(trust, 0.6), 0.0)
        return None

    def _crosstalk(self, zone: str, votes: list) -> tuple[list, list[str]]:
        """Two single-zone sensors, one by each nightstand (group layout "two_sensors"), each see a little of the other
        side: the partner turning over can set off both. When both fire but this side's stronger signals (the bed
        strip, a scale) all say empty, this side's sensor is most likely seeing the partner, so its vote is left out.
        With no strip or scale to go by, or with either saying someone's here, nothing changes."""
        src = self.sources
        strong = [v for k, _, v in votes if k == "bed" or k.startswith("weight:")]
        if not src or not strong or any(v >= 0.5 for v in strong):
            return votes, []
        other = "right" if zone == "left" else "left"
        drop: list[str] = []
        for k, _, v in votes:
            if not k.startswith("presence:") or v < 0.5:
                continue
            cfg = src.get(k.split(":", 1)[1])
            if not cfg or not cfg.group or cfg.params.get("layout") != "two_sensors":
                continue
            partner = next((c for c in src.configs if c.group == cfg.group and c.zone == other and c.enabled), None)
            if partner and (src.vote_value(partner) or 0) >= 0.5:
                drop.append(k)
        return [x for x in votes if x[0] not in drop], drop

    def _scale_hint(self, zone: str, bed: bool | None) -> None:
        """Tell the bed scales on this side what the other signals know (engine/bedscale.py): confirmed empty lets a
        scale re-zero on extra bedding or laundry; a running night in bed lets it learn the sleeper's weight."""
        src = self.sources
        others = [v for k, v in src.zone_signals(zone).items() if not k.startswith("weight:")]
        pet = bool((self.__dict__.get("_pet_on") or {}).get(zone)) or bool(((self.fused.get(zone) or {}).get("pet") or {}).get("candidate"))
        if pet or bed is True or any(v >= 0.5 for v in others):
            empty = False
        elif bed is False or others:
            empty = True
        else:
            empty = None                                     # nothing but the scale itself: it decides on its own
        people = [p for p in self.household.people if p.zone == zone]
        prev = (self.fused.get(zone) or {}).get("present")
        in_bed = bool(prev and not pet and any(self.engines[p.id].s.started for p in people if p.id in self.engines))
        kg = [float(src.calibration.person_kg[p.id]) for p in people if src.calibration.person_kg.get(p.id)]
        src.hint_side(zone, empty, in_bed, kg)

    def _away_zones(self, now: float) -> set[str]:
        """Sides whose sleeper is marked away (not lent to a guest), and only while the other side's person isn't away."""
        from . import away as away_mod
        out = set()
        for p in self.household.people:
            if p.zone in self.household.guest:
                continue
            try:
                if away_mod.is_away(self.store, p.id, now):
                    out.add(p.zone)
            except Exception:  # noqa: BLE001
                pass
        return out if len(out) < len(self.household.people) else set()

    def _hold_watch(self, now: float) -> None:
        """Out-of-bed holds where the person's own radar still saw them (a misread, e.g. the echo filter) are noted as an
        `up_misread` event when the hold ends, so the morning "up N×" leaves them out (summary.py)."""
        w = self.__dict__.setdefault("_holds", {})
        for p in self.household.people:
            e = self.engines[p.id]
            h = w.get(p.id)
            if e.s.absent_since is not None and e.s.started:
                rad = (self.__dict__.get("_radar_raw") or {}).get(p.zone) or []
                if h is None or h["from"] != e.s.absent_since:
                    h = w[p.id] = {"from": e.s.absent_since, "ticks": 0, "seen": 0}
                if rad:
                    h["ticks"] += 1
                    h["seen"] += any(v >= 0.5 for v in rad)
            elif h is not None:
                w.pop(p.id)
                if h["ticks"] >= 4 and h["seen"] >= 0.5 * h["ticks"]:
                    self.store.event(now, "up_misread", p.id, "radar", since=h["from"], seen=h["seen"], ticks=h["ticks"])

    def _pet_hints(self, zone: str, votes: list, ep: dict | None) -> dict:
        """What the pet guard may use beyond the strip and scales: the sleeper's watch verdict, their learned range
        (only once the learning week produced one) and the learned pet signatures."""
        out: dict = {"watch": None, "rng": None, "sigs": None, "empty_side": bool(ep and ep.get("empty_side")), "away": False}
        p = next((x for x in self.household.people if x.zone == zone), None)
        if p is None:
            return out
        if zone not in self.household.guest:
            # the side's sleeper is away (engine/away.py): whatever lies there for a few minutes isn't them (pets.py)
            from . import away as away_mod
            try:
                out["away"] = away_mod.is_away(self.store, p.id, self.clock.now_ts())
                if out["away"] and zone in self.presence.away_sides:
                    # the other side has someone, and does the away side's pad follow theirs? (pets.py: crosstalk, not a pet)
                    out["crosstalk"] = self.presence.xt if self.presence.partner_near(zone) else None
            except Exception:  # noqa: BLE001
                out["away"] = False
        # outside a running night (by day, in the evening before it starts): their watch and phone may say whether it's
        # them (pets.py). Phone evidence only counts before the usual bedtime, to hold off an EARLY start
        e = self.engines.get(p.id)
        running = bool(e and e.s.started and e.s.state != "done")
        out["pre_start"] = not running
        n = e.night if e else None
        now_ = self.clock.now_ts()
        out["early"] = not running and not (n is not None and n.bedtime <= now_ < n.end)
        try:
            from .phone_context import bedside_state, latest
            out["phone"] = bedside_state(self.store, latest(self.store, p.id), zone, now_) if not running else None
        except Exception:  # noqa: BLE001
            out["phone"] = None
        if ep is not None:
            trust = getattr(p.settings.autopilot, "trust_more", "wearable")
            if (ep.get("ident") or {}).get("state") == "me" or ep.get("me_seen"):
                out["watch"] = "me"
            elif ep.get("not_me") and self.WATCH_NOT_ME_W.get(trust, 0.6) > 0.5 and (not ep.get("outvoted") or not running):
                # (before the night: a radar that sees "someone" may be seeing the dog, so it doesn't outvote the watch)
                out["watch"] = "not_me"
        from . import petq
        now = self.clock.now_ts()
        sigs = {}
        for pet_id, s in petq.pet_sigs(self.store).items():
            v = petq.sig_view(s)
            if v:
                sigs[pet_id] = v
        out["sigs"] = sigs or None
        try:
            lr = self.autopilot.learning(p, now)
            band = (self.autopilot.baseline(p, now) or {}).get("band") if lr["have"] >= max(1, lr["need"]) else None
        except Exception:  # noqa: BLE001
            band = None
        out["rng"] = petq.person_range(band, petq.learned_range(self.store, p.id)) if band else None
        return out

    def _pet_check(self, zone: str, votes: list, ep: dict | None = None) -> dict | None:
        """Pets on the bed (see engine/pets.py): returns the assessment and records non-personal start/end events."""
        hh = self.household
        if not getattr(hh, "pets", None):
            self.__dict__.pop("_pet_on", None)
            return None
        now = self.clock.now_ts()
        if self.active_since is not None:
            # people awake and moving: a fast heart on the strip is a person's, not the cat's. Forget what the guard
            # was building up, so it starts afresh once they've settled
            guard = self.__dict__.get("_pets")
            if guard is not None:
                guard.state.pop(zone, None)
            on = self.__dict__.setdefault("_pet_on", {})
            if on.get(zone):
                self.store.event(now, "pet_cleared", None, "pets", zone=zone)
            on[zone] = False
            return None
        from .pets import PetGuard
        guard = self.__dict__.setdefault("_pets", PetGuard())
        now = self.clock.now_ts()
        weights: list[float] = []
        radar: list[float] = []
        person_kg: list[float] = []
        if self.sources:
            for cfg in self.sources.configs:
                if not cfg.enabled or cfg.zone != zone:
                    continue
                l = self.sources.latest.get(cfg.id) or {}
                if cfg.role == "weight" and l.get("value") is not None and now - (l.get("ts") or 0) <= 300:
                    weights.append(float(self.sources.net_kg(cfg, float(l["value"]))))   # above the empty-bed zero
            radar = [v for k, wt, v in votes if k.startswith("presence:")]
            person_kg = [float(v) for v in self.sources.calibration.person_kg.values() if v]
        pg = guard.assess(zone, now, hh, self.presence.sides.get(zone), weights, radar, person_kg,
                          **self._pet_hints(zone, votes, ep))
        on = self.__dict__.setdefault("_pet_on", {})
        active = bool(pg and (pg["suspected"] or pg["with_person"]))
        if active and not on.get(zone):
            self.store.event(now, "pet_suspected", None, "pets", zone=zone, pet=pg["pet"], why=pg["why"], with_person=pg["with_person"],
                             evidence=pg.get("evidence"))
        elif not active and on.get(zone):
            self.store.event(now, "pet_cleared", None, "pets", zone=zone)
        on[zone] = active
        return pg

    # ---------- loop ----------
    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        self._persist()

    async def _run(self) -> None:
        n = 0
        while True:
            try:
                await self.tick()
                n += 1
                if n % 10 == 0:
                    self._persist()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("tick failed")
            await asyncio.sleep(TICK_S)

    async def tick(self, force: set[str] | None = None) -> dict:
        now = self.clock.now_ts()
        frame = self.driver.latest()
        online = bool(frame and getattr(frame, "online", True))
        # reporting and done starting up (sensor codes 1 / 255 = initializing / invalid while it boots)
        ready = online and any(z.sensor.status not in (None, 1, 255) for z in frame.zones.values())
        if ready and not self.__dict__.get("_was_ready", True):
            # the bed is back (after a power cut, a reboot or the broker being down): whatever it ignored while away
            # goes out at once, not after the backed-off retry gap
            self.__dict__.pop("_unconfirmed", None)
            # and the time it could not be reached is not time it ignored us: "isn't taking changes" is only said about
            # a bed that was reachable and still did not obey (it would otherwise follow "lost contact" on the way back)
            self.__dict__.pop("unconfirmed_since", None)
            self._last_cmd.clear()
        self._was_ready = ready
        # a side whose sleeper's night is running in bed: if both pads come back at once with the same readings
        # (restart, bed reboot), that side is the real one and the other the echo
        self.presence.prefer = {p.zone for p in self.household.people
                                if self.engines[p.id].s.started and self.engines[p.id].s.absent_since is None
                                and self.engines[p.id].s.state == "rhythm"}
        self.presence.pet_sides = {z for z, f in self.fused.items() if (f.get("pet") or {}).get("suspected")}
        # (a radar that skips a reading keeps its last verdict for 10 minutes: it still exists, it is not "no radar")
        rl = self.__dict__.setdefault("_radar_last", {})
        for z, vs in (self.__dict__.get("_radar_raw") or {}).items():
            if vs:
                rl[z] = (now, any(v >= 0.5 for v in vs))
        self.presence.radar = {z: v for z, (t, v) in rl.items() if now - t <= 600}
        self.presence.away_sides = self._away_zones(now)
        if frame:
            self.presence.update(now, {z: f.sensor for z, f in frame.zones.items()})
        guest_zones = set(self.household.guest)
        self._active_tick(now, guest_zones)
        if self.sources is not None:
            self.sources.paused_zones = guest_zones          # nothing from a guest's side is stored
        try:
            self._settle_sample(now)
        except Exception:  # noqa: BLE001
            log.exception("settle sample failed")
        if self._check_leak(now):
            # Force both sides off immediately on the tick a leak starts, bypassing command backoff.
            force = {p.id for p in self.household.people}
        for p in self.household.people:
            # one person's failure (a bad setting, a broken source) must not stop the other side's update
            try:
                await self._tick_person(p, now, frame, guest_zones, force)
            except Exception:  # noqa: BLE001
                log.exception("tick failed for %s", p.id)
                self._person_errors[p.id] = self._person_errors.get(p.id, 0) + 1
            else:
                self._person_errors.pop(p.id, None)
        try:
            self._hold_watch(now)
        except Exception:  # noqa: BLE001
            log.exception("hold watch failed")
        if frame and online and now - self._last_tel >= TELEMETRY_EVERY_S:
            # only while the bed is reporting: a frozen last frame (bed off, broker down) is not history
            self._last_tel = now
            rows = []
            for p in self.household.people:
                if p.zone in self.household.guest:
                    continue                                  # a guest's night is not recorded
                z = frame.zones.get(p.zone)
                s = self.presence.sides.get(p.zone)
                e = self.engines[p.id]
                if z:
                    fz = self.fused.get(p.zone, {}).get("present")
                    rows.append((now, p.zone, int(bool(z.on)), z.setpoint_c, z.measured_c,
                                 None if fz is None else int(fz),
                                 s.hr if s else None, s.br if s else None, s.status if s else None, e.s.target_c, e.s.phase_id,
                                 None if s is None or s.occupied is None else int(bool(s.occupied and not s.echo)), p.id))
            self.store.telemetry(rows)
        try:
            self.autopilot.tick(now)
        except Exception:  # noqa: BLE001
            log.exception("autopilot failed")
        snap = self.snapshot()
        if self.notifier is not None:
            try:
                self.notifier.observe(now, snap)
            except Exception:  # noqa: BLE001
                log.exception("notifications failed")
        for fn in list(self.listeners):
            try:
                fn(snap)
            except Exception:  # noqa: BLE001
                pass
        return snap

    async def _tick_person(self, p: Person, now: float, frame, guest_zones: set[str], force: set[str] | None) -> None:
        e = self.engines[p.id]
        present = self.fuse(p.zone)
        if p.zone in guest_zones:
            target = None if self.leak_hold else self._guest_tick(p, e, present, now)
            if self.leak_hold:
                e.s.target_c = None
            if frame:
                z = frame.zones.get(p.zone)
                forced = bool((force and p.id in force) or (self.leak_hold and z and z.on))
                await self._apply(p, target, frame, forced=forced)
            return
        # the extras (phone/calendar context, alarm, room) are optional: if one breaks, the night still runs on
        # what is left instead of the side being stuck at its last setting
        manual = self.home.get(p.id)
        home = manual
        try:
            from .context import person_context
            ctx = person_context(self.sources, self.store, p, now)
            self.context[p.id] = ctx.to_dict()
            home = manual if manual is not None else ctx.home
            if home != self._ctx_home.get(p.id, None) and (p.id in self._ctx_home or home is not None):
                self.store.event(now, "home_changed", p.id, "manual" if manual is not None else "context",
                                 home=home, why="set by hand" if manual is not None else ctx.why)
            self._ctx_home[p.id] = home
            e.busy_until = ctx.busy_until
            e.follow_alarm(now, ctx.next_alarm, ctx.alarm_reported)    # the warm wake lines up with the phone's alarm
        except Exception:  # noqa: BLE001
            log.exception("context failed for %s", p.id)
        # away for a while (engine/away.py): back when they use their side or the end date passes
        from . import away as away_mod
        try:
            away_mod.tick(self, p, now, list(e.s.events))
            e.away = away_mod.is_away(self.store, p.id, now)
        except Exception:  # noqa: BLE001
            log.exception("away check failed for %s", p.id)
        try:
            e.start_gate = self._start_gate(p, e, now)
        except Exception:  # noqa: BLE001
            e.start_gate = None
            log.exception("start gate failed for %s", p.id)
        try:
            self._room_inputs(p, e, now, frame)
        except Exception:  # noqa: BLE001
            log.exception("room inputs failed for %s", p.id)
        try:
            from .sleepsign import SleepSigns
            signs = self.__dict__.setdefault("_sleep_signs", SleepSigns())
            e.sleep_sign = signs.sign(self, p, e, now, (self.context.get(p.id) or {}).get("lights_on"))
        except Exception:  # noqa: BLE001
            e.sleep_sign = None                              # no sign: bedtime is still the backstop
            log.exception("sleep signs failed for %s", p.id)
        e.personal = self._personal_signals(p.zone)
        target = e.tick(now, present, home, active=self.active_since)
        if self.leak_hold:
            # Keep both sides off during leak_hold regardless of rhythms, flashes, overrides or Autopilot.
            e.s.flash = None
            e.s.override = None
            e.s.target_c = None
            target = None
        try:
            self._room_shortfall(p, e, now, frame)
        except Exception:  # noqa: BLE001
            log.exception("room check failed for %s", p.id)
        # write tonight's events from a copy: one that can't be written (a locked database) is logged and dropped, so it
        # can't stop the others, the next ticks or the bed commands below
        evs, e.s.events = list(e.s.events), []
        for ev in evs:
            d = dict(ev)
            try:
                self.store.event(d.pop("ts"), d.pop("kind"), d.pop("person", p.id), d.pop("source", None), **d)
            except Exception:  # noqa: BLE001
                log.exception("could not record %s for %s", ev.get("kind"), p.id)
        if frame:
            z = frame.zones.get(p.zone)
            forced = bool((force and p.id in force) or (self.leak_hold and z and z.on))
            await self._apply(p, target, frame, forced=forced)

    def _check_leak(self, now: float) -> bool:
        """Trip an emergency hold on both sides if any enabled leak sensor reads wet; returns True when newly triggered."""
        c = la(now) if (la := getattr(self.sources, "active_leak", None)) else None   # source managers without leak sensors
        if self.leak_hold:
            wet = c is not None
            if bool(self.leak_hold.get("wet")) != wet:
                self.leak_hold["wet"] = wet
                self.store.put("leak_hold", self.leak_hold)
            return False
        if c is None:
            return False
        self.leak_hold = {"since": float(now), "sensor": c.name, "sensor_id": c.id, "wet": True}
        self.store.put("leak_hold", self.leak_hold)
        self.store.event(now, "leak_detected", None, c.id, sensor=c.name, sensor_id=c.id)
        for e in self.engines.values():
            e.s.flash = None
            e.s.override = None
            e.s.target_c = None
        for g in self.household.guest.values():
            g.on = False
        self._persist()
        return True

    def clear_leak_hold(self, source: str = "app", actor: str | None = None) -> tuple[bool, str | None]:
        """Clear leak_hold once acknowledged by a person, unless an enabled leak sensor is still reading wet."""
        now = self.clock.now_ts()
        c = la(now) if (la := getattr(self.sources, "active_leak", None)) else None   # source managers without leak sensors
        if c is not None:
            return False, f"{c.name} still reports water. Dry or disable the sensor before turning the bed back on."
        hold = self.leak_hold
        if not hold:
            return True, None
        self.leak_hold = None
        self.store.put("leak_hold", None)
        self.store.event(now, "leak_cleared", actor, source, sensor=hold.get("sensor"), sensor_id=hold.get("sensor_id"))
        self._persist()
        return True, None

    # ---------- settling in: who is really in bed (radar zones + the phone going on the charger) ----------
    SETTLE_S = 600               # the first ~10 minutes after lying down decide whether this side's night starts
    SETTLE_SHARE = 0.6           # the radar must back it for this share of that time
    SETTLE_MIN_S = 420           # and have watched at least this long (from lying down, not on top of other waits)
    SETTLE_MIN_N = 8             # with at least this many radar samples
    OFFER_START_S = 40 * 60      # lying there this long with the radar still not backing it: offer to start (notify.py)
    CHARGER_EARLY_S = 1200       # the phone plugged in up to 20 min before lying down still counts

    def _radar(self, zone: str) -> float | None:
        """This side's bed-radar zone right now (1 = someone there, 0 = nobody), None without a working radar."""
        src = self.sources
        if src is None or not hasattr(src, "configs"):
            return None
        vals = [src.vote_value(c) for c in src.configs
                if c.zone == zone and c.enabled and c.kind == "radar" and c.role == "presence"]
        vals = [v for v in vals if v is not None]
        return max(vals) if vals else None

    def _settle_sample(self, now: float) -> None:
        hist = self.__dict__.setdefault("_settle_hist", {"left": [], "right": []})
        a, b = self._radar("left"), self._radar("right")
        for zone, own, other in (("left", a, b), ("right", b, a)):
            h = hist.setdefault(zone, [])
            h.append((now, own, other))
            while h and now - h[0][0] > self.SETTLE_S + 120:
                h.pop(0)

    def _charger(self, p, since: float, now: float) -> bool:
        """Their phone went on the charger around the time they lay down and still is (the phone app's charging share).
        Where the phone is isn't checked: it backs a pad that is already lit on their side."""
        from .phone_context import STALE, latest
        cur = latest(self.store, p.id) or {}
        cs = cur.get("charging_since")
        ts = (cur.get("ts") or {}).get("charging") or 0
        return bool(cs and (cur.get("data") or {}).get("charging") and now - ts <= STALE["charging"]
                    and since - self.CHARGER_EARLY_S <= cs <= now)

    def _start_gate(self, p, e, now: float) -> bool | None:
        """Before this side's night starts: does the bed radar back the presence (and is it really this side's sleeper)?
        - nobody else in bed: this side's radar zone saw someone for most of the settle time;
        - the partner already in bed: BOTH zones saw someone at once for most of it (two bodies). The partner rolling
          over onto this side lights this zone but empties theirs, so it doesn't count;
        - their phone going on the charger as they settle confirms it at once.
        None (the usual start) without a radar that has been reporting."""
        info = self.__dict__.setdefault("_settle_info", {})
        if e.s.started or not e.s.presence_since:
            info.pop(p.id, None)
            return None
        # from when they lay down: the strip's stretch began (it may be well before presence_since, e.g. while their
        # watch was being checked), so this wait runs alongside the others instead of after them
        ep = (self.__dict__.get("_eps") or {}).get(p.zone)
        since = min(e.s.presence_since, ep["since"]) if ep else e.s.presence_since
        if self._charger(p, since, now):
            info[p.id] = {"gate": True, "why": "phone on the charger"}
            return True
        if ep and ep.get("me_seen") and not ep.get("not_me"):
            info[p.id] = {"gate": True, "why": "their watch matched the bed"}
            return True
        h = [x for x in (self.__dict__.get("_settle_hist") or {}).get(p.zone, []) if x[0] >= max(since, now - self.SETTLE_S)]
        seen = [x for x in h if x[1] is not None]
        if not h or len(seen) < 0.6 * len(h):
            info.pop(p.id, None)
            return None                                   # no radar (or it went quiet): the usual start
        # the partner in bed now: their night running and them on their side (not up, not in an out-of-bed hold)
        partner = next((q for q in self.household.people if q.zone != p.zone and q.id in self.engines
                        and self.engines[q.id].s.started and self.engines[q.id].s.absent_since is None
                        and self.engines[q.id].s.state == "rhythm"), None)
        partner_seen = partner is not None and sum(1 for x in seen if x[2] is not None) >= 0.6 * len(seen)
        if partner is not None and partner_seen:
            share = sum(1 for _, own, oth in seen if own is not None and own >= 0.5 and oth is not None and oth >= 0.5) / len(seen)
            why = "the radar saw two people" if share >= self.SETTLE_SHARE else f"the radar saw only one person ({partner.name} moving across?)"
        else:
            share = sum(1 for _, own, _o in seen if own >= 0.5) / len(seen)
            why = "the radar saw someone on this side" if share >= self.SETTLE_SHARE else "the radar hasn't seen anyone on this side"
        # one sample per tick: about a second apart in real use, minutes apart when the simulator runs fast (x300) or
        # the demo seeds its history, so the count asked for follows the spacing (real use still needs SETTLE_MIN_N)
        gap = (h[-1][0] - h[0][0]) / (len(h) - 1) if len(h) > 1 else 1.0
        need = min(self.SETTLE_MIN_N, max(2, int(0.8 * self.SETTLE_MIN_S / max(gap, 1.0))))
        slack = gap if gap > 5 else 0.0                  # sparse samples: the oldest one in the window can be a gap late
        if now - since < self.SETTLE_MIN_S or len(seen) < need or now - seen[0][0] + slack < self.SETTLE_MIN_S - 60:
            # (the radar's samples are kept in memory only: after a restart they start afresh while presence_since comes
            # back, so a verdict waits until the radar has really watched for a while again)
            info[p.id] = {"gate": False, "why": "settling in", "share": round(share, 2)}
            return False
        ok = share >= self.SETTLE_SHARE
        info[p.id] = {"gate": ok, "why": why, "share": round(share, 2)}
        # lying there a long while (the strip steady, no pet suspected, not away) and the radar still doesn't back it:
        # notify.py offers once to start it now. Never started automatically
        pet = (self.fused.get(p.zone) or {}).get("pet") or {}
        if (not ok and ep is not None and now - ep["since"] >= self.OFFER_START_S and not ep.get("pet_ticks")
                and not pet.get("candidate") and not pet.get("suspected") and not getattr(e, "away", False)):
            info[p.id]["offer_start"] = True
        return ok

    def _away_check_view(self, pid: str) -> dict | None:
        from . import away as away_mod
        try:
            return away_mod.open_check(self.store, pid)
        except Exception:  # noqa: BLE001
            return None

    def _away_view(self, pid: str) -> dict | None:
        from . import away as away_mod
        try:
            return away_mod.view(self.store, pid, self.clock.now_ts())
        except Exception:  # noqa: BLE001
            return None

    # ---------- cold / warm room (engine/padlag.py) ----------
    PADLAG_EVERY_S = 3600

    def room_c(self, now: float) -> float | None:
        """The bedroom temperature now (mean of the room sensors over 30 min), None without a room sensor."""
        hit = self.__dict__.get("_room_now")
        if hit and 0 <= now - hit[0] < 60:
            return hit[1]
        from .padlag import room_now
        temps, _ = self.autopilot.room(now - 1800, now)
        v = room_now(temps, now)
        self._room_now = (now, v)
        return v

    def pad_model(self, zone: str, now: float) -> dict:
        """This side's learned warm/cool speed per room temperature (kv padlag:<zone>, relearned hourly)."""
        saved = self.store.get(f"padlag:{zone}") or {}
        if 0 <= now - float(saved.get("at") or 0) < self.PADLAG_EVERY_S:
            return saved.get("model") or {}
        from .padlag import LEARN_DAYS, learn
        since = now - LEARN_DAYS * 86400
        temps, _ = self.autopilot.room(since, now)
        model = learn(self.store.history(zone, since, now, step_s=60), temps) if temps else {}
        self.store.put(f"padlag:{zone}", {"at": now, "model": model})
        return model

    def _room_inputs(self, p: Person, e: NightEngine, now: float, frame) -> None:
        room = self.room_c(now)
        e.room_c = room
        if room is None:
            e.pad_model, e.hold_warm = {}, False                 # no room sensor: nothing changes
            return
        last = self.__dict__.setdefault("_room_in_at", {})
        if 0 <= now - last.get(p.id, -1e18) < 60:
            return
        last[p.id] = now
        e.pad_model = self.pad_model(p.zone, now)
        recent = [x for x in self.store.events(since=now - 1800, limit=50, person=p.id) if x.get("person") == p.id]
        e.hold_warm = any(x["kind"] == "feel" and x.get("feel") == "too_hot" for x in recent)

    def _room_shortfall(self, p: Person, e: NightEngine, now: float, frame) -> None:
        """A warm target the pad can't reach in a cold room: said plainly (snapshot, one story moment per phase)."""
        from .padlag import COLD_NOTE_C, SHORT_C, SHORT_FOR_S, shortfall_text
        notes = self.__dict__.setdefault("_room_note", {})
        since = self.__dict__.setdefault("_short_since", {})
        z = frame.zones.get(p.zone) if frame else None
        room, tgt = e.room_c, e.s.target_c
        short = (room is not None and room < COLD_NOTE_C and tgt is not None and tgt >= room + 3 and not e.s.flash
                 and e.s.state in ("rhythm", "out_of_bed_hold") and z is not None and z.measured_c is not None
                 and z.measured_c < tgt - SHORT_C)
        if not short:
            since.pop(p.id, None)
            notes.pop(p.id, None)
            return
        prev = since.get(p.id)
        if prev is None or abs(prev[1] - tgt) > 0.2:
            since[p.id] = (now, tgt)                      # a target still on its way (a ramp) isn't a shortfall yet
            notes.pop(p.id, None)
            return
        if now - prev[0] < SHORT_FOR_S:
            return
        units = getattr(p.settings, "units", "F")
        text = shortfall_text(room, tgt, units)
        notes[p.id] = {"text": text, "room_c": room, "target_c": tgt, "measured_c": z.measured_c}
        key = f"{e.s.key}:{e.s.phase_id}"
        said = self.__dict__.setdefault("_room_said", {})
        if said.get(p.id) != key:
            said[p.id] = key
            self.store.event(now, "room_cold", p.id, "system", room_c=room, target_c=tgt, measured_c=z.measured_c,
                             phase=e.s.phase_id, text=text)

    def room_note(self, pid: str) -> dict | None:
        return (self.__dict__.get("_room_note") or {}).get(pid)

    # ---------- "active, not sleeping yet" (engine/active.py) ----------
    @property
    def active_since(self) -> float | None:
        det = self.__dict__.get("_active")
        return det.since if det is not None else None

    def _active_tick(self, now: float, guest_zones: set[str]) -> None:
        from .active import ActiveDetector
        det = self.__dict__.setdefault("_active", ActiveDetector())
        fast = any(getattr(p, "fast_heart", False) for p in getattr(self.household, "pets", []) or [])
        ch = det.update(now, self.presence.sides, known=set(self.presence.prefer), fast_pets=fast)
        self.presence.active = det.on
        zones = [z for z in ("left", "right") if z not in guest_zones]       # nothing is noted about a guest's side
        if ch == "started" and zones:
            self.store.event(now, "active_started", None, "presence", zones=zones, began=det.began)
        elif ch and zones:
            self.store.event(now, f"active_{ch}", None, "presence", zones=zones)

    # ---------- guest mode ----------
    def guest_start(self, zones: list[str], temp_c: float | None, morning_off_min: int | None, ends: float | None,
                    source: str = "app") -> None:
        from ..models import GuestSide
        from .night import NightState
        now = self.clock.now_ts()
        for z in zones:
            cur = self.household.guest.get(z)
            self.household.guest[z] = GuestSide(
                since=cur.since if cur else now, on=True,
                temp_c=temp_c if temp_c is not None else (cur.temp_c if cur else 21.0),
                morning_off_min=morning_off_min or (cur.morning_off_min if cur else 40), ends=ends)
            for p in self.household.people:
                if p.zone == z:
                    self.engines[p.id].s = NightState()      # the person's own night stops: no flash, change or start carried over
            self._guest_empty.pop(z, None)
            if not cur:
                self.store.event(now, "guest_started", None, source, zone=z)
        if self.sources is not None:
            self.sources.paused_zones = set(self.household.guest)
        self.reload_household(self.household)

    def guest_stop(self, zones: list[str], source: str = "app", reason: str | None = None) -> None:
        from .night import NightState
        now = self.clock.now_ts()
        for z in zones:
            if self.household.guest.pop(z, None) is not None:
                for p in self.household.people:
                    if p.zone == z:
                        self.engines[p.id].s = NightState()  # back to the person's own rhythm, starting fresh
                self._guest_empty.pop(z, None)
                self.store.event(now, "guest_stopped", None, source, zone=z, **({"reason": reason} if reason else {}))
        self.reload_household(self.household)

    def guest_set(self, zone: str, on: bool | None, temp_c: float | None) -> None:
        g = self.household.guest.get(zone)
        if g is None:
            raise KeyError(zone)
        if on is not None:
            g.on = on
            self._guest_empty.pop(zone, None)
        if temp_c is not None:
            g.temp_c = round(clamp(temp_c, self.household.bed_min_c, self.household.bed_max_c), 2)
        self.reload_household(self.household)

    @property
    def _guest_empty(self) -> dict[str, float]:
        return self.__dict__.setdefault("_guest_empty_since", {})

    def _guest_tick(self, p: Person, e, present: bool | None, now: float) -> float | None:
        """A guest's side: only the guest's on/off and temperature. The person who usually sleeps there is away
        for the duration (their engine idles), and nothing about the guest is stored."""
        from datetime import datetime
        g = self.household.guest[p.zone]
        if g.ends is not None and now >= g.ends:
            self.guest_stop([p.zone], source="system", reason="ended")
            return None
        # morning: once the side has been empty for a while, turn it off (unknown presence never counts as empty)
        if present is False:
            self._guest_empty.setdefault(p.zone, now)
        else:
            self._guest_empty.pop(p.zone, None)
        since = self._guest_empty.get(p.zone)
        hour = datetime.fromtimestamp(now, self.clock.tz).hour
        if g.on and since is not None and 5 <= hour < 14 and now - since >= g.morning_off_min * 60:
            g.on = False
            self.store.event(now, "guest_side_off", None, "system", zone=p.zone)
            self.reload_household(self.household)
        e.s.state, e.s.phase_id, e.s.base_c = "guest", None, None
        e.s.target_c = g.temp_c if g.on else None
        e.s.events.clear()
        return e.s.target_c

    async def _apply(self, p: Person, target: float | None, frame, forced: bool) -> None:
        z = frame.zones.get(p.zone)
        if z is None:
            return
        if self.leak_hold:
            # Safety interlock: never send want_on=True while a leak hold is active.
            target = None
            if z.on:
                forced = True
        hh = self.household
        e = self.engines[p.id]
        if target is not None:
            lo, hi = (hh.bed_min_c, hh.bed_max_c) if (e.s.flash or p.zone in hh.guest) else (hh.user_min_c, hh.user_max_c)
            target = round(clamp(target, lo, hi) * 10) / 10
        want_on = target is not None
        key = (want_on, target)
        pending_force = self.__dict__.setdefault("_pending_force", set())
        pending_desired = self.__dict__.setdefault("_pending_desired", {})
        inflight_keys = self.__dict__.setdefault("_inflight_key", {})
        inflight_task = self._inflight.get(p.zone)
        inflight_running = inflight_task is not None and not inflight_task.done()
        need = (want_on != bool(z.on)) or (want_on and (z.setpoint_c is None or abs(z.setpoint_c - target) >= 0.05))
        if not need and inflight_running and inflight_keys.get(p.zone) != key:
            # Telemetry may still reflect the state prior to the in-flight command (e.g. turning off while turn-on is in flight).
            need = True
        if not need:
            if not inflight_running:
                pending_force.discard(p.zone)
                pending_desired.pop(p.zone, None)
            return
        t = time.monotonic()
        fails = self.__dict__.setdefault("_unconfirmed", {})       # zone → (command, times it went unconfirmed)
        last_key, n_fail = fails.get(p.zone, (None, 0))
        gap = MIN_CMD_GAP_S * (2 ** min(n_fail, 6)) if last_key == key else MIN_CMD_GAP_S   # same command: back off to ~8 min
        if inflight_running:
            if forced:
                # Preserve forced commands (such as turning the bed off) if an earlier command is still in flight.
                pending_force.add(p.zone)
                pending_desired[p.zone] = (p, target)
            elif p.zone in pending_force:
                pending_desired[p.zone] = (p, target)
            return
        was_pending = p.zone in pending_force
        if not (forced or was_pending) and t - self._last_cmd.get(p.zone, 0) < min(gap, 300):
            return
        pending_force.discard(p.zone)
        pending_desired.pop(p.zone, None)
        self._last_cmd[p.zone] = t

        async def send() -> None:
            try:
                ok = await self.driver.set_zone(p.zone, on=want_on, temp_c=target)
                if ok:
                    fails.pop(p.zone, None)
                    self.__dict__.setdefault("unconfirmed_since", {}).pop(p.zone, None)
                else:
                    prev_key, prev_n = fails.get(p.zone, (None, 0))
                    fails[p.zone] = (key, prev_n + 1 if prev_key == key else 1)
                    # since when this side's commands have gone unconfirmed (notify.py says so after a while)
                    self.__dict__.setdefault("unconfirmed_since", {}).setdefault(p.zone, self.clock.now_ts())
                    log.warning("%s: bed did not confirm %s", p.zone, target)
            except CommandError as ex:
                log.warning("%s: %s", p.zone, ex)
            except Exception:  # noqa: BLE001
                log.exception("set_zone failed")
            finally:
                cur = asyncio.current_task()
                if self._inflight.get(p.zone) is cur:
                    self._inflight.pop(p.zone, None)
                    inflight_keys.pop(p.zone, None)
                    if p.zone in pending_force:
                        des = pending_desired.get(p.zone)
                        latest = self.driver.latest() or frame
                        if des is not None and latest is not None:
                            dp, dtarget = des
                            # Dispatch the pending forced command immediately, bypassing backoff for this send.
                            await self._apply(dp, dtarget, latest, forced=True)
        inflight_keys[p.zone] = key
        self._inflight[p.zone] = asyncio.create_task(send())

    # ---------- the control unit ("hub") ----------
    def hub_summary(self, frame=None) -> dict | None:
        from .. import hub
        frame = frame or self.driver.latest()
        s = hub.summary(frame.hub if frame else None)
        if s is not None:
            s["command_received"] = getattr(self.driver, "acked", None)      # did the unit say it got our last command
        return s

    def hub_detail(self) -> dict | None:
        """The owner's view: includes the unit's network name and address. Never log or send this to anyone else."""
        from .. import hub
        frame = self.driver.latest()
        d = hub.detail(frame.hub if frame else None,
                         untried=sorted(c.value for c in getattr(self.driver, "untried", set())))
        if d is not None:
            d["command_received"] = getattr(self.driver, "acked", None)
        return d

    def running_sides(self) -> list[str]:
        """Zones whose night is running (a restart or water fill would interrupt them)."""
        zones = [p.zone for p in self.household.people
                 if self.engines[p.id].s.started and self.engines[p.id].s.state in ("rhythm", "out_of_bed_hold")]
        return zones + [z for z in self.household.guest if z not in zones]      # a guest's side is running too

    def note_restart(self) -> None:
        """The unit was told to restart: it forgets its setpoints. Drop the retry back-offs so that, the moment it is
        reporting again, every side's current target goes out at once (tick compares targets with what the unit reports)."""
        self.__dict__.pop("_unconfirmed", None)
        self._last_cmd.clear()
        self.__dict__["_was_ready"] = False

    # ---------- view ----------
    def snapshot(self) -> dict:
        now = self.clock.now_ts()
        frame = self.driver.latest()
        people = []
        for p in self.household.people:
            e = self.engines[p.id]
            n = e.projected(now)
            z = frame.zones.get(p.zone) if frame else None
            s = self.presence.sides.get(p.zone)
            o_end = e.override_end(n) if n else None
            ov = e.s.override

            def adj(ph):                                   # the temperature this phase will actually run at tonight
                if not ov or o_end is None or ph.start >= o_end or (ov.until == "next_phase" and ph.id != ov.phase_id):
                    return None
                return round(ov.temp_c if ov.temp_c is not None else ph.temp_c + (ov.delta_c or 0), 2)
            settle_ids = e.settle_phase_ids() if n else set()
            phases = [{"id": ph.id, "name": ph.name, "start": ph.start, "temp_c": ph.temp_c, "adjusted_c": adj(ph),
                       **({"when_settled": True} if ph.id in settle_ids else {})} for ph in (n.phases if n else [])]
            cur_phase = next((ph["name"] for ph in phases if ph["id"] == e.s.phase_id), None)
            actual = []
            if n:
                # the measured curve only changes when a telemetry row is written (every 15 s): cache it until then
                step = 60 if self.clock.speed <= 1 else 120
                key = (n.precool, step, self._last_tel)
                hit = self.__dict__.setdefault("_actual_cache", {}).get(p.id)
                if hit and hit[0] == key:
                    actual = hit[1]
                else:
                    hist = self.store.history(p.zone, since=n.precool - 3600, step_s=step, person=p.id)
                    actual = [[round(h["ts"]), round(h["measured"], 2)] for h in hist if h["measured"] is not None]
                    self._actual_cache[p.id] = (key, actual)
            from .rhythm import curve
            f = e.s.flash
            o = e.s.override
            people.append({
                "id": p.id, "name": p.name, "persona": p.persona, "zone": p.zone,
                "settings": p.settings.model_dump(),
                "night": {
                    "state": e.s.state, "phase_id": e.s.phase_id, "phase_name": cur_phase, "started": e.s.started,
                    "in_bed": e.s.in_bed, "base_c": e.s.base_c, "target_c": e.s.target_c, "skip": e.s.skip,
                    "lie_in": e.s.lie_in, "running": e.running(),
                    "room_note": self.room_note(p.id), "lead_min": round(e.lead_s / 60) if e.lead_s else None,
                    "pause_until": e.s.pause_until, "home": e.s.home,
                    # in bed early: waiting for a sign of sleep before the night moves on (engine/sleepsign.py)
                    "sleep": {"waiting": bool(settle_ids) or e.waiting_for_sleep(), "at": e.s.sleep_at, "why": e.s.sleep_why}, "away": self._away_view(p.id), "away_check": self._away_check_view(p.id),
                    "settle": (self.__dict__.get("_settle_info") or {}).get(p.id), "context": None if p.zone in self.household.guest else self.context.get(p.id),
                    "guest": self.household.guest[p.zone].model_dump() if p.zone in self.household.guest else None,
                    "flash": None if not f else {"kind": f.kind, "start": f.start, "end": f.end, "ease_end": f.end + f.ease_s,
                                                  "target_c": f.target_c, "source": f.source},
                    "override": None if not o else {"until": o.until, "temp_c": o.temp_c, "delta_c": o.delta_c, "phase_id": o.phase_id,
                                                  "end_ts": o.end_ts, "manual": o.manual, "set_ts": o.set_ts},
                },
                "bed": {"on": z.on if z else None, "setpoint_c": z.setpoint_c if z else None, "measured_c": z.measured_c if z else None},
                "presence": {"present": self.fused.get(p.zone, {}).get("present", self.presence.present(p.zone)),
                             "starts_in_s": (max(0, sched_confirm(p, e) - (now - e.s.presence_since)) if (e.s.presence_since and not e.s.started and e.s.state == "waiting") else None),
                             "fusion": self.fused.get(p.zone), "bed_present": self.presence.present(p.zone), "occupied": s.occupied if s else None,
                             "since": s.since if s else None, "echo": s.echo if s else False, "stale": s.stale if s else False,
                             "hr": s.hr if s else None, "br": s.br if s else None, "status": s.status if s else None,
                             "status_text": STATUS_TEXT.get(s.status, None) if s and s.status is not None else None},
                "schedule": None if not n else {"bedtime": n.bedtime, "precool": n.precool, "window_start": n.window_start,
                                                "window_end": n.window_end, "wake": n.wake, "end": n.end, "rhythm_id": n.rhythm_id},
                "phases": phases,
                "next": e.next_info(now),
                "curve": e.curve(n, 600) if n else [],
                "rhythm_curve": [[t, v] for t, v in curve(n, 600)] if n and (o or f) else None,
                "actual": actual,
            })
        snap = {
            "now": now, "tz": str(self.clock.tz), "speed": self.clock.speed,
            "system": {**self.driver.status(), "capabilities": sorted(c.value for c in self.driver.capabilities),
                       "firmware": frame.firmware if frame else None, "quiet": frame.quiet_mode if frame else None,
                       "brightness": frame.brightness if frame else None, "rgb": frame.rgb if frame else None,
                       "system_state": frame.system_state if frame else None,
                       # control-unit health in brief (no network name or address; the owner's page has the detail)
                       "hub": self.hub_summary(frame),
                       # ticks in a row a person's update has failed (0 / absent = fine; notify.py says so after a while)
                       "tick_errors": dict(self._person_errors)},
            "household": {"user_min_c": self.household.user_min_c, "user_max_c": self.household.user_max_c},
            "guest": {z: g.model_dump() for z, g in self.household.guest.items()},
            "leak_hold": dict(self.leak_hold) if self.leak_hold else None,
            # each side of the bed as the bed names it, whoever sleeps there (setup's "check your side" uses it)
            "zones": {z: {"present": self.fused.get(z, {}).get("present", self.presence.present(z)),
                          "bed_present": self.presence.present(z)} for z in ("left", "right")},
            "people": people,
        }
        self.last_snapshot = snap
        return snap
