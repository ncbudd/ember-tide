"""Context devices: phone on Wi-Fi, phone location (GPS), calendar, bedroom light. Simulated end to end with the
real controller, night engine and source manager."""

import pytest

from embertide.engine.context import person_context
from embertide.sources import SourceConfig, SourceManager
from test_scenarios import Night, ts


class CtxNight(Night):
    def __init__(self, tmp_path, start, devices=(), **kw):
        super().__init__(tmp_path, start, **kw)
        self.mgr = SourceManager(self.store, self.clock, lambda: self.bed)
        self.mgr.configs = list(devices)
        self.c.sources = self.mgr

    async def step(self, n=1, dt=30):
        for _ in range(n):
            self.mgr.sim_step(self.clock.now_ts())
            await super().step(1, dt)

    def ctx(self, p=None):
        return self.c.context[(p or self.p1).id]


def phone(**params):
    return SourceConfig(id="ph1", name="Phone", driver="sim", role="home", kind="phone", person="p1", params={"kind": "home", **params})


def gps(**trip):
    return SourceConfig(id="gps1", name="Location", driver="sim", role="location", kind="location", person="p1",
                        params={"kind": "location", "trip": trip} if trip else {"kind": "location"})


def cal(*events):
    return SourceConfig(id="cal1", name="Calendar", driver="sim", role="calendar", kind="calendar", person="p1",
                        params={"kind": "calendar", "events": [list(e) for e in events]})


def light(**params):
    return SourceConfig(id="lt1", name="Bedroom light", driver="sim", role="light", kind="light", params={"kind": "light", **params})


# ───────────── no devices: nothing changes ─────────────
async def test_no_context_devices_change_nothing(tmp_path):
    n = CtxNight(tmp_path, "2026-11-05T21:00")
    await n.until("2026-11-05T21:40")
    assert n.ctx()["home"] is None and n.e(n.p1).s.state == "waiting" and n.side("left").on


# ───────────── phone on Wi-Fi ─────────────
async def test_phone_away_holds_precool_then_home_resumes(tmp_path):
    n = CtxNight(tmp_path, "2026-11-05T19:00", [phone(away=[["18:00", "22:10"]], naps=False)])
    await n.until("2026-11-05T19:10")
    assert n.ctx()["home"] is True                                    # "not home" for only 10 min (we joined at 19:00): not away yet
    await n.until("2026-11-05T19:25")
    assert n.ctx()["home"] is False                                   # 25 min of "not home": away
    await n.until("2026-11-05T21:40")
    assert n.ctx()["home"] is False and n.e(n.p1).s.state == "away" and not n.side("left").on
    assert n.side("right").on                                         # the partner is unaffected
    await n.until("2026-11-05T22:12")
    assert n.ctx()["home"] is True and n.e(n.p1).s.state == "waiting" and n.side("left").on
    kinds = [(e["home"], e["source"]) for e in n.events("home_changed")]
    assert (False, "context") in kinds and (True, "context") in kinds


async def test_sleeping_phone_dropping_off_wifi_is_not_away(tmp_path):
    n = CtxNight(tmp_path, "2026-11-05T21:00", [phone(naps=True)])
    seen = set()
    for _ in range(120):                                              # an hour in 30 s steps
        await n.step()
        seen.add(n.ctx()["home"])
    assert seen <= {True, None} and False not in seen


async def test_manual_switch_wins_over_context(tmp_path):
    n = CtxNight(tmp_path, "2026-11-05T21:40", [phone(naps=False)])
    n.c.home[n.p1.id] = False
    await n.step(4)
    assert n.e(n.p1).s.state == "away"
    n.c.home[n.p1.id] = None                                          # hand it back to the devices
    await n.step(2)
    assert n.e(n.p1).s.home is True and n.e(n.p1).s.state == "waiting"


# ───────────── GPS ─────────────
async def test_driving_home_starts_precool_before_arrival(tmp_path):
    n = CtxNight(tmp_path, "2026-11-05T19:00", [gps(leave="19:30", arrive="23:00", km=30, speed=60)])
    await n.until("2026-11-05T21:40")
    assert n.ctx()["home"] is False and n.ctx()["distance_km"] == 30 and n.e(n.p1).s.state == "away"
    await n.until("2026-11-05T22:40")                                 # 20 min out, heading home
    c = n.ctx()
    assert c["heading_home"] and c["eta_min"] is not None and c["eta_min"] <= 30
    assert c["home"] is True and n.side("left").on, "bed should be pre-cooling for the arrival"
    assert "on the way home" in c["why"]
    await n.until("2026-11-05T23:05")
    assert n.ctx()["home"] is True and n.ctx()["distance_km"] == 0


async def test_parked_far_away_is_not_heading_home(tmp_path):
    n = CtxNight(tmp_path, "2026-11-05T19:00", [gps(leave="19:00", arrive="09:00", km=12, speed=40)])
    await n.until("2026-11-05T22:30")
    c = n.ctx()
    assert c["home"] is False and not c["heading_home"] and n.e(n.p1).s.state == "away"


async def test_gps_home_beats_wifi_away(tmp_path):
    n = CtxNight(tmp_path, "2026-11-05T19:00", [phone(away=[["18:00", "23:59"]], naps=False), gps()])
    await n.until("2026-11-05T21:40")
    assert n.ctx()["home"] is True, "GPS says home; the phone just isn't on Wi-Fi"


async def test_stale_gps_is_ignored(tmp_path):
    g = gps(leave="19:00", arrive="09:00", km=12)
    n = CtxNight(tmp_path, "2026-11-05T19:00", [g])
    await n.until("2026-11-05T20:00")
    g.enabled = False                                                 # stops reporting
    await n.until("2026-11-05T21:00")
    assert person_context(n.mgr, n.store, n.p1, n.clock.now_ts()).home is None


# ───────────── calendar ─────────────
async def test_late_event_delays_schedule_fallback(tmp_path):
    # bed sensors silent all night: normally the night starts on schedule 10 min after bedtime
    n = CtxNight(tmp_path, "2026-11-05T21:00", [cal(("19:00", "23:30", "Concert"))])
    n.bed.scripted = False
    n.side("left").frozen = n.side("right").frozen = True
    await n.until("2026-11-05T22:40")
    assert n.ctx()["busy_until"] == pytest.approx(ts("2026-11-05T23:50"))
    started_early = n.e(n.p1).s.started
    await n.until("2026-11-06T00:10")
    assert not started_early, "shouldn't start on schedule while out at the concert"
    assert n.e(n.p1).s.started


async def test_finished_event_is_forgotten(tmp_path):
    n = CtxNight(tmp_path, "2026-11-05T18:00", [cal(("18:00", "19:00", "Gym"))])
    await n.until("2026-11-05T18:30")
    assert n.ctx()["event_end"] == pytest.approx(ts("2026-11-05T19:00"))
    await n.until("2026-11-05T19:10")
    assert n.ctx()["event_end"] is None and n.ctx()["busy_until"] == pytest.approx(ts("2026-11-05T19:20"))   # travel home
    await n.until("2026-11-05T19:25")
    assert n.ctx()["busy_until"] is None


# ───────────── bedroom light ─────────────
async def test_bedroom_light(tmp_path):
    n = CtxNight(tmp_path, "2026-11-05T22:50", [light(off="23:00", night=[["03:10", "03:14"]], on="07:00")])
    await n.step(2)
    assert n.ctx()["lights_on"] is True
    await n.until("2026-11-05T23:05")
    assert n.ctx()["lights_on"] is False
    await n.until("2026-11-06T03:12", dt=120)
    assert n.ctx()["lights_on"] is True


async def test_snapshot_carries_context(tmp_path):
    n = CtxNight(tmp_path, "2026-11-05T21:00", [phone(naps=False)])
    await n.step(2)
    p = next(x for x in n.c.snapshot()["people"] if x["id"] == "p1")
    assert p["night"]["context"]["home"] is True


def test_story_tells_away_and_back_but_not_flickers():
    from zoneinfo import ZoneInfo
    from embertide.engine.summary import _moments
    ev = [{"kind": "home_changed", "ts": 100, "home": False}, {"kind": "home_changed", "ts": 300, "home": True},
          {"kind": "home_changed", "ts": 5000, "home": False, "why": "phone location: 12 km away"},
          {"kind": "home_changed", "ts": 5100, "home": False}, {"kind": "home_changed", "ts": 9000, "home": True}]
    m = [(x["ts"], x["home"]) for x in _moments(ev, ZoneInfo("America/New_York"))]
    assert m == [(5000, False), (9000, True)]
