"""The Android app's "From this phone" reports: location, home Wi-Fi, calendar, alarm, charging/bedtime, watch, and
bedside dial beacons. Validation, merging into the person's context, staleness, switches turned off, the weak
"phone by the bed" in-bed vote, and privacy of the all-people listing."""

from embertide.auth import Principal, authorize
from embertide.engine import phone_context as PC
from test_autopilot_suite import H, make


def ctl(c):
    return c.app.state.notifier.c


def ctx(c, pid="p1"):
    from embertide.engine.context import person_context
    k = ctl(c)
    p = next(x for x in k.household.people if x.id == pid)
    return person_context(k.sources, k.store, p, k.clock.now_ts())


def post(c, pid="p1", **body):
    r = c.post(f"/api/people/{pid}/phone-context", headers=H, json=body)
    return r


def fresh_client(tmp_path):
    c = make(tmp_path)
    return c


def nogap(monkeypatch):
    import embertide.phone_api as A
    monkeypatch.setattr(A, "MIN_GAP_S", 0)


def test_away_heading_home_wifi_and_staleness(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        assert post(c, home=False, distance_km=12.0, device="Pixel 9 Pro XL", sharing=["location"]).status_code == 200
        x = ctx(c)
        assert x.home is False and x.why == "your phone: 12.0 km away" and x.distance_km == 12.0
        post(c, home=False, distance_km=8.0, heading_home=True, eta_min=18)
        x = ctx(c)
        assert x.home is True and x.heading_home and "on the way home, about 18 min" in x.why
        post(c, home=True)
        assert ctx(c).home is True and ctx(c).why == "your phone: home"
        assert ctx(c).distance_km is None and not ctx(c).heading_home      # the trip is over
        # stale: 7 h later the phone's word no longer counts
        k = ctl(c)
        cur = PC.latest(k.store, "p1")
        for f in cur["ts"]:
            cur["ts"][f] -= 7 * 3600
        assert ctx(c).home is None


def test_wifi_alone_makes_home_but_never_away(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        post(c, wifi_home=True, sharing=["wifi"])
        assert ctx(c).home is True and "home Wi-Fi" in ctx(c).why
        post(c, wifi_home=False)
        assert ctx(c).home is None


def test_calendar_alarm_charging_and_switch_off(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        now = ctl(c).clock.now_ts()
        post(c, evening_event_end=now + 3600, early_start=now + 12 * 3600, next_alarm=now + 11 * 3600, charging=True,
             bedtime_mode=False, watch_connected=True, sharing=["calendar", "alarm", "charging", "watch"])
        x = ctx(c)
        assert x.event_end == now + 3600 and x.busy_until == now + 3600 + PC.TRAVEL_S
        assert x.early_start == now + 12 * 3600 and x.next_alarm == now + 11 * 3600
        assert x.charging is True and x.bedtime_mode is False and x.watch_connected is True
        post(c, sharing=["alarm"])                                  # calendar, charging, watch switched off
        x = ctx(c)
        assert x.event_end is None and x.early_start is None and x.charging is None and x.next_alarm == now + 11 * 3600
        post(c, next_alarm=0)                                       # "no alarm set" clears it
        assert ctx(c).next_alarm is None


def test_validation_and_rate_limit(tmp_path):
    with make(tmp_path) as c:
        now = ctl(c).clock.now_ts()
        assert post(c, next_alarm=now + 10 * 86400).status_code == 422
        assert post(c, distance_km=-1).status_code == 422
        assert c.post("/api/people/p1/phone-context", headers=H, content=b'{"eta_min": NaN}',
                      ).status_code in (400, 422)
        assert post(c, dials=[{"minor": 1, "rssi": 20}]).status_code == 422
        assert post(c, home=True, sharing=["location", "bogus"]).status_code == 200
        assert PC.latest(ctl(c).store, "p1")["sharing"] == ["location"]
        assert post(c, home=True).status_code == 429               # faster than the app ever sends
        assert post(c, pid="nobody", home=True).status_code == 404


def test_only_the_person_or_owner_may_send():
    sleeper = Principal(kind="user", id="u2", name="Sam", role="sleeper", person="p2")
    viewer = Principal(kind="user", id="u3", name="V", role="viewer", person=None)
    shares = lambda pid: (True, True)                               # noqa: E731
    assert authorize(sleeper, "POST", "/api/people/p2/phone-context", shares) is None
    assert authorize(sleeper, "POST", "/api/people/p1/phone-context", shares) is not None
    assert authorize(viewer, "POST", "/api/people/p1/phone-context", shares) is not None
    assert authorize(sleeper, "PUT", "/api/dial-beacons", shares) is not None


def test_all_people_listing_shows_sharing_not_values(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        post(c, home=False, distance_km=40.0, device="Pixel 9 Pro XL", sharing=["location", "alarm"])
        rows = c.get("/api/phone-context").json()
        assert rows[0]["device"] == "Pixel 9 Pro XL" and rows[0]["sharing"] == ["location", "alarm"]
        assert "data" not in rows[0] and "distance" not in str(rows) and "home" not in rows[0]


def test_bedside_dial_decisions(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        k = ctl(c)
        now = k.clock.now_ts()

        def side(dials, charging=True):
            post(c, charging=charging, dials=dials, sharing=["charging", "dial"])
            return PC.bedside_side(k.store, PC.latest(k.store, "p1"), now)
        assert side([{"minor": 1, "rssi": -60}]) == "left"                          # right by the left dial
        assert side([{"minor": 2, "rssi": -82}, {"minor": 1, "rssi": -92}]) == "right"   # clearly stronger
        assert side([{"minor": 1, "rssi": -80}, {"minor": 2, "rssi": -84}]) is None       # can't tell
        assert side([{"minor": 1, "rssi": -60}], charging=False) is None                  # not put down for the night
        assert side([{"minor": 9, "rssi": -50}]) is None                                  # not one of ours
        assert side([]) is None


def test_bedside_is_a_weak_vote_for_own_side_only(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        k = ctl(c)
        left = next(p for p in k.household.people if p.zone == "left")
        post(c, pid=left.id, charging=True, dials=[{"minor": 1, "rssi": -55}], sharing=["charging", "dial"])
        k.fuse("left")
        v = [x for x in k.fused["left"]["votes"] if x["signal"] == "phone:bedside"]
        assert v and v[0]["weight"] == PC.BEDSIDE_WEIGHT and v[0]["value"] == 1.0
        k.fuse("right")
        assert not [x for x in k.fused["right"]["votes"] if x["signal"] == "phone:bedside"]
        # by the RIGHT dial: no vote on either side for the left person (only their own side), never a "not in bed"
        post_gap = PC.latest(k.store, left.id)
        post_gap["data"]["dials"] = [{"minor": 2, "rssi": -50, "seen_at": k.clock.now_ts()}]
        k.fuse("left")
        assert not [x for x in k.fused["left"]["votes"] if x["signal"] == "phone:bedside"]
        # alone it can't outvote a bed strip that says empty: 0.15 against 0.5
        assert PC.BEDSIDE_WEIGHT / (PC.BEDSIDE_WEIGHT + 0.5) < 0.5
        assert ctx(c, left.id).near_dial == "right"                 # context still knows the phone is by the bed


def test_phone_by_the_dial_means_home(tmp_path, monkeypatch):
    nogap(monkeypatch)
    with make(tmp_path) as c:
        post(c, bedtime_mode=True, dials=[{"minor": 1, "rssi": -58}], sharing=["charging", "dial"])
        x = ctx(c)
        assert x.near_dial == "left" and x.home is True and "by the left dial" in x.why


def test_dial_beacon_map(tmp_path):
    with make(tmp_path) as c:
        d = c.get("/api/dial-beacons").json()
        assert d["uuid"] == PC.DIAL_UUID and {x["side"] for x in d["dials"]} == {"left", "right"}
        r = c.put("/api/dial-beacons", headers=H, json=[{"minor": 5, "side": "right", "name": "Sam's dial"}, {"minor": 6, "side": "left"}])
        assert r.status_code == 200 and r.json()["dials"][0]["minor"] == 5
        assert c.put("/api/dial-beacons", headers=H, json=[{"minor": 5, "side": "right"}, {"minor": 5, "side": "left"}]).status_code == 422
        assert c.put("/api/dial-beacons", headers=H, json=[{"minor": 5, "side": "middle"}]).status_code == 422


def test_near_dial_holds_through_a_missed_scan_while_plugged_in():
    """A screen-off scan can miss the phone's own dial; it still lies there as long as it hasn't been unplugged."""
    from embertide.engine import phone_context as PC

    class S(dict):
        def get(self, k, d=None): return super().get(k, d)
        def put(self, k, v): self[k] = v
    st = S()
    st.put("dial_beacons", [{"minor": 1, "side": "right", "name": "A"}, {"minor": 2, "side": "left", "name": "B"}])
    t = 1_800_000_000.0
    PC.update(st, "p1", {"charging": True}, t)
    PC.update(st, "p1", {"dials": [{"minor": 1, "rssi": -40, "seen_at": t + 60}]}, t + 60)
    assert PC.bedside_side(st, PC.latest(st, "p1"), t + 120) == "right"
    # next scan only hears the far dial faintly: still by the right dial
    PC.update(st, "p1", {"charging": True, "dials": [{"minor": 2, "rssi": -93, "seen_at": t + 960}]}, t + 960)
    assert PC.bedside_side(st, PC.latest(st, "p1"), t + 1000) == "right"
    # unplugged (picked up): gone at once
    PC.update(st, "p1", {"charging": False}, t + 1100)
    assert PC.bedside_side(st, PC.latest(st, "p1"), t + 1110) is None
    # plugged in somewhere else, and a scan hears nothing near: no sticky carry-over
    PC.update(st, "p1", {"charging": True, "dials": []}, t + 1200)
    assert PC.bedside_side(st, PC.latest(st, "p1"), t + 1210) is None


def test_near_dial_holds_when_the_phone_goes_quiet_but_last_said_plugged_in():
    """Android may not run the phone's check for over an hour; the last word was 'plugged in, by the right dial'."""
    from embertide.engine import phone_context as PC

    class S(dict):
        def get(self, k, d=None): return super().get(k, d)
        def put(self, k, v): self[k] = v
    st = S()
    st.put("dial_beacons", [{"minor": 1, "side": "right", "name": "A"}, {"minor": 2, "side": "left", "name": "B"}])
    t = 1_800_000_000.0
    PC.update(st, "p1", {"charging": True, "dials": [{"minor": 1, "rssi": -40, "seen_at": t}]}, t)
    assert PC.bedside_side(st, PC.latest(st, "p1"), t + 90 * 60) == "right"          # 90 min of silence
    assert PC.bedside_side(st, PC.latest(st, "p1"), t + 4 * 3600) is None           # but not forever
    PC.update(st, "p1", {"charging": False}, t + 100 * 60)
    assert PC.bedside_side(st, PC.latest(st, "p1"), t + 101 * 60) is None           # unplugged when it did report
