"""Lab: wearables coming, going and being swapped over many nights.

A watch taken off mid-week, a source deleted or switched off, a Galaxy Watch replaced by a ring that reads skin
temperature 0.6 °C higher (or heart rate 4 bpm lower), Health Connect imports replaced by the live watch path, two
devices at once at different sampling rates, "Which to trust more" changed mid-week, and a recalibration after a
switch. What must hold for a comfortable sleeper: the learned normal range isn't poisoned by the new device's level,
Autopilot doesn't start nudging, nothing stale is used, duplicates count once, the comparison names the device, and
the person is told the new device is being learned.
"""

from collections import Counter

from embertide.engine.autopilot import RELEARN_NIGHTS, signals_at
from lab import Lab, Sleeper, Wearable, ts

START = "2026-11-02T20:00"          # a Monday


def acted(lab, pid="p1"):
    return [x for x in lab.decisions(pid) if x["status"] in ("suggested", "applied", "learning")]


def skin_pushes(lab, pid="p1", since=0.0):
    """Skin temperature behind something Autopilot did or noted (a blocked candidate in the wake phase isn't)."""
    return [x for x in acted(lab, pid) if x["ts"] >= since and any(r["signal"] == "skin_temp" for r in x["reasons"])]


def why(decs):
    return Counter((x["status"], x["direction"], tuple(r["signal"] for r in x["reasons"]), tuple(x.get("blocked_by") or ())) for x in decs)


def restless_sleeper(*wear: Wearable) -> Sleeper:
    """Comfortable on the rhythm, but moves a lot every night: restlessness alone must never be acted on, so the
    only thing that could tip Autopilot over is a fake skin-temperature signal from a device change."""
    return Sleeper(restless=0.45, wearables=list(wear))


async def week_then(lab, first: int, then: int, change, morning=None):
    await lab.nights(first, morning=morning)
    n0 = len(acted(lab))
    change(lab)
    await lab.nights(then, morning=morning)
    return n0


def morning_items(lab, d):
    r = lab.suggest(lab.p1, show_all=True)
    return {"date": d.isoformat(), "items": [(i["kind"], i.get("phase"), i["direction"]) for i in r["items"]]}


# ───────── swapping a watch for a ring that reads differently ─────────
async def test_ring_reading_higher_skin_does_not_start_nudges(tmp_path):
    """Galaxy Watch for the learning week, then an Oura ring (through the cloud) that reads skin +0.6 °C."""
    lab = Lab(tmp_path, START, sleepers={"left": restless_sleeper(Wearable("samsung SM-L340"))})

    def swap(lab):
        lab.wearable("left", "samsung SM-L340").on = False
        lab.sleepers["left"].wearables.append(Wearable("Oura", skin_bias=0.6, every_s=300))
    before = await week_then(lab, 7, 4, swap, morning=morning_items)
    after = acted(lab)[before:]
    assert after == [], why(after)
    assert skin_pushes(lab) == [], why(skin_pushes(lab))
    assert all(m["items"] == [] for m in lab.mornings), [m for m in lab.mornings if m["items"]]
    # the learned normal range stays on one level: the ring's nights are lined up with the watch's
    band = lab.c.autopilot.baseline(lab.p1, lab.clock.now_ts())["band"]["skin"]
    assert band[1] - band[0] < 0.5, band


async def test_ring_reading_lower_skin_does_not_look_cold(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": restless_sleeper(Wearable("Pixel Watch 3"))})

    def swap(lab):
        lab.wearable("left", "Pixel Watch 3").on = False
        lab.sleepers["left"].wearables.append(Wearable("Oura", skin_bias=-0.7, every_s=300, gap_prob=0.1))
    before = await week_then(lab, 7, 4, swap)
    assert acted(lab)[before:] == [], why(acted(lab)[before:])


async def test_new_device_is_learned_for_a_few_nights_then_trusted(tmp_path):
    """While the ring is new, the person is told so, and its skin readings are not compared with the old watch's
    normal range. After RELEARN_NIGHTS nights it is lined up with the old level and the note goes away."""
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(wearables=[Wearable("samsung SM-L340")])})
    await lab.nights(7)
    lab.wearable("left", "samsung SM-L340").on = False
    lab.sleepers["left"].wearables.append(Wearable("Oura", skin_bias=0.6, every_s=300))
    await lab.nights(1)
    st = lab.c.autopilot.wearable_status(lab.p1, lab.clock.now_ts())
    new = [d for d in st if d["learning"]]
    assert [d["name"] for d in new] == ["Oura"], st
    assert new[0]["kind"] == "ring" and "learning its readings" in new[0]["text"] and "ring" in new[0]["text"].lower()
    assert new[0]["nights_left"] == RELEARN_NIGHTS - 1
    old = next(d for d in st if d["name"] == "samsung SM-L340")
    assert not old["learning"]
    await lab.nights(RELEARN_NIGHTS)
    st = lab.c.autopilot.wearable_status(lab.p1, lab.clock.now_ts())
    assert not any(d["learning"] for d in st), st
    # lined up: tonight's ring readings sit inside the normal range learned (mostly) from the watch
    now = lab.clock.now_ts()
    band = lab.c.autopilot.baseline(lab.p1, now)["band"]["skin"]
    night = lab.c.autopilot.skin(lab.p1, now - 12 * 3600, now)
    mid = sorted(v for _, v in night)[len(night) // 2]
    assert band[0] - 0.1 <= mid <= band[1] + 0.1, (band, mid)


async def test_insights_tell_the_person_about_the_new_device(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(wearables=[Wearable("samsung SM-L340")])})
    await lab.nights(7)
    lab.wearable("left", "samsung SM-L340").on = False
    lab.sleepers["left"].wearables.append(Wearable("Oura", skin_bias=0.6, every_s=300))
    await lab.nights(1)
    from embertide.studio_api import wearable_cards
    cards = wearable_cards(lab.c, lab.p1, lab.clock.now_ts())
    assert any(c["id"] == "wearable_learning" and "Oura" in c["title"] and "learning" in c["title"].lower() for c in cards), cards
    assert all(c["proposal"] is None for c in cards)


# ───────── heart rate: a new device reading lower, and the live watch path ─────────
async def test_mid_night_switch_to_a_lower_reading_device_is_not_a_heart_rate_change(tmp_path):
    """Health Connect watch heart rate until 2 am, then the live watch path reading 6 bpm higher. Inside one
    night that must not look like a heart-rate rise."""
    lab = Lab(tmp_path, START, sleepers={"left": restless_sleeper(Wearable("Galaxy Watch", skin=False, hr=True, every_s=300))})
    lab.p1.settings.autopilot.trust_more = "wearable"
    await lab.nights(7)
    n0 = len(acted(lab))
    await lab.until("2026-11-09T21:00")
    await lab.until("2026-11-10T02:00")
    lab.wearable("left", "Galaxy Watch").on = False
    lab.sleepers["left"].wearables.append(Wearable("live", skin=False, hr=True, hr_bias=6, every_s=120, live=True))
    await lab.until("2026-11-10T07:30")
    now = lab.clock.now_ts()
    hr = lab.c.autopilot.wear_hr(lab.p1, now - 6 * 3600, now)
    vals = [v for t, v in hr if t >= ts("2026-11-10T03:00")]
    olds = [v for t, v in hr if ts("2026-11-10T00:00") <= t < ts("2026-11-10T02:00")]
    assert abs(sorted(vals)[len(vals) // 2] - sorted(olds)[len(olds) // 2]) < 3, "the live path wasn't lined up with the watch"
    hrs = [x for x in acted(lab)[n0:] if any(r["signal"] == "hr" for r in x["reasons"])]
    assert hrs == [], why(hrs)


async def test_new_ring_reading_4_bpm_lower(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": restless_sleeper(Wearable("Galaxy Watch", skin=False, hr=True, every_s=300))})

    def swap(lab):
        lab.wearable("left", "Galaxy Watch").on = False
        lab.sleepers["left"].wearables.append(Wearable("Oura", skin=False, hr=True, hr_bias=-4, every_s=600, gap_prob=0.2))
    before = await week_then(lab, 7, 3, swap)
    assert acted(lab)[before:] == [], why(acted(lab)[before:])


# ───────── two at once ─────────
async def test_watch_and_ring_together_at_different_rates(tmp_path):
    """The watch every 2 min, the ring every 5 min reading 0.6 °C / 4 bpm apart. Merged onto one level, with the
    same reading arriving under two names counted once."""
    lab = Lab(tmp_path, START, sleepers={"left": restless_sleeper(
        Wearable("Galaxy Watch", hr=True, every_s=120),
        Wearable("Oura", hr=True, skin_bias=0.6, hr_bias=-4, every_s=300))})
    await lab.nights(9)
    assert skin_pushes(lab) == [], why(skin_pushes(lab))
    assert [x for x in acted(lab) if x["status"] != "learning"] == [], why(acted(lab))
    now = lab.clock.now_ts()
    skin = lab.c.autopilot.skin(lab.p1, now - 12 * 3600, now)
    w = Window_from(lab, now - 9 * 3600, skin)
    assert w is None or abs(w) < 0.3


def Window_from(lab, at, skin):
    from embertide.engine.autopilot import Window
    w = Window(now=at, rows=[], in_bed=at - 4 * 3600, state="rhythm", phase="deep", flash_active=False, events=[],
               skin=[s for s in skin if s[0] <= at], persona="neutral", wake=None)
    return signals_at(w)["skin_delta"]


def test_same_reading_under_two_names_counts_once(tmp_path):
    """The live watch path and a Health Connect import of the same watch: the same readings, a second apart."""
    from embertide.engine import watch
    lab = Lab(tmp_path, "2026-11-06T09:00")
    s = ts("2026-11-05T23:00")
    watch.record(lab.store, "p1", [(s + i * 60 + 0.4, 60.0 + (i % 5)) for i in range(120)], [], s + 7200)
    from embertide.autopilot_api import WearableBody, _import_wearable
    r = _import_wearable(lab.c, lab.p1, WearableBody(device="samsung SM-L340", metric="hr", align="off",
                                                     points=[[s + i * 60 + 1, 60.0 + (i % 5)] for i in range(120)]))
    assert r["stored"] == 0, r
    assert len(lab.c.autopilot.wear_hr(lab.p1, s - 60, s + 7200)) == 120


# ───────── taking it off, deleting it, switching it off ─────────
async def test_watch_taken_off_mid_week_leaves_nothing_stale(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": restless_sleeper(Wearable("Galaxy Watch", hr=True))})
    await lab.nights(7)
    lab.wearable("left", "Galaxy Watch").on = False
    await lab.nights(3)
    now = lab.clock.now_ts()
    assert lab.c.autopilot.skin(lab.p1, now - 6 * 3600, now) == []
    assert lab.c.autopilot.wear_hr(lab.p1, now - 6 * 3600, now) == []
    st = lab.c.autopilot.wearable_status(lab.p1, now)
    gone = next(d for d in st if d["name"] == "Galaxy Watch")
    assert gone["quiet"] and "hasn't sent anything" in gone["text"], gone
    assert acted(lab)[0:] == [] or all(x["status"] == "learning" for x in acted(lab)), why(acted(lab))


async def test_deleted_or_switched_off_source_is_not_read(tmp_path):
    from embertide.sources import SourceConfig
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper()})
    cfg = SourceConfig(id="haskin", name="Watch skin (HA)", driver="sim", role="skin_temp", zone="left", person="p1", kind="wearable")
    lab.sources.configs.append(cfg)
    s = ts("2026-11-02T23:00")
    lab.store.samples_many([(s + i * 120, "haskin", 34.0) for i in range(60)])
    lab.store.samples_many([(s + i * 120, "hahr", 58.0) for i in range(60)])
    lab.sources.configs.append(SourceConfig(id="hahr", name="Watch HR (HA)", driver="sim", role="wearable_hr", zone="left", person="p1", kind="wearable"))
    assert lab.c.autopilot.skin(lab.p1, s, s + 7200) and lab.c.autopilot.wear_hr(lab.p1, s, s + 7200)
    cfg.enabled = False                                               # switched off
    assert lab.c.autopilot.skin(lab.p1, s, s + 7200) == []
    lab.sources.configs[:] = [c for c in lab.sources.configs if c.id != "hahr"]     # deleted
    assert lab.c.autopilot.wear_hr(lab.p1, s, s + 7200) == []


# ───────── settings changing mid-week ─────────
async def test_changing_which_to_trust_mid_week_starts_no_nudges(tmp_path):
    lab = Lab(tmp_path, START, sleepers={"left": restless_sleeper(Wearable("Galaxy Watch", hr=True))})
    await lab.nights(7)
    n0 = len(acted(lab))
    for choice in ("bed", "balanced", "wearable"):
        lab.p1.settings.autopilot.trust_more = choice
        await lab.nights(1)
    assert acted(lab)[n0:] == [], why(acted(lab)[n0:])


async def test_recalibrating_after_a_switch_learns_the_new_device_only(tmp_path):
    from embertide.autopilot_api import _recalibrate
    lab = Lab(tmp_path, START, sleepers={"left": restless_sleeper(Wearable("Galaxy Watch"))})
    await lab.nights(7)
    lab.wearable("left", "Galaxy Watch").on = False
    lab.sleepers["left"].wearables.append(Wearable("Oura", skin_bias=0.6, every_s=300))
    _recalibrate(lab.c, lab.p1)
    await lab.nights(7)
    now = lab.clock.now_ts()
    st = lab.c.autopilot.wearable_status(lab.p1, now)
    assert [d["name"] for d in st if not d["quiet"]] == ["Oura"] and not any(d["learning"] for d in st), st
    band = lab.c.autopilot.baseline(lab.p1, now)["band"]["skin"]
    assert band[0] > 34.3, band                                        # the ring's own level, the watch forgotten
    n0 = len(acted(lab))
    await lab.nights(2)
    assert acted(lab)[n0:] == [], why(acted(lab)[n0:])


# ───────── the comparison says which device each night used ─────────
async def test_comparison_names_the_device_each_night(tmp_path):
    from embertide.engine.compare import compare_night
    lab = Lab(tmp_path, START, sleepers={"left": Sleeper(wearables=[Wearable("samsung SM-L340", hr=True)])})
    await lab.nights(2)
    lab.wearable("left", "samsung SM-L340").on = False
    lab.sleepers["left"].wearables.append(Wearable("Oura", hr=True, every_s=300))
    await lab.nights(1)
    first = compare_night(lab.c, lab.p1, "2026-11-02")
    last = compare_night(lab.c, lab.p1, "2026-11-04")
    assert [d["name"] for d in first["devices"]] == ["samsung SM-L340"], first["devices"]
    assert [d["name"] for d in last["devices"]] == ["Oura"] and last["devices"][0]["kind"] == "ring"
    assert any("Oura" in u for u in last["using"]), last["using"]
    from embertide.autopilot_api import _recheck
    assert _recheck(lab.c, lab.p1, "2026-11-04")["devices"] == ["Oura"]
