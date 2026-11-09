"""Database housekeeping: old device readings become 5-minute averages, old telemetry one row a minute, recent data
untouched, and repeated runs only do new work."""

from embertide.store import Store

DAY = 86400.0


def test_compact_downsamples_old_data_only(tmp_path):
    s = Store(tmp_path / "s.db")
    now = 100 * DAY
    old, new = now - 40 * DAY, now - 2 * DAY
    s.samples_many([(old + i * 15, "room", 20.0 + (i % 2)) for i in range(400)])          # 100 min at 15 s
    s.samples_many([(new + i * 15, "room", 21.0) for i in range(400)])
    s.telemetry([(now - 70 * DAY + i * 15, "left", 1, 18.0, 18.2, 1, 55, 14, 0, 18.0, "deep", 1, "p1") for i in range(400)])
    r = s.compact(now)
    assert r["samples_before"] == 400 and 20 <= r["samples_after"] <= 22                  # one per 5 minutes
    old_pts = s.samples("room", old - 1, old + 7000)
    assert all(abs(v - 20.5) < 0.01 for _, v in old_pts)                                   # averages kept
    assert len(s.samples("room", new - 1, new + 7000)) == 400                              # recent untouched
    assert r["telemetry_removed"] == 300                                                   # 4 rows a minute → 1
    again = s.compact(now + 3600)
    assert again["samples_before"] == 0 and again["telemetry_removed"] == 0


def test_slow_sensors_store_on_change_or_every_few_minutes(tmp_path):
    from embertide.clock import FixedClock
    from embertide.sources import SourceConfig, SourceManager
    clock = FixedClock(1_800_000_000.0, "America/New_York")
    st = Store(tmp_path / "s.db")
    m = SourceManager(st, clock, lambda: None)
    room = SourceConfig(id="r", name="Room", driver="sim", role="room_temp", kind="room")
    m.configs = [room]
    for i in range(240):                                  # an hour of readings every 15 s, steady temperature
        m._sample(room, 21.0, clock.now_ts())
        clock.advance(15)
    assert len(st.samples("r", 0, 2e9)) <= 14             # every 5 min, not 240
    m._sample(room, 23.0, clock.now_ts())                 # a real change is stored at once
    assert st.samples("r", 0, 2e9)[-1][1] == 23.0
