"""Lab: replay must tell the same story as the live night, and the per-minute work must stay cheap."""

from collections import Counter


import embertide.engine.summary as summary_mod
from lab import Lab, Sleeper, ts


async def test_replay_matches_live_over_several_nights(tmp_path):
    """A hot sleeper in Suggest mode for three nights. Replaying each night with the same settings finds the same
    suggestions at (about) the same moments."""
    lab = Lab(tmp_path, "2026-11-02T20:00", sleepers={"left": Sleeper(pref_c={"*": -1.7}, watch=True, tap_prob=0.4)})
    lab.p1.settings.autopilot.learn_skip = True
    await lab.nights(3, dt=60)
    pol = lab.p1.settings.autopilot
    for night in ("2026-11-02", "2026-11-03", "2026-11-04"):
        a, b = ts(f"{night}T21:00"), ts(f"{night}T21:00") + 12 * 3600
        live = sorted(x["ts"] for x in lab.decisions("p1", "suggested") if a <= x["ts"] <= b)
        e = lab.c.engine_for("p1")
        rep = sorted(d["ts"] for d in lab.c.autopilot.replay(lab.p1, a, b, pol, step_s=60, wake=None) if d["status"] == "suggested")
        assert live, night
        assert abs(len(live) - len(rep)) <= 1, (night, len(live), len(rep))
        near = sum(1 for t in live if any(abs(t - r) <= 180 for r in rep))
        assert near >= len(live) - 1, (night, [round((t - a) / 60) for t in live], [round((r - a) / 60) for r in rep])


async def test_replay_is_read_only(tmp_path):
    lab = Lab(tmp_path, "2026-11-02T20:00", sleepers={"left": Sleeper(pref_c={"*": -1.7}, tap_prob=0.4)})
    lab.p1.settings.autopilot.learn_skip = True
    await lab.nights(1)
    before = len(lab.store.events(limit=100000)), lab.store.get("nights")
    lab.c.autopilot.replay(lab.p1, ts("2026-11-02T21:00"), ts("2026-11-03T08:00"), lab.p1.settings.autopilot)
    assert (len(lab.store.events(limit=100000)), lab.store.get("nights")) == before


# fixed: one cache entry per person
async def test_two_people_dont_thrash_the_hourly_caches(tmp_path, monkeypatch):
    lab = Lab(tmp_path, "2026-11-02T20:00", sleepers={"left": Sleeper(), "right": Sleeper()})
    await lab.nights(1)
    calls = Counter()
    real = summary_mod.recent

    def counting(store, person, *a, **k):
        calls[person.id] += 1
        return real(store, person, *a, **k)
    monkeypatch.setattr(summary_mod, "recent", counting)
    now = ts("2026-11-03T23:30")
    for _ in range(5):                                   # five minutes of ticks within one hour
        for p in lab.hh.people:
            lab.c.autopilot.baseline(p, now)
            lab.c.autopilot.learning(p, now)
        now += 60
    assert calls["p1"] <= 1 and calls["p2"] <= 1, dict(calls)
