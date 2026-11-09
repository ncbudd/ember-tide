"""Lab: in-bed decisions when several devices disagree.

Bed strip + radar + bed scale per side, with each device's own weight and timing. Covers disagreement, an empty side
that blips, a stuck or silent sensor, a pet on the bed, two people echoing on the strip, and different vote weights.
"""


from embertide.sources import SourceConfig
from lab import Lab


def add(lab, sid, kind, zone, **tuning):
    role = {"radar": "presence", "motion": "presence", "pad": "presence", "scale": "weight", "custom": "presence"}[kind]
    c = SourceConfig(id=sid, name=sid, driver="sim", role=role, kind=kind, zone=zone, tuning=tuning)
    lab.sources.configs.append(c)
    return c


def feed(lab, c, v):
    lab.sources._sample(c, v, lab.clock.now_ts())


async def run(lab, minutes, feeds=(), dt=30):
    """Step the world, re-sending each device's reading every step (like a device that reports every 30 s)."""
    for _ in range(int(minutes * 60 / dt)):
        for c, v in feeds:
            feed(lab, c, v() if callable(v) else v)
        await lab.step(1, dt)


def bed_strip(lab, zone, occupied):
    lab.bed.set_presence(zone, occupied)


# ───────── disagreement ─────────
async def test_scale_and_radar_outvote_a_bed_strip_that_misses_you(tmp_path):
    """The strip loses a light, still sleeper; radar and scale both see them."""
    lab = Lab(tmp_path, "2026-11-05T22:00")
    radar, scale = add(lab, "rad", "radar", "left"), add(lab, "kg", "scale", "left")
    bed_strip(lab, "left", False)
    await run(lab, 3, [(radar, 1.0), (scale, 70.0)])
    f = lab.c.fused["left"]
    assert f["present"] is True, f                       # 0.35 + 0.45 against the strip's 0.5


async def test_strip_and_scale_beat_a_radar_that_sees_the_cat(tmp_path):
    lab = Lab(tmp_path, "2026-11-05T22:00")
    radar, scale = add(lab, "rad", "radar", "left"), add(lab, "kg", "scale", "left")
    bed_strip(lab, "left", False)
    await run(lab, 3, [(radar, 1.0), (scale, 4.5)])      # a 4.5 kg cat: radar says yes, scale says no
    assert lab.c.fused["left"]["present"] is False


async def test_custom_weights_change_who_wins(tmp_path):
    lab = Lab(tmp_path, "2026-11-05T22:00")
    radar = add(lab, "rad", "radar", "left", weight=1.0)
    lab.sources.calibration.weights["bed"] = 0.2
    bed_strip(lab, "left", False)
    await run(lab, 2, [(radar, 1.0)])
    assert lab.c.fused["left"]["present"] is True
    radar.tuning["weight"] = 0.1
    await run(lab, 2, [(radar, 1.0)])
    assert lab.c.fused["left"]["present"] is False


async def test_weight_zero_device_has_no_say(tmp_path):
    lab = Lab(tmp_path, "2026-11-05T22:00")
    radar = add(lab, "rad", "radar", "left", weight=0.0)
    bed_strip(lab, "left", False)
    await run(lab, 2, [(radar, 1.0)])
    assert lab.c.fused["left"]["present"] is False


# ───────── timing quirks ─────────
async def test_radar_hold_bridges_a_still_sleeper(tmp_path):
    """Radar drops out for 30 s now and then; with the hold it keeps voting 'in bed'."""
    lab = Lab(tmp_path, "2026-11-05T22:00")
    radar = add(lab, "rad", "radar", "left", weight=1.0, hold_s=45, min_on_s=0)
    lab.sources.calibration.weights["bed"] = 0.01
    bed_strip(lab, "left", True)
    seq = iter([1, 1, 0, 1, 1, 0, 1, 1, 1, 0] * 10)
    seen = []
    for _ in range(40):
        feed(lab, radar, float(next(seq)))
        await lab.step(1, 30)
        seen.append(lab.c.fused["left"]["present"])
    assert all(seen[2:]), seen


async def test_radar_min_on_ignores_someone_leaning_over(tmp_path):
    lab = Lab(tmp_path, "2026-11-05T22:00")
    radar = add(lab, "rad", "radar", "left", weight=1.0, min_on_s=60, hold_s=0)
    lab.sources.calibration.weights["bed"] = 0.01
    bed_strip(lab, "left", False)
    feed(lab, radar, 1.0)
    await lab.step(1, 20)
    feed(lab, radar, 0.0)
    await lab.step(1, 20)
    assert lab.c.fused["left"]["present"] is False


async def test_scale_hysteresis_stops_flicker(tmp_path):
    lab = Lab(tmp_path, "2026-11-05T22:00")
    scale = add(lab, "kg", "scale", "left", weight=1.0, threshold_kg=20, hysteresis_kg=5, min_on_s=0)
    lab.sources.calibration.weights["bed"] = 0.01
    bed_strip(lab, "left", False)
    out = []
    for kg in [25, 25, 18, 22, 17, 19, 16, 14, 13]:
        feed(lab, scale, float(kg))
        await lab.step(1, 30)
        out.append(lab.c.fused["left"]["present"])
    assert out[:6] == [True] * 6 and out[-1] is False, out


async def test_a_stale_device_drops_out_of_the_vote(tmp_path):
    lab = Lab(tmp_path, "2026-11-05T22:00")
    radar = add(lab, "rad", "radar", "left", weight=1.0, max_age_s=120, min_on_s=0)
    lab.sources.calibration.weights["bed"] = 0.4
    bed_strip(lab, "left", False)
    feed(lab, radar, 1.0)
    await lab.step(1, 30)
    assert lab.c.fused["left"]["present"] is True
    await run(lab, 5)                                   # radar goes silent (stuck at 'present')
    f = lab.c.fused["left"]
    assert f["present"] is False and not any(v["signal"].endswith(":rad") for v in f["votes"]), f


async def test_stuck_radar_without_max_age_is_outvoted(tmp_path):
    """An event-only radar (max age 0 keeps its last state) stuck on 'present' all day must not start a night."""
    lab = Lab(tmp_path, "2026-11-05T21:00")
    radar, scale = add(lab, "rad", "radar", "left"), add(lab, "kg", "scale", "left")
    bed_strip(lab, "left", False)
    feed(lab, radar, 1.0)
    await run(lab, 60, [(scale, 1.5)])
    assert not lab.c.engine_for("p1").s.started


# ───────── the bed's own quirks ─────────
async def test_empty_side_blips_never_start_a_night(tmp_path):
    lab = Lab(tmp_path, "2026-11-05T22:00", blips=True, seed=5)
    lab.bed.blips = True
    bed_strip(lab, "left", True)
    bed_strip(lab, "right", False)
    await run(lab, 120, dt=30)
    assert lab.c.engine_for("p1").s.started
    assert not lab.c.engine_for("p2").s.started
    assert not [x for x in lab.events("rhythm_started", "p2")]


async def test_echo_of_one_sleeper_on_both_pads(tmp_path):
    """One person in bed shows on both strips; only their side starts."""
    lab = Lab(tmp_path, "2026-11-05T22:00", echo=True)
    bed_strip(lab, "left", True)
    await run(lab, 30)
    assert lab.c.engine_for("p1").s.started and not lab.c.engine_for("p2").s.started


async def test_echo_but_the_partners_scale_says_they_are_here(tmp_path):
    """Both in bed, but the strip shows one echo. The right side's scale proves the partner is really there."""
    lab = Lab(tmp_path, "2026-11-05T22:00", echo=True)
    scale_r = add(lab, "kgr", "scale", "right")
    radar_r = add(lab, "radr", "radar", "right")
    bed_strip(lab, "left", True)
    lab.bed.z["right"].occupied = False                  # the strip can't tell: it only sees the echo
    await run(lab, 20, [(scale_r, 62.0), (radar_r, 1.0)])
    assert lab.c.fused["right"]["present"] is True
    assert lab.c.engine_for("p2").s.started


async def test_nothing_known_falls_back_to_the_schedule(tmp_path):
    """All sensors silent after bedtime: the night still starts on schedule (after the 10-minute grace)."""
    lab = Lab(tmp_path, "2026-11-05T21:30")
    lab.bed.z["left"].frozen = True                     # the strip's clock stops: stale → unknown
    lab.bed.scripted = False
    await lab.until("2026-11-05T23:00", dt=60)
    assert lab.c.fused["left"]["present"] is None
    assert lab.c.engine_for("p1").s.started


async def test_light_and_custom_devices_dont_vote_unless_presence(tmp_path):
    lab = Lab(tmp_path, "2026-11-05T22:00")
    light = SourceConfig(id="lt", name="Lamp", driver="sim", role="light", kind="light")
    lab.sources.configs.append(light)
    bed_strip(lab, "left", False)
    await run(lab, 2, [(light, 1.0)])
    assert not any("lt" in v["signal"] for v in lab.c.fused["left"]["votes"])


# fixed: the scale now outweighs the strip by default
async def test_pet_jumps_on_an_empty_side_for_an_hour(tmp_path):
    """A dog on the right side: strip says occupied (it has a heartbeat), scale reads 12 kg. The right side's night
    must not start for the dog."""
    lab = Lab(tmp_path, "2026-11-05T22:00", echo=False)
    scale_r = add(lab, "kgr", "scale", "right")
    bed_strip(lab, "left", True)
    bed_strip(lab, "right", True)                        # the strip sees a body
    await run(lab, 60, [(scale_r, 12.0)])
    assert not lab.c.engine_for("p2").s.started, lab.c.fused["right"]
