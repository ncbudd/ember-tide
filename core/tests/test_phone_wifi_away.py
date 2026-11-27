"""Phone's own Wi-Fi report: off the home Wi-Fi for 30 min counts as away when nothing better says so."""

from embertide.engine import phone_context as PC
from embertide.engine.context import person_context
from test_autopilot_suite import make
from test_context import CtxNight
from test_phone_context import ctl, ctx, nogap, post


def test_wifi_off_31_min_marks_away(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        k = ctl(c)
        post(c, wifi_home=False, sharing=["wifi"])
        t0 = PC.latest(k.store, "p1")["wifi_off_since"]
        k.clock.jump_to(t0 + 15 * 60)
        post(c, wifi_home=False)                         # the phone checks in every ~15 min
        k.clock.jump_to(t0 + 31 * 60 + 1)
        post(c, wifi_home=False)
        x = ctx(c)
        assert x.home is False
        assert x.why == "your phone has been off the home Wi-Fi for 31 min"


def test_wifi_off_10_min_is_still_unknown(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        k = ctl(c)
        post(c, wifi_home=False, sharing=["wifi"])
        t0 = PC.latest(k.store, "p1")["wifi_off_since"]
        k.clock.jump_to(t0 + 10 * 60 + 1)
        post(c, wifi_home=False)
        assert ctx(c).home is None


def test_wifi_off_away_setting_false_never_marks_away(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        k = ctl(c)
        k.person("p1").settings.wifi_off_away = False
        post(c, wifi_home=False, sharing=["wifi"])
        t0 = PC.latest(k.store, "p1")["wifi_off_since"]
        k.clock.jump_to(t0 + 15 * 60)
        post(c, wifi_home=False)
        k.clock.jump_to(t0 + 35 * 60)
        post(c, wifi_home=False)
        assert ctx(c).home is None


def test_back_on_wifi_marks_home_and_clears_wifi_off_since(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        k = ctl(c)
        post(c, wifi_home=False, sharing=["wifi"])
        t0 = PC.latest(k.store, "p1")["wifi_off_since"]
        k.clock.jump_to(t0 + 15 * 60)
        post(c, wifi_home=False)                         # the phone checks in every ~15 min
        k.clock.jump_to(t0 + 31 * 60 + 1)
        post(c, wifi_home=False)
        assert ctx(c).home is False
        post(c, wifi_home=True)
        x = ctx(c)
        assert x.home is True and "home Wi-Fi" in x.why
        assert PC.latest(k.store, "p1")["wifi_off_since"] is None


def test_repeated_false_reports_keep_first_wifi_off_since(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        k = ctl(c)
        post(c, wifi_home=False, sharing=["wifi"])
        first = PC.latest(k.store, "p1")["wifi_off_since"]
        k.clock.jump_to(first + 10 * 60)
        post(c, wifi_home=False)
        assert PC.latest(k.store, "p1")["wifi_off_since"] == first
        k.clock.jump_to(first + 20 * 60)
        post(c, wifi_home=False)
        assert PC.latest(k.store, "p1")["wifi_off_since"] == first
        # turning off the wifi sharing switch drops wifi_off_since too
        post(c, sharing=["alarm"])
        assert "wifi_off_since" not in PC.latest(k.store, "p1")


def test_phone_location_home_wins_over_wifi_off(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        k = ctl(c)
        post(c, wifi_home=False, sharing=["wifi", "location"])
        t0 = PC.latest(k.store, "p1")["wifi_off_since"]
        k.clock.jump_to(t0 + 31 * 60 + 1)
        post(c, wifi_home=False, home=True)
        x = ctx(c)
        assert x.home is True and x.why == "your phone: home"


def test_stale_wifi_report_never_marks_away(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        k = ctl(c)
        post(c, wifi_home=False, sharing=["wifi"])
        t0 = PC.latest(k.store, "p1")["wifi_off_since"]
        k.clock.jump_to(t0 + 31 * 60 + 1)
        assert ctx(c).home is None


async def test_full_context_and_evening_precool_hold(tmp_path):
    n = CtxNight(tmp_path, "2026-11-05T21:00")
    PC.update(n.store, n.p1.id, {"wifi_home": False}, n.clock.now_ts(), sharing=["wifi"])
    await n.until("2026-11-05T21:20")
    PC.update(n.store, n.p1.id, {"wifi_home": False}, n.clock.now_ts())
    await n.until("2026-11-05T21:31")
    full = person_context(n.mgr, n.store, n.p1, n.clock.now_ts())
    assert full.home is False
    assert full.why == "your phone has been off the home Wi-Fi for 31 min"
    await n.until("2026-11-05T21:40")
    assert n.ctx()["home"] is False and n.e(n.p1).s.state == "away" and not n.side("left").on


def test_wifi_off_after_a_silent_gap_starts_afresh(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        k = ctl(c)
        post(c, wifi_home=False, sharing=["wifi"])
        t0 = PC.latest(k.store, "p1")["wifi_off_since"]
        k.clock.jump_to(t0 + 3 * 3600)                   # the phone said nothing for 3 h (it could have been home)
        post(c, wifi_home=False)
        assert PC.latest(k.store, "p1")["wifi_off_since"] > t0
        assert ctx(c).home is None
