"""One side's tick going wrong: an event that can't be written never freezes that side's bed commands, and a side
whose update keeps failing is shown in the snapshot and said once."""

import logging

import pytest

from embertide.notify import TICK_FAIL_AFTER, Notifier
from test_scenarios import Night


async def test_an_event_that_cant_be_written_doesnt_freeze_the_side(tmp_path):
    logging.disable(logging.CRITICAL)
    try:
        n = Night(tmp_path, "2026-11-05T21:30")
        n.present("left", True)
        real = n.store.event
        fail = {"once": True}

        def flaky(ts, kind, *a, **k):
            if fail["once"] and kind == "rhythm_started":
                fail["once"] = False
                raise RuntimeError("database is locked")
            return real(ts, kind, *a, **k)
        n.store.event = flaky
        await n.until("2026-11-05T22:30")
        e = n.e(n.p1)
        assert e.s.started and not fail["once"]
        assert not e.s.events and not n.c._person_errors                # nothing left half-written, no failing ticks
        await n.until("2026-11-06T06:40")
        assert e.s.state == "rhythm" and n.side("left").setpoint == pytest.approx(e.s.target_c, abs=0.1)   # commands kept going
    finally:
        logging.disable(logging.NOTSET)


async def test_a_side_that_keeps_failing_shows_in_the_snapshot_and_is_said_once(tmp_path):
    logging.disable(logging.CRITICAL)
    try:
        n = Night(tmp_path, "2026-11-05T19:00")
        nt = Notifier(n.c, None)
        n.c.notifier = nt
        boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad setting"))      # noqa: E731
        n.c.engines[n.p2.id].tick = boom
        await n.step(3, dt=1)
        assert n.c.snapshot()["system"]["tick_errors"] == {n.p2.id: 3}
        assert not n.events("notification")
        await n.step(TICK_FAIL_AFTER, dt=1)
        sent = [x for x in n.events("notification") if x.get("about") == "problem"]
        assert len(sent) == 1 and sent[0]["title"] == f"{n.p2.name}'s side isn't updating"
        await n.step(TICK_FAIL_AFTER, dt=1)
        assert len([x for x in n.events("notification") if x.get("about") == "problem"]) == 1      # once per night
        del n.c.engines[n.p2.id].tick                                                   # fixed: the count clears
        await n.step(1, dt=1)
        assert n.c.snapshot()["system"]["tick_errors"] == {}
    finally:
        logging.disable(logging.NOTSET)
