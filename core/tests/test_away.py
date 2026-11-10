"""Away for a while (engine/away.py): the night and the calibration week pause; stays on the side teach the pet."""

from embertide.engine import away, petq
from test_watch_identity import DOG, WatchNight, _ep


async def test_away_side_never_starts_and_learning_pauses(tmp_path):
    """2026-11-09: one sleeper is away for the week; the dog lies on her side all night. Her side stays off, nobody
    gets asked about it, Autopilot says the learning is paused, and no morning summary goes to her."""
    n = WatchNight(tmp_path, "2026-11-06T20:00", pets=[DOG])
    n.bed.scripted = False
    away.start(n.c, "p2", n.clock.now_ts(), by="p1")
    n.present("right", True)                          # the dog, from 9 PM until morning
    from embertide.notify import Notifier
    await n.until("2026-11-06T23:30")
    msgs = Notifier(n.c, None).observe(n.clock.now_ts(), n.c.snapshot())
    assert not [m for m in msgs if m.kind == "away_precool"]          # no "you're away at pre-cool" every night
    await n.until("2026-11-07T07:00")
    e = n.e(n.p2)
    assert not e.s.started and e.s.state in ("away", "done") and e.s.target_c is None
    assert petq.open_for(n.store, "p2", n.clock.now_ts()) is None
    lr = n.c.autopilot.learning(n.p2, n.clock.now_ts())
    assert lr["paused"] and not lr["done"]
    snap = n.c.snapshot()
    assert next(p for p in snap["people"] if p["id"] == "p2")["night"]["away"]["since"]
    assert Notifier(n.c, None)._morning(n.p2, "2026-11-06", n.clock.now_ts()) is None


async def test_held_stretches_teach_the_pet_later_and_are_dropped_if_they_were_back(tmp_path):
    n = WatchNight(tmp_path, "2026-11-08T13:00", pets=[DOG])
    t = n.clock.now_ts()
    away.start(n.c, "p2", t)
    assert petq.consider(n.c, "right", _ep(t, t + 3 * 3600), t + 3 * 3600 + 60) is None     # held, not asked
    assert len(n.store.get("away_spans:p2")) == 1
    assert away._settle(n.c, "p2", t + 5 * 3600) == 0                 # too recent to be sure
    assert away._settle(n.c, "p2", t + 3 * 3600 + away.LEARN_AFTER_S + 60) == 1
    assert petq.pet_sigs(n.store)                                      # the dog's signature learned from it
    # another stay, then she says she's back the next morning: that stay could have been her, so it's dropped
    t2 = t + 30 * 3600
    petq.consider(n.c, "right", _ep(t2, t2 + 6 * 3600), t2 + 6 * 3600 + 60)
    sig_before = petq.pet_sigs(n.store)
    assert away.end(n.c, "p2", t2 + 8 * 3600, "back")
    assert not n.store.get("away_spans:p2") and petq.pet_sigs(n.store) == sig_before
    assert not away.is_away(n.store, "p2")


async def test_using_their_side_or_the_end_date_means_back(tmp_path):
    n = WatchNight(tmp_path, "2026-11-06T20:00", pets=[DOG])
    n.bed.scripted = False
    now = n.clock.now_ts()
    away.start(n.c, "p2", now)
    await n.step(2)
    n.e(n.p2).tonight(n.clock.now_ts(), "start_now", source="dial")
    await n.step(2)
    assert not away.is_away(n.store, "p2") and n.e(n.p2).s.started
    assert any(e["kind"] == "away_ended" for e in n.store.events(since=now, limit=50))
    away.start(n.c, "p1", n.clock.now_ts(), until=n.clock.now_ts() + 600)
    await n.step(30)
    assert not away.is_away(n.store, "p1") and n.store.get("away:p1") is None


def test_away_api_owner_or_self(tmp_path):
    from fastapi.testclient import TestClient
    from test_auth import H, PW, invite_partner, make, setup_owner
    with make(tmp_path) as owner:
        setup_owner(owner)
        code = invite_partner(owner)
        g = TestClient(owner.app)
        assert g.post("/api/auth/accept", json={"code": code, "username": "sam", "password": PW}).status_code == 200
        assert g.post("/api/people/p1/away", json={"away": True}, headers=H).status_code == 403     # not hers
        r = owner.post("/api/people/p2/away", json={"away": True}, headers=H)
        assert r.status_code == 200, r.text
        snap = owner.get("/api/snapshot").json()
        assert next(p for p in snap["people"] if p["id"] == "p2")["night"]["away"]
        assert owner.post("/api/people/p2/away", json={"away": True, "until": 1.0}, headers=H).status_code == 422
        assert g.post("/api/people/p2/away", json={"away": False}, headers=H).status_code == 200    # her own: back
        snap = owner.get("/api/snapshot").json()
        assert next(p for p in snap["people"] if p["id"] == "p2")["night"]["away"] is None


def test_healthz_offers_the_encrypted_address(tmp_path):
    from test_auth import H, make, setup_owner
    with make(tmp_path) as c:
        a = c.get("/healthz").json()
        assert a["ok"] and a["id"] and "secure_url" not in a
        setup_owner(c)
        assert c.put("/api/notify/settings", json={"public_url": "https://sleep.example.com/"}, headers=H).status_code == 200
        b = c.get("/healthz").json()
        assert b["secure_url"] == "https://sleep.example.com" and b["id"] == a["id"]



# ---------- the morning check ----------
import math
from datetime import date, datetime


def _fake(n, monkeypatch, occupied, bed_hr=None, ring=None, wear_hr=None):
    """Stub the bed history / ring for the night of 2026-11-09 (6 PM to noon)."""
    a = datetime(2026, 11, 9, 18, 0, tzinfo=n.clock.tz).timestamp()

    def history(zone, x, y, step_s=60, person=None):
        out, t = [], max(x, a)
        while t < min(y, a + 18 * 3600):
            occ = occupied(t)
            out.append({"ts": t, "occupied": 1 if occ else 0, "hr": (bed_hr(t) if bed_hr and occ else None)})
            t += step_s
        return out
    monkeypatch.setattr(n.c.store, "history", history)
    if wear_hr:
        monkeypatch.setattr(n.c.store, "sample_sources", lambda pre: ["wear:p2:hr:oura"] if pre.startswith("wear:p2") else [])
        monkeypatch.setattr(n.c.store, "samples", lambda src, x, y: [(t, wear_hr(t)) for t in range(int(x), int(y), 300)])
    if ring:
        n.c.store.put("sleeps:p2", [{"id": "oura:1", "kind": "night", "start": a + ring[0] * 3600, "end": a + ring[1] * 3600}])
    return a


async def test_morning_check_asks_when_she_wasnt_in_bed_and_answers_retroactively(tmp_path, monkeypatch):
    n = WatchNight(tmp_path, "2026-11-10T09:00", pets=[DOG])
    a = _fake(n, monkeypatch, occupied=lambda t: False)
    now = n.clock.now_ts()
    q = away.morning_check(n.c, n.p2, now)
    assert q and q["kind"] == "away" and q["night"] == "2026-11-09" and "empty all night" in q["why"]
    assert away.morning_check(n.c, n.p2, now + 60) is None                     # once per night
    assert "2026-11-09" in away.excluded_nights(n.store, "p2")                  # left out while asked
    assert n.c.snapshot()["people"][1]["night"]["away_check"]["night"] == "2026-11-09"
    away.answer_check(n.c, "p2", "2026-11-09", "away", now + 120)
    rec = away.get(n.store, "p2")
    assert rec and rec["since"] == a and "2026-11-09" in away.excluded_nights(n.store, "p2")
    assert away.open_check(n.store, "p2") is None


async def test_morning_check_from_the_notification_and_i_was_home(tmp_path, monkeypatch):
    from embertide.notify import Notifier
    n = WatchNight(tmp_path, "2026-11-10T09:00", pets=[DOG])
    _fake(n, monkeypatch, occupied=lambda t: False, ring=(5, 13))                 # ring asleep 11 PM-7 AM, side empty
    nt = Notifier(n.c, None)
    m = nt._away_check(n.p2, n.clock.now_ts())
    assert m and m.title == "Were you away Monday night?" and [x.act for x in m.actions] == ["away_away", "away_night", "away_home"]
    used = nt.use_action(nt.new_action("p2", m.actions[2]))
    assert used["done"] == "Noted: you were home"
    assert "2026-11-09" not in away.excluded_nights(n.store, "p2") and not away.is_away(n.store, "p2")


async def test_in_bed_as_usual_asks_nothing_and_the_dog_alone_isnt_her(tmp_path, monkeypatch):
    n = WatchNight(tmp_path, "2026-11-10T09:00", pets=[DOG])
    wave = lambda t: 58 + 6 * math.sin(t / 2400)                                 # her heart rate through the night
    _fake(n, monkeypatch, occupied=lambda t: True, bed_hr=wave, ring=(5, 13), wear_hr=wave)
    assert away.night_verdict(n.c, n.p2, date(2026, 11, 9))[0] == "bed"
    assert away.morning_check(n.c, n.p2, n.clock.now_ts()) is None
    (tmp_path / "b").mkdir()
    m = WatchNight(tmp_path / "b", "2026-11-10T09:00", pets=[DOG])
    dog = lambda t: 58 + 6 * math.sin(t / 700 + 2)                               # same level, its own ups and downs
    _fake(m, monkeypatch, occupied=lambda t: True, bed_hr=dog, ring=(5, 13), wear_hr=wave)
    assert away.night_verdict(m.c, m.p2, date(2026, 11, 9))[0] == "away"


async def test_marked_away_but_asleep_in_bed_asks_welcome_back(tmp_path, monkeypatch):
    n = WatchNight(tmp_path, "2026-11-10T09:00", pets=[DOG])
    wave = lambda t: 60 + 5 * math.sin(t / 2000)
    a = _fake(n, monkeypatch, occupied=lambda t: True, bed_hr=wave, ring=(5, 13), wear_hr=wave)
    away.start(n.c, "p2", a - 86400)
    q = away.morning_check(n.c, n.p2, n.clock.now_ts())
    assert q and q["kind"] == "back"
    away.answer_check(n.c, "p2", q["night"], "back", n.clock.now_ts())
    assert not away.is_away(n.store, "p2") and n.store.get("away_log:p2")


async def test_a_stay_the_radar_didnt_see_isnt_held_as_the_pet(tmp_path):
    """2026-11-09 night 2: only the owner in bed; the away partner's pad lit (his movement leaking across) while
    the bed radar's zone on her side said empty. That isn't the dog: nothing is held to learn from."""
    n = WatchNight(tmp_path, "2026-11-08T21:00", pets=[DOG])
    t = n.clock.now_ts()
    away.start(n.c, "p2", t)
    ep = {**_ep(t, t + 3 * 3600), "radar_ticks": 300, "radar_empty": 290}
    petq.consider(n.c, "right", ep, t + 3 * 3600 + 60)
    assert not n.store.get("away_spans:p2")
    petq.consider(n.c, "right", {**_ep(t + 4 * 3600, t + 6 * 3600), "radar_ticks": 200, "radar_empty": 10}, t + 6 * 3600 + 60)
    assert len(n.store.get("away_spans:p2")) == 1                  # seen by the radar: held as before


async def test_a_bed_or_server_outage_isnt_were_you_away(tmp_path, monkeypatch):
    """Her ring had her asleep, but the bed (or the server) wasn't reporting: no rows, or rows with presence unknown,
    don't make an empty side. Can't tell, so nothing is asked. With the bed known for most of the night, it judges."""
    from datetime import datetime
    n = WatchNight(tmp_path, "2026-11-10T09:00", pets=[DOG])
    a = datetime(2026, 11, 9, 18, 0, tzinfo=n.clock.tz).timestamp()
    n.c.store.put("sleeps:p2", [{"id": "oura:1", "kind": "night", "start": a + 5 * 3600, "end": a + 13 * 3600}])
    monkeypatch.setattr(n.c.store, "history", lambda *x, **k: [])                         # nothing recorded at all
    assert away.night_verdict(n.c, n.p2, date(2026, 11, 9))[0] is None
    assert away.morning_check(n.c, n.p2, n.clock.now_ts()) is None

    def unknown(zone, x, y, step_s=60, person=None):                                     # rows, but presence unknown
        return [{"ts": t, "occupied": None, "hr": None} for t in range(int(x), int(y), step_s)]
    monkeypatch.setattr(n.c.store, "history", unknown)
    assert away.night_verdict(n.c, n.p2, date(2026, 11, 9))[0] is None

    def partly(zone, x, y, step_s=60, person=None):                                      # known and empty for 70% of it
        return [{"ts": t, "occupied": 0 if t < x + 0.7 * (y - x) else None, "hr": None} for t in range(int(x), int(y), step_s)]
    monkeypatch.setattr(n.c.store, "history", partly)
    assert away.night_verdict(n.c, n.p2, date(2026, 11, 9))[0] == "away"
    n.c.store.put("sleeps:p2", [])                                                       # no ring night
    assert away.night_verdict(n.c, n.p2, date(2026, 11, 9))[0] == "away"                 # known empty most of the night
    monkeypatch.setattr(n.c.store, "history", unknown)
    assert away.night_verdict(n.c, n.p2, date(2026, 11, 9))[0] is None                   # presence unknown all night


async def test_a_welcome_back_still_asked_holds_off_teaching_that_nights_stays(tmp_path):
    """Marked away, a stay on her side that night, and "Welcome back?" asked about it but not answered yet: the stay
    isn't taught as the dog after 18 h. "Still away" lets it be taught; "Yes, I'm back" drops it."""
    for ans, learned in (("still", 1), ("back", 0)):
        (tmp_path / ans).mkdir()
        n = WatchNight(tmp_path / ans, "2026-11-08T23:00", pets=[DOG])
        t = n.clock.now_ts()
        away.start(n.c, "p2", t - 86400)
        petq.consider(n.c, "right", _ep(t, t + 3 * 3600), t + 3 * 3600 + 60)
        assert len(n.store.get("away_spans:p2")) == 1
        away._save_checks(n.store, "p2", {"2026-11-08": {"kind": "back", "status": "asked", "why": "ring", "at": t + 10 * 3600}})
        late = t + 3 * 3600 + away.LEARN_AFTER_S + 3600
        assert away._settle(n.c, "p2", late) == 0 and len(n.store.get("away_spans:p2")) == 1     # waits for the answer
        away.answer_check(n.c, "p2", "2026-11-08", ans, late + 60)
        if ans == "still":
            assert away._settle(n.c, "p2", late + 120) == learned and petq.pet_sigs(n.store)
        else:
            assert not n.store.get("away_spans:p2") and not petq.pet_sigs(n.store)


async def test_a_stay_their_watch_or_phone_says_was_them_isnt_held(tmp_path):
    n = WatchNight(tmp_path, "2026-11-08T23:00", pets=[DOG])
    t = n.clock.now_ts()
    away.start(n.c, "p2", t)
    petq.consider(n.c, "right", {**_ep(t, t + 3 * 3600), "me_seen": True}, t + 3 * 3600 + 60)
    assert not n.store.get("away_spans:p2")                        # her watch matched the bed
    n.c.context["p2"] = {"home": True, "why": "your phone: home"}
    petq.consider(n.c, "right", _ep(t + 4 * 3600, t + 6 * 3600), t + 6 * 3600 + 60)
    assert not n.store.get("away_spans:p2")                        # her phone says home
    n.c.context["p2"] = {"home": False, "why": "your phone: away from home"}
    petq.consider(n.c, "right", _ep(t + 7 * 3600, t + 9 * 3600), t + 9 * 3600 + 60)
    assert len(n.store.get("away_spans:p2")) == 1                  # away for real: held as before


class _RadarSees:
    """A stand-in for the source manager: one radar zone on the right that sees someone (the dog) all along."""
    configs: list = []
    latest: dict = {}
    paused_zones: set = set()

    def __init__(self):
        import types
        self.calibration = types.SimpleNamespace(weights={"bed": 0.5}, person_kg={})

    def zone_signals(self, zone):
        return {"presence:r1": 1.0} if zone == "right" else {}

    def get(self, i):
        return None

    def vote_value(self, c):
        return None


async def test_a_pet_stay_the_radar_sees_is_held_through_fuse(tmp_path):
    """The dog on the away partner's side, suspected as the pet (so fusion zeroes its votes), with the radar seeing it
    the whole time: counted as seen by the radar, so the stay is held to learn from (not dropped as "radar empty")."""
    n = WatchNight(tmp_path, "2026-11-08T21:00", pets=[DOG])
    n.bed.scripted = False
    n.c.sources = _RadarSees()
    n.c._pet_check = lambda zone, votes, ep=None: ({"suspected": True, "candidate": True, "with_person": False,
                                                    "pet": DOG["name"], "why": "dog"} if zone == "right" else None)
    away.start(n.c, "p2", n.clock.now_ts())
    n.present("right", True)
    await n.step(60)                                               # 30 min
    ep = n.c._eps["right"]
    assert ep["radar_ticks"] >= 50 and not ep.get("radar_empty")
    assert all(v["value"] == 0.0 for v in n.fused()["votes"] if v["signal"].startswith("presence:"))   # still zeroed in fusion
    n.present("right", False)
    await n.step(6)                                                # the stay ends
    spans = n.store.get("away_spans:p2") or []
    assert len(spans) == 1 and spans[0]["zone"] == "right"
