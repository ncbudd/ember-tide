"""Settling in (controller._start_gate): with a bed radar, the first ~10 minutes decide whose night starts. The
partner rolling onto the empty side doesn't start it; two people seen at once, or the phone going on the charger, does."""

from test_scenarios import Night


def radar(n, monkeypatch):
    seen = {"left": None, "right": None}
    monkeypatch.setattr(n.c, "_radar", lambda zone: seen[zone])
    return seen


async def test_the_radar_backs_the_first_sleeper_after_settling(tmp_path, monkeypatch):
    n = Night(tmp_path, "2026-11-05T23:00", echo=False)
    seen = radar(n, monkeypatch)
    seen["left"], seen["right"] = 1, 0
    n.present("left", True)
    await n.step(10)                                   # 5 min: lying there, still settling
    assert not n.e(n.p1).s.started and n.c.snapshot()["people"][0]["night"]["settle"]["why"] == "settling in"
    await n.step(8)                                    # 9 min: the radar saw them all along
    e = n.e(n.p1).s
    assert e.started and abs(e.in_bed - n.clock.now_ts()) > 7 * 60      # in bed from when they lay down


async def test_a_pad_the_radar_doesnt_back_never_starts(tmp_path, monkeypatch):
    n = Night(tmp_path, "2026-11-05T23:00", echo=False)
    seen = radar(n, monkeypatch)
    seen["left"], seen["right"] = 0, 0                 # the strip lit, the radar sees nobody (weight leaking across)
    n.present("left", True)
    await n.step(30)
    assert not n.e(n.p1).s.started


async def test_rolling_onto_the_partners_side_isnt_the_partner(tmp_path, monkeypatch):
    """2026-11-09 (owner): one person in bed rolls partly onto the other side. That side's pad and radar zone light,
    but the radar sees one body (his own zone empties), so the other side's night doesn't start. When the partner
    really gets in, the radar sees two people and it does."""
    n = Night(tmp_path, "2026-11-05T23:00", echo=False)
    seen = radar(n, monkeypatch)
    seen["left"], seen["right"] = 1, 0
    n.present("left", True)
    await n.step(20)
    assert n.e(n.p1).s.started
    seen["left"], seen["right"] = 0, 1                 # rolled across: his zone empties, hers lights
    n.present("right", True)
    await n.step(30)
    assert not n.e(n.p2).s.started
    assert "only one person" in n.c.snapshot()["people"][1]["night"]["settle"]["why"]
    seen["left"], seen["right"] = 1, 1                 # she gets in: two bodies
    await n.step(20)
    assert n.e(n.p2).s.started


async def test_the_phone_on_the_charger_confirms_at_once(tmp_path, monkeypatch):
    from embertide.engine import phone_context
    n = Night(tmp_path, "2026-11-05T23:00", echo=False)
    seen = radar(n, monkeypatch)
    seen["left"], seen["right"] = 0, 0                 # the radar can't see them (say it's mis-aimed)
    phone_context.update(n.store, "p1", {"charging": True}, n.clock.now_ts())
    n.present("left", True)
    await n.step(10)                                   # past the usual 3-minute start
    assert n.e(n.p1).s.started


async def test_no_radar_starts_as_before(tmp_path, monkeypatch):
    n = Night(tmp_path, "2026-11-05T23:00", echo=False)
    radar(n, monkeypatch)                              # never reports
    n.present("left", True)
    await n.step(10)
    assert n.e(n.p1).s.started


async def test_their_watch_matching_the_bed_opens_the_gate(tmp_path, monkeypatch):
    """The radar can't see them (mis-aimed), but their own watch reads what the pad reads: it's them, the night starts."""
    from test_watch_identity import BAND, DOG, WatchNight, matching
    n = WatchNight(tmp_path, "2026-11-05T22:00", watch_fn=matching, pets=[DOG], band=BAND)
    n.bed.scripted = False
    seen = radar(n, monkeypatch)
    seen["left"], seen["right"] = 0, 0
    await n.step(12)
    n.present("right", True)
    await n.until("2026-11-05T22:40")
    assert n.e(n.p2).s.started
    assert any(e["kind"] == "rhythm_started" and e["person"] == "p2" for e in n.store.events(limit=200))


def _offers(n):
    return [x for x in n.events("notification") if x.get("about") == "not_started"]


async def test_lying_there_long_with_the_radar_refusing_offers_to_start_once(tmp_path, monkeypatch):
    from embertide.notify import Notifier
    n = Night(tmp_path, "2026-11-05T23:00", echo=False)
    nt = n.c.notifier = Notifier(n.c, None)
    seen = radar(n, monkeypatch)
    seen["left"], seen["right"] = 0, 0
    n.present("left", True)
    await n.step(70)                                   # 35 min: not yet
    assert not _offers(n) and not n.e(n.p1).s.started
    await n.step(12)                                   # 41 min: offered, once
    offers = _offers(n)
    assert len(offers) == 1 and offers[0]["title"] == "Your side hasn't started" and offers[0]["person"] == "p1"
    assert not n.e(n.p1).s.started                     # never started on its own
    await n.step(40)
    assert len(_offers(n)) == 1 and not n.e(n.p1).s.started
    from embertide.notify import Action
    nt.use_action(nt.new_action("p1", Action("Start now", "start_now")))
    await n.step(1)
    assert n.e(n.p1).s.started


async def test_no_offer_while_away(tmp_path, monkeypatch):
    from embertide.engine import away
    from embertide.notify import Notifier
    n = Night(tmp_path, "2026-11-05T23:00", echo=False)
    n.c.notifier = Notifier(n.c, None)
    seen = radar(n, monkeypatch)
    seen["left"], seen["right"] = 0, 0
    away.start(n.c, "p1", n.clock.now_ts())
    n.present("left", True)
    await n.step(100)
    assert not _offers(n)


async def test_a_bedtime_after_midnight_still_gets_the_offer(tmp_path, monkeypatch):
    from embertide.notify import Notifier
    n = Night(tmp_path, "2026-11-06T00:10", echo=False)
    n.c.notifier = Notifier(n.c, None)
    seen = radar(n, monkeypatch)
    seen["left"], seen["right"] = 0, 0
    n.present("left", True)
    await n.step(100)
    assert len(_offers(n)) == 1 and not n.e(n.p1).s.started


async def test_no_offer_in_the_last_hour_before_wake(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from embertide.notify import Notifier
    probe = Night(tmp_path / "probe", "2026-11-06T00:10", echo=False)
    await probe.step(1)
    wake = datetime.fromtimestamp(probe.c.snapshot()["people"][0]["schedule"]["wake"], probe.c.clock.tz)
    start = (wake - timedelta(minutes=80)).strftime("%Y-%m-%dT%H:%M")
    n = Night(tmp_path / "late", start, echo=False)
    n.c.notifier = Notifier(n.c, None)
    seen = radar(n, monkeypatch)
    seen["left"], seen["right"] = 0, 0
    n.present("left", True)
    await n.step(90)
    assert not _offers(n) and (n.c.snapshot()["people"][0]["night"]["settle"] or {}).get("offer_start")


async def test_the_offer_can_be_switched_off(tmp_path, monkeypatch):
    from embertide.notify import Notifier
    n = Night(tmp_path, "2026-11-05T23:00", echo=False)
    n.c.notifier = Notifier(n.c, None)
    n.p1.settings.notify.not_started = False
    seen = radar(n, monkeypatch)
    seen["left"], seen["right"] = 0, 0
    n.present("left", True)
    await n.step(100)
    assert not _offers(n) and (n.c.snapshot()["people"][0]["night"]["settle"] or {}).get("offer_start")


def test_old_saved_prefs_load_with_the_offer_on():
    from embertide.models import NotifyPrefs
    assert NotifyPrefs.model_validate({"morning": False, "left_on": True}).not_started is True
    assert NotifyPrefs().not_started is True


async def test_after_a_restart_one_radar_sample_doesnt_decide(tmp_path, monkeypatch):
    """The radar's samples live in memory, but the in-bed time comes back after a restart: the first tick after it
    must not decide on a single sample. It keeps settling in until the radar has watched for a while again."""
    from embertide.engine.controller import Controller
    n = Night(tmp_path, "2026-11-05T23:00", echo=False)
    seen = radar(n, monkeypatch)
    seen["left"], seen["right"] = 0, 0
    n.present("left", True)
    await n.step(20)                                   # 10 min: the radar never backed it
    assert not n.e(n.p1).s.started and n.e(n.p1).s.presence_since
    n.c._persist()
    n.c = Controller(n.hh, n.bed, n.store, n.clock)    # restart
    seen["left"] = 1
    monkeypatch.setattr(n.c, "_radar", lambda zone: seen[zone])
    await n.step(2)
    assert not n.e(n.p1).s.started and n.c.snapshot()["people"][0]["night"]["settle"]["why"] == "settling in"
    await n.step(14)                                   # ~7 min of samples again: now it decides
    assert n.e(n.p1).s.started


async def test_a_partner_up_for_a_moment_isnt_in_bed(tmp_path, monkeypatch):
    """The partner's night is running but they're up (out-of-bed hold): this side needs only its own radar zone."""
    n = Night(tmp_path, "2026-11-05T23:00", echo=False)
    seen = radar(n, monkeypatch)
    seen["left"], seen["right"] = 1, 0
    n.present("left", True)
    await n.step(20)
    assert n.e(n.p1).s.started
    n.present("left", False)
    seen["left"] = 0
    await n.step(4)
    assert n.e(n.p1).s.state == "out_of_bed_hold"
    seen["right"] = 1
    n.present("right", True)
    await n.step(20)
    assert n.e(n.p2).s.started
