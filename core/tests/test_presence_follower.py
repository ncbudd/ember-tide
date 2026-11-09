"""The other pad picking up one sleeper's heartbeat through the mattress, a beat or two off (not an exact copy).

Shape taken from a real night (2026-11-07): the left sleeper steady; the right pad flickering on and off for ~20 min,
then reporting "in bed" with readings within 1-3 of the left's and moving with them.
"""

import random

from embertide.drivers.base import SensorFrame
from embertide.engine.presence import PresenceFilter


def fr(l, r, clock):
    return {"left": SensorFrame(status=l[0], hr=l[1], br=l[2], clock=(clock,)),
            "right": SensorFrame(status=r[0], hr=r[1], br=r[2], clock=(clock,))}


EMPTY = (5, None, None)


def walk(rng, x, lo, hi, step=1.0):
    return max(lo, min(hi, x + rng.uniform(-step, step)))


def test_near_copy_echo_on_the_other_pad_is_ignored():
    rng = random.Random(7)
    pf = PresenceFilter()
    t, hr, br = 0.0, 66.0, 15.0
    for i in range(3600):                                   # one reading a second for an hour
        t += 1
        hr, br = walk(rng, hr, 58, 76, 0.6), walk(rng, br, 12, 20, 0.3)
        left = (0, round(hr), round(br))
        if i < 1200:                                        # first 20 min: the right pad flickers
            right = (0, round(hr + rng.choice([-1, 0, 1])), round(br + rng.choice([-2, 0, 2]))) if (i // 60) % 4 == 0 else EMPTY
        else:                                               # then it says "in bed", a beat or two off
            right = (0, round(hr + rng.choice([-2, -1, 0, 1, 2])), round(br + rng.choice([-2, -1, 0, 1, 2])))
        pf.update(t, fr(left, right, i))
        if i > 1200 + 300:                                  # within 5 min of the right pad settling it's an echo
            assert pf.present("left") is True
            assert pf.present("right") is False, f"right counted as a person at {i} s"


def test_two_real_sleepers_with_similar_readings_both_count():
    rng = random.Random(11)
    pf = PresenceFilter()
    t, a, ab, b, bb = 0.0, 64.0, 15.0, 65.0, 15.0
    for i in range(3 * 3600):                               # three hours, independent bodies, same typical numbers
        t += 1
        a, ab = walk(rng, a, 56, 74, 0.6), walk(rng, ab, 12, 19, 0.3)
        b, bb = walk(rng, b, 56, 74, 0.6), walk(rng, bb, 12, 19, 0.3)
        pf.update(t, fr((0, round(a), round(ab)), (0, round(b), round(bb)), i))
        if i > 60:
            assert pf.present("left") is True and pf.present("right") is True, f"a real sleeper dropped at {i} s"


def test_echo_stops_then_a_real_person_gets_in_on_that_side():
    rng = random.Random(3)
    pf = PresenceFilter()
    t, hr, br = 0.0, 66.0, 15.0
    i = 0

    def step(right_fn):
        nonlocal t, hr, br, i
        t += 1
        i += 1
        hr, br = walk(rng, hr, 58, 76, 0.6), walk(rng, br, 12, 20, 0.3)
        pf.update(t, fr((0, round(hr), round(br)), right_fn(), i))

    for k in range(900):                                    # 15 min of echo: flickering at first, then steady
        step(lambda: (0, round(hr + rng.choice([-1, 0, 1])), round(br + rng.choice([-1, 0, 1])))
             if k > 600 or (k // 60) % 3 == 0 else EMPTY)
    assert pf.present("right") is False
    for _ in range(300):                                    # the echo stops: right really empty for 5 min
        step(lambda: EMPTY)
    p2, b2 = 58.0, 18.0

    def partner():
        nonlocal p2, b2
        p2, b2 = walk(rng, p2, 50, 70, 0.6), walk(rng, b2, 14, 22, 0.3)
        return (0, round(p2), round(b2))

    for _ in range(30):                                     # a real person gets in there
        step(partner)
    assert pf.present("right") is True and pf.present("left") is True


def test_a_steady_near_copy_is_an_echo_even_without_flicker():
    """2026-11-07: after ~20 min the echo pad stopped flickering and just copied the sleeper (breathing within
    1/min, heart rate within a beat or two, moving together). 10 min of that marks it as an echo; the pad with the
    weaker signal (more "breathing pause") is the one dropped."""
    rng = random.Random(5)
    pf = PresenceFilter()
    hr, br = 66.0, 15.0
    for i in range(1800):
        hr, br = walk(rng, hr, 58, 76, 0.6), walk(rng, br, 12, 20, 0.3)
        right = (2, None, None) if i % 40 == 0 else (0, round(hr) + 1, round(br))   # weak signal: breathing pauses
        if i < 15:
            right = EMPTY                                  # the copy starts seconds after the sleeper lies down
        pf.update(float(i), fr((0, round(hr), round(br)), right, i))
    assert pf.present("left") is True and pf.present("right") is False


def test_the_pad_that_was_on_first_is_never_the_echo():
    """2026-11-09: she got in first (her pad also lit the empty side); he got in 14 min later and their readings ran
    together. Her pad had more weak frames, but it was on first, so it can't be the copy: both stay in bed."""
    rng = random.Random(5)
    pf = PresenceFilter()
    hr, br = 66.0, 15.0
    for i in range(2400):
        hr, br = walk(rng, hr, 58, 76, 0.6), walk(rng, br, 12, 20, 0.3)
        left = (2, None, None) if i % 40 == 0 else (0, round(hr), round(br))        # HER pad: the weaker signal
        right = EMPTY if i < 15 else (0, round(hr) + 1, round(br))
        pf.update(float(i), fr(left, right, i))
    assert pf.present("left") is True


def test_after_a_restart_with_both_in_bed_nothing_is_called_an_echo():
    rng = random.Random(7)
    pf = PresenceFilter()
    hr, br = 64.0, 15.0
    for i in range(1800):
        hr, br = walk(rng, hr, 58, 76, 0.6), walk(rng, br, 12, 20, 0.3)
        right = (2, None, None) if i % 40 == 0 else (0, round(hr) + 1, round(br))
        pf.update(float(i), fr((0, round(hr), round(br)), right, i))           # both already on when it started
    assert pf.present("left") is True and pf.present("right") is True


def test_flat_but_different_readings_are_two_people():
    pf = PresenceFilter()
    for i in range(900):
        pf.update(float(i), fr((0, 62, 14), (0, 65, 16), i), )
    assert pf.present("left") is True and pf.present("right") is True


def test_partner_back_from_the_bathroom_with_similar_readings_still_counts():
    """One long gap (getting up) then back in bed, with their own readings in the same range: still a person."""
    rng = random.Random(9)
    pf = PresenceFilter()
    hr, br, p2, b2 = 64.0, 15.0, 63.0, 15.0
    for i in range(2400):
        hr, br = walk(rng, hr, 58, 74, 0.6), walk(rng, br, 12, 19, 0.3)
        p2, b2 = walk(rng, p2, 58, 74, 0.6), walk(rng, b2, 12, 19, 0.3)
        right = EMPTY if 600 <= i < 900 else (0, round(p2), round(b2))
        pf.update(float(i), fr((0, round(hr), round(br)), right, i))
        if i > 960:
            assert pf.present("right") is True, f"partner dropped at {i} s"


def test_a_pad_that_lights_up_just_after_the_other_and_copies_it_waits():
    """2026-11-09 00:55: the empty side came on 15 s after hers and read like her: not a person yet."""
    rng = random.Random(3)
    pf = PresenceFilter()
    hr, br = 74.0, 15.0
    seen_present = None
    for i in range(900):
        hr, br = walk(rng, hr, 64, 80, 0.5), walk(rng, br, 13, 18, 0.2)
        right = EMPTY if i < 15 else (0, round(hr) - 1, round(br))
        pf.update(float(i), fr((0, round(hr), round(br)), right, i))
        if pf.present("right") and seen_present is None:
            seen_present = i
    assert pf.present("left") is True
    assert seen_present is None or seen_present >= 600            # held for the first 10 minutes at least


def test_two_people_getting_in_together_both_count_quickly():
    pf = PresenceFilter()
    for i in range(300):
        right = EMPTY if i < 15 else (0, 58, 12)
        pf.update(float(i), fr((0, 72, 17), right, i))
    assert pf.present("left") is True and pf.present("right") is True
