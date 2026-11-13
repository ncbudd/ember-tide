"""The morning AI review as a notification: once a night, only with its switch (and the review on), trimmed to a sentence
or two, never for a review without a model; and the Tonight card's endpoints."""

import asyncio

from embertide.notify import Notifier, review_excerpt
from test_autopilot_suite import make
from test_notify import Fake, providers
from test_scenarios import Night

H = {"X-Embertide": "1"}
LONG = ("You settled in quickly and the bed followed your plan well. " * 1 + "Autopilot made no changes overnight. "
        "A cooler start might help on warm nights.")


async def morning(tmp_path):
    n = Night(tmp_path, "2026-11-05T22:00")
    n.c.notifier = Notifier(n.c, None, client=Fake().client())
    n.c.notifier.save_providers(providers())
    n.present("left", True)
    await n.until("2026-11-06T07:00", dt=120)
    n.present("left", False)
    await n.until("2026-11-06T08:30", dt=120)
    n.c.notifier._snap = n.c.snapshot()
    n.p1.settings.autopilot.ai_review = True
    return n


def reviews(n):
    return [e for e in n.events("notification") if e.get("about") == "review"]


def out(answer=LONG, model="test-model"):
    return {"answer": answer, "proposal": None, "model": model, "ts": 0, "date": "2026-11-05"}


async def test_sent_once_with_trimmed_body_and_link(tmp_path):
    n = await morning(tmp_path)
    m = n.c.notifier.review_message(n.p1, "2026-11-05", out())
    assert m and m.kind == "review" and m.title == "Your night, reviewed" and m.link == "/" and m.person == n.p1.id
    assert m.body == "You settled in quickly and the bed followed your plan well. Autopilot made no changes overnight."
    assert n.c.notifier.review_message(n.p1, "2026-11-05", out()) is None               # same night: not twice
    assert n.c.notifier.review_message(n.p1, "2026-11-06", out())                        # another night is fine


async def test_send_review_goes_out_once(tmp_path):
    n = await morning(tmp_path)
    assert n.c.notifier.send_review(n.p1, "2026-11-05", out()) is True
    assert not n.c.notifier.send_review(n.p1, "2026-11-05", out())
    await asyncio.sleep(0)
    assert len(reviews(n)) == 1


async def test_respects_switches_and_empty_or_modelless(tmp_path):
    n = await morning(tmp_path)
    nt = n.c.notifier
    n.p1.settings.notify.review = False
    assert nt.review_message(n.p1, "2026-11-05", out()) is None
    n.p1.settings.notify.review = True
    n.p1.settings.autopilot.ai_review = False
    assert nt.review_message(n.p1, "2026-11-05", out()) is None
    n.p1.settings.autopilot.ai_review = True
    assert nt.review_message(n.p1, "2026-11-05", out("  ")) is None
    assert nt.review_message(n.p1, "2026-11-05", out(model=None)) is None               # the built-in summary: no ping
    assert nt.review_message(n.p1, "2026-11-05", None) is None
    assert nt.review_message(n.p1, "2026-11-05", out())                                  # none of the above used up the night


async def test_not_for_someone_away(tmp_path):
    from embertide.engine import away
    n = await morning(tmp_path)
    away.start(n.c, n.p1.id, n.clock.now_ts())
    assert n.c.notifier.review_message(n.p1, "2026-11-05", out()) is None


async def test_held_in_quiet_hours_is_dropped_not_told_late(tmp_path):
    n = await morning(tmp_path)
    nt = n.c.notifier
    assert nt._wants_held({"kind": "review"}, n.p1.id) is False


def test_excerpt_trimming():
    assert review_excerpt("One. Two. Three.") == "One. Two."
    assert review_excerpt("Short only") == "Short only"
    long2 = "A" * 100 + ". " + "B" * 100 + "."
    assert review_excerpt(long2) == "A" * 100 + "."                                      # the second sentence doesn't fit
    one = ("word " * 80).strip() + "."
    t = review_excerpt(one)
    assert len(t) <= 180 and t.endswith("…")


def test_tonight_card_endpoints_and_dismiss(tmp_path):
    with make(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-06T09:00"}, headers=H)
        st = c.app.state.oura.c.store
        assert c.get("/api/people/p1/autopilot/ai-review/last").json() == {}
        st.put("ai_review:p1:2026-11-05", {"answer": "Fine night.", "proposal": None, "model": "m", "ts": 1, "date": "2026-11-05"})
        r = c.get("/api/people/p1/autopilot/ai-review/last").json()
        assert r["answer"] == "Fine night." and r["dismissed"] is False
        assert c.post("/api/people/p1/autopilot/ai-review/dismiss", json={"date": "2026-11-05"}, headers=H).json()["ok"]
        assert c.get("/api/people/p1/autopilot/ai-review/last").json()["dismissed"] is True
        c.post("/api/sim", json={"jump_to": "2026-11-06T15:00"}, headers=H)                # afternoon: still last night's
        assert c.get("/api/people/p1/autopilot/ai-review/last").json()["answer"] == "Fine night."
        c.post("/api/sim", json={"jump_to": "2026-11-07T09:00"}, headers=H)                # a day later: that review is old
        assert c.get("/api/people/p1/autopilot/ai-review/last").json() == {}
