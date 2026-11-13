"""Second pass, area 6: which notifications fire in the whole-night scenarios, when, with which page and expiry.
The 'fix this when you can' kinds (wearable, bed_silent, sensor, target, account, reach, bridge, storage) never wake anyone."""

import time

from sim2kit import catch, hm, kinds, reading
from simnight import Plan, Sim, ts

NEW_KINDS = {"wearable", "bed_silent", "sensor", "target", "account", "reach", "bridge", "storage"}
DAYTIME_KINDS = {"wearable", "sensor", "account", "reach", "bridge", "storage"}


def hour(t: float) -> int:
    return int(hm(t)[:2])


async def test_fix_when_you_can_notes_only_in_calm_daytime_and_once(tmp_path):
    """The link to Home Assistant down, the disk nearly full, an account needing sign-in and three undeliverable messages in a
    row, all true from 21:00 on; a night in between. Each is said once, between 9 AM and 7 PM, with nobody's night running."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    nt = r.c.notifier
    nt.bridge_status = lambda: (True, "not connected")
    nt.disk_usage = lambda path: (100 << 30, 99 << 30, 1 << 29)
    nt.account_states = lambda: [("p1", "Oura", True)]
    r.store.put("notify_reach", {"p2": 5})
    got = catch(r)
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "22:00")
    await r.run("2026-11-06T20:00", script=plan)
    fired = [n for n in got if n.kind in NEW_KINDS]
    seen = {n.kind for n in fired}
    assert seen == {"bridge", "storage", "account", "reach"}, [(hm(n.at), n.kind) for n in fired]
    for n in fired:
        assert 9 <= hour(n.at) < 19, (n.kind, hm(n.at))
        assert n.expires is None                                       # nothing here is about one night
        assert n.link.startswith("/") and n.link != "/", (n.kind, n.link)
    for k in seen:
        assert [x.kind for x in fired].count(k) == 1, k


async def test_a_note_never_goes_out_while_either_night_is_running(tmp_path):
    """Sam lies in; Alex is up and about by 07:30 (night done). Whenever a fix-when-you-can note goes out, nobody's night is
    running at that moment."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    nt = r.c.notifier
    nt.bridge_status = lambda: (True, "not connected")
    got = catch(r)
    plan = Plan().sleep("left", "22:40", "06:50").sleep("right", "22:40", "10:40")
    await r.run("2026-11-06T15:00", script=plan)
    b = [n for n in got if n.kind == "bridge"]
    assert len(b) == 1
    for p in (r.p1, r.p2):
        assert r.at(p, hm(b[0].at) and "2026-11-06T" + hm(b[0].at))[0] not in ("rhythm", "out_of_bed_hold")


async def test_partner_not_home_gets_one_precool_message_that_ends_with_the_night(tmp_path):
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.c.home[r.p2.id] = False
    got = catch(r)
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "22:00")
    await r.run("2026-11-06T07:30", script=plan)
    pre = [n for n in got if n.kind == "away_precool"]
    assert len(pre) == 1 and pre[0].person == "p2"
    assert pre[0].expires is not None
    left_s = pre[0].expires - time.time()
    assert 0 < left_s < 16 * 3600, left_s                              # gone when the night ends (before noon)
    assert pre[0].link.startswith("/")
    assert not [n for n in got if n.person == "p1" and n.kind in NEW_KINDS | {"away_precool"}]


async def test_a_pad_that_will_not_heat_is_held_for_the_morning(tmp_path):
    """The pad reads 15 C whatever is asked: 'target' trouble goes to the sleeper but is not sent in the night."""
    r = Sim(tmp_path, "2026-11-05T21:00")
    got = catch(r)
    plan = Plan().sleep("left", "22:40", "06:50")
    plan.at("22:00", lambda x: setattr(x.bed, "stuck_measured", 15.0))
    await r.run("2026-11-06T09:00", script=plan)
    tgt = [n for n in got if n.kind == "target"]
    sent_at_night = [e for e in r.events("notification") if e.get("about") == "target"
                     and ts("2026-11-05T22:30") <= e["ts"] < ts("2026-11-06T07:00")]
    assert not sent_at_night, [(hm(e["ts"]), e["title"]) for e in sent_at_night]
    for n in tgt:
        assert n.link in ("/studio/hub", "/settings#hub"), n.link
        assert n.expires is None


async def test_everything_at_once_early_reader_pet_restart_alarm_sends_nothing_at_night(tmp_path):
    from embertide.models import Pet
    cat = Pet(name="Biscuit", kind="cat", weight_kg=4.5, sleeps_on_bed="yes", side="either")
    r = Sim(tmp_path, "2026-11-05T21:00")
    r.hh.pets = [cat]
    got = catch(r)
    plan = reading(Plan().sleep("left", "21:10", "06:50"), "left", "21:10", "21:40")
    plan.at("21:00", lambda x: x.bed.pets.update(right=165))
    plan.at("23:45", lambda x: (x.bed.pets.pop("right", None), setattr(x.bed, "echo", False))).at("23:50", lambda x: setattr(x.bed, "echo", True))
    plan.at("22:05", lambda x: x.phone(x.p1, next_alarm=ts("2026-11-06T05:30")))
    plan.at("01:00", lambda x: (x.crash(), None)).at("01:03", lambda x: None)
    await r.run("2026-11-06T01:03", script=plan)
    r.boot()
    got2 = catch(r)
    await r.run("2026-11-06T07:30", script=plan)
    sent = [(hm(e["ts"]), e["about"], e.get("person")) for e in r.events("notification")
            if ts("2026-11-05T22:30") <= e["ts"] < ts("2026-11-06T07:00")]
    held = [(hm(e["ts"]), e["about"]) for e in r.events("notification_held")]
    assert [x for x in sent if x[1] not in ("morning",)] == [], sent
    assert r.started_count(r.p1) == 1
