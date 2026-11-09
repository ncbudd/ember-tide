"""voice.py: every message type has enough variants per tone, variants keep their slots and numbers, the pick is
deterministic and never repeats back to back, and alerts are never cheeky."""

import re
from datetime import date, timedelta

import pytest

from embertide import voice
from embertide.store import Store
from embertide.voice import ALERTS, PHRASES, TONES, pick, say, slots_of

SAMPLE = {"name": "Alex", "minutes": 23, "summary": "7 h 50 min in bed · settled in 12 min", "who": "Biscuit",
          "when": "2:40 AM–3:10 AM", "side": "left side", "phase": "Deep", "deg": "2°F", "source": "the phone",
          "n": 3, "look": 7, "step": "1 °F", "detail": "no answer", "lines": "2:06 AM: Can't reach the bed."}


class P:
    def __init__(self, pid="p1", tone="calm"):
        self.id = pid
        self.settings = type("S", (), {"tone": tone})()


def _text(v) -> str:
    return " ".join(v) if isinstance(v, tuple) else v


@pytest.mark.parametrize("kind", sorted(PHRASES))
def test_every_type_has_three_variants_per_tone_with_the_same_slots(kind):
    want = slots_of(PHRASES[kind]["calm"][0])
    for tone in TONES:
        vs = PHRASES[kind][tone]
        assert len(vs) >= 3, (kind, tone)
        assert len(set(vs)) == len(vs), (kind, tone, "duplicate variant")
        for v in vs:
            assert slots_of(v) == want, (kind, tone, v)
            assert type(v) is type(PHRASES[kind]["calm"][0])        # titles stay titles, lines stay lines


@pytest.mark.parametrize("kind", sorted(PHRASES))
def test_filled_variants_keep_every_value_and_number(kind):
    slots = {k: SAMPLE[k] for k in slots_of(PHRASES[kind]["calm"][0])}
    for tone in TONES:
        for i in range(len(PHRASES[kind][tone])):
            out = _text(say(kind, slots, tone=tone, day=date(2026, 11, 1) + timedelta(days=i)))
            for k, v in slots.items():
                assert str(v).lower() in out.lower(), (kind, tone, k, out)
            assert "{" not in out and "}" not in out
            nums = re.findall(r"\d+", " ".join(str(slots[k]) for k in slots))
            for n in nums:
                assert n in out


def test_suggestions_keep_the_exact_change():
    for d in range(10):
        day = date(2026, 11, 1) + timedelta(days=d)
        for tone in TONES:
            w = voice.suggestion_words("manual", "cooler", {"phase": "Deep", "deg": "2°F", "source": "a dial"}, P(tone=tone), day)
            assert "2°F cooler" in w["title"] and "a dial" in w["detail"]
            w = voice.suggestion_words("pattern", "warmer", {"phase": "Late", "n": 4, "look": 7, "step": "1 °F"}, P(tone=tone), day)
            text = w["title"] + " " + w["detail"]
            assert "4" in text and "7" in text and "1 °F warmer" in text and "cooler" not in text


def test_pick_is_deterministic_and_never_repeats_day_to_day():
    for kind in PHRASES:
        n = len(PHRASES[kind]["calm"])
        for who in ("p1", "p2", "household"):
            prev = None
            for d in range(60):
                day = date(2026, 1, 1) + timedelta(days=d)
                i = pick(kind, n, who, day)
                assert i == pick(kind, n, who, day)                 # same day, same words: no flicker on reload
                assert i != prev                                    # never the same as yesterday
                prev = i


def test_store_remembers_the_last_pick_and_skips_it_on_a_later_day(tmp_path):
    store = Store(tmp_path / "v.db")
    p = P()
    first = say("left_on", SAMPLE, person=p, day=date(2026, 11, 2), store=store)
    assert say("left_on", SAMPLE, person=p, day=date(2026, 11, 2), store=store) == first      # reload: unchanged
    seen = [first]
    for d in (5, 9, 12, 20, 21, 30):                                 # sent now and then, not daily
        seen.append(say("left_on", SAMPLE, person=p, day=date(2026, 11, d), store=store))
    assert all(a != b for a, b in zip(seen, seen[1:]))


def test_alerts_are_never_cheeky():
    for kind in ALERTS:
        calm = PHRASES[kind]["calm"]
        assert PHRASES[kind]["cheeky"] == calm and PHRASES[kind]["friendly"] == calm
        slots = {k: SAMPLE[k] for k in slots_of(calm[0])}
        for d in range(6):
            day = date(2026, 11, 1) + timedelta(days=d)
            out = say(kind, slots, person=P(tone="cheeky"), day=day)
            assert out in [voice._fill(v, slots) for v in calm]


def test_calm_starts_with_the_original_sentence():
    assert PHRASES["away_precool"]["calm"][0][0] == "Pre-cool is due, but you're not home"
    assert PHRASES["pet_question"]["calm"][0][0] == "Was {who} on the bed around {when}?"
    assert all("not home" in t for t, _ in PHRASES["away_precool"]["calm"])


def test_cheeky_stays_kind():
    """A word list as a tripwire: nothing about bodies, weight or anything rude in any variant."""
    banned = re.compile(r"\b(fat|weight|belly|diet|chubby|ugly|stupid|idiot|lazy|sexy|naked|kiss|shut up|loser)\b", re.I)
    for kind, tones in PHRASES.items():
        for tone, vs in tones.items():
            for v in vs:
                assert not banned.search(_text(v)), (kind, tone, v)


def test_samples_preview_every_tone():
    out = {t: voice.samples(t, "Sam", date(2026, 11, 8)) for t in TONES}
    assert all(len(v) == 4 and all(v) for v in out.values())
    assert out["calm"] != out["cheeky"]
    assert any("Sam" in line for line in out["friendly"])


def test_tone_setting_and_preview_route(tmp_path):
    from test_notify import H, make
    with make(tmp_path) as c:
        assert c.patch("/api/people/p1/settings", json={"tone": "cheeky"}, headers=H).status_code == 200
        assert c.get("/api/snapshot").json()["people"][0]["settings"]["tone"] == "cheeky"
        assert c.patch("/api/people/p1/settings", json={"tone": "rude"}, headers=H).status_code == 422
        r = c.get("/api/voice/samples", params={"name": "Sam"}).json()
        assert set(r) == set(TONES) and all(len(v) == 4 for v in r.values())
