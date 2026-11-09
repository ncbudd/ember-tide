"""A night the ring says ran a degree or more warmer than normal (illness, alcohol) is left out of Autopilot's learning;
the few-tenths swings of an ordinary month (often the cycle) stay in."""

from embertide.engine.ring import ring_night, unusual
from embertide.store import Store


def test_unusual_needs_a_full_degree(tmp_path):
    s = Store(tmp_path / "t.db")
    a, b = 1_800_000_000.0, 1_800_000_000.0 + 8 * 3600
    assert unusual(s, "p2", a, b) is None                                    # no ring: nothing to say
    s.sample(b + 600, "wear:p2:temp_dev:Oura", 0.4)                           # luteal-sized: a normal night
    assert unusual(s, "p2", a, b) is None
    s.sample(b + 1200, "wear:p2:temp_dev:Oura", 1.9)               # fever-sized, the latest reading wins
    assert unusual(s, "p2", a, b) == 1.9
    assert unusual(s, "p2", a + 86400, b + 86400) is None                    # the next night isn't affected


class _Clock:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/New_York")


class _C:
    def __init__(self, store):
        self.store, self.clock = store, _Clock()


class _P:
    id = "p2"


def test_ring_card_says_why_a_night_was_left_out(tmp_path):
    s = Store(tmp_path / "t.db")
    a, b = 1_800_000_000.0, 1_800_000_000.0 + 8 * 3600
    s.sample(b, "wear:p2:temp_dev:Oura", 1.9)
    s.sample(b, "wear:p2:readiness:Oura", 24)
    r = ring_night(_C(s), _P(), a, b, bed={"hr_low": 55})
    assert r["unusual"]
    by = {x["key"]: x for x in r["lines"]}
    assert by["temp_dev"]["tone"] == "warn" and "leaves this night out" in by["temp_dev"]["note"]
    assert by["readiness"]["note"].startswith("Pay attention")
    assert ring_night(_C(s), _P(), a + 86400 * 3, b + 86400 * 3) is None     # nothing that night
