"""How often and when notifications go out: quiet hours, daily caps, de-duplication across channels, one morning
summary instead of several pings, and the week budgets from the notification lab (tests/notify_week.py)."""

import httpx

from embertide.notify import DAILY_MAX, Action, Message, Notifier
from notify_week import LabNotifier, run_week, ts
from test_notify import Fake, _phone_keys, providers
from test_scenarios import Night


async def _drain(nt):
    for t in list(nt._tasks):
        await t


def _sent(n, kind=None):
    return [x for x in n.store.events(limit=5000) if x["kind"] == "notification" and (kind is None or x.get("about") == kind)]


async def test_bed_offline_at_night_waits_for_the_morning_summary(tmp_path):
    n = Night(tmp_path, "2026-11-04T21:00", scripted=True)
    n.c.notifier = LabNotifier(n.c, {"offline": [(ts("2026-11-05T02:00"), ts("2026-11-05T02:20"))]})
    await n.until("2026-11-05T02:30", dt=60)
    held = [x for x in n.store.events(limit=5000) if x["kind"] == "notification_held"]
    assert held and not _sent(n, "problem")                          # nobody woken at 2 AM
    await n.until("2026-11-05T09:00", dt=60)
    morning = _sent(n, "morning")
    assert len(morning) == 2                                         # one each, and nothing else
    assert all("Can't reach the bed" in m["body"] or "isn't answering" in m["body"] or "Lost contact" in m["body"] for m in morning)
    assert not _sent(n, "problem") and not _sent(n, "digest")
    assert not n.store.get("notify_held")                            # told once, then forgotten


async def test_quiet_hours_off_sends_it_at_once(tmp_path):
    n = Night(tmp_path, "2026-11-04T21:00", scripted=True)
    for p in n.hh.people:
        p.settings.notify.quiet_hours = False
    n.c.notifier = LabNotifier(n.c, {"offline": [(ts("2026-11-05T02:00"), ts("2026-11-05T02:20"))]})
    await n.until("2026-11-05T02:30", dt=60)
    assert len(_sent(n, "problem")) == 1


async def test_daily_cap_and_duplicates(tmp_path):
    n = Night(tmp_path, "2026-11-06T13:00")
    nt = Notifier(n.c, None)
    n.c.notifier = nt
    assert nt.send(Message("left_on", "On", "empty", n.p1.id))
    assert not nt.send(Message("left_on", "On", "empty", n.p1.id))           # the same again: dropped
    kinds = ["pet_question", "away_precool", "empty_side", "left_on", "morning"]
    sent = sum(nt.send(Message(k, f"t-{k}", "b", n.p1.id)) for k in kinds)
    assert sent == DAILY_MAX - 1                                              # 4 a day, all kinds together
    assert nt.send(Message("action_done", "Done", "b", n.p1.id))              # what you asked for is never capped
    skipped = [x for x in n.store.events(limit=500) if x["kind"] == "notification_skipped"]
    assert {x["why"] for x in skipped} == {"duplicate", "cap"}


async def test_household_problem_goes_to_phones_first_not_twice(tmp_path):
    n = Night(tmp_path, "2026-11-05T13:00")
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        return httpx.Response(200, json={})
    nt = Notifier(n.c, None, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    n.c.notifier = nt
    nt.save_providers(providers())
    n.c.store.put("notify_settings", {"push_server": "https://push.example"})
    _, pub, auth = _phone_keys()
    nt.add_device(n.p1.id, "https://push.example/upAbC123xyz", pub, auth, "Pixel")
    nt.add_device(n.p2.id, "https://push.example/upDeF456uvw", pub, auth, "Other")
    n.p2.settings.notify.problems = False                                     # p2 doesn't want bed trouble
    nt.send(Message("problem", "Can't reach the bed", "…", None, priority=4))
    await _drain(nt)
    assert seen == ["https://push.example/upAbC123xyz"]                       # p1's phone only; no household ntfy too


async def test_button_confirmation_skips_the_app(tmp_path):
    n = Night(tmp_path, "2026-11-05T21:00")
    fake = Fake()
    nt = Notifier(n.c, "https://bed.example", client=fake.client())
    n.c.notifier = nt
    nt.save_providers(providers())
    n.c.store.put("notify_settings", {"push_server": "https://ntfy.example"})
    _, pub, auth = _phone_keys()
    nt.add_device(n.p1.id, "https://ntfy.example/upAbC123xyz", pub, auth, "Pixel")
    code = nt.new_action(n.p1.id, Action("Off tonight", "skip"))
    assert nt.use_action(code)
    await _drain(nt)
    assert [c["json"].get("topic") for c in fake.calls] == ["alex-bed"]     # ntfy only: the app shows its own "done"


async def test_pet_question_from_the_night_rides_in_the_morning_summary(tmp_path):
    from embertide.models import Pet
    n = Night(tmp_path, "2026-11-04T21:00", scripted=True)
    n.hh.pets = [Pet(name="Biscuit", kind="cat", weight_kg=4.5)]
    n.c.notifier = Notifier(n.c, None)
    await n.until("2026-11-05T03:00", dt=60)
    now = n.clock.now_ts()
    n.store.put("pet_questions", [{"id": "q1", "person": n.p2.id, "zone": "right", "group": "g", "kind": "pet", "created": now,
                                   "pets": [n.hh.pets[0].id], "status": "open", "answer": None, "answered_at": None, "pushed": False,
                                   "spans": [{"start": now - 1200, "end": now, "evidence": "heart"}]}])
    await n.until("2026-11-05T10:00", dt=60)
    assert not _sent(n, "pet_question")
    mine = [m for m in _sent(n, "morning") if m["person"] == n.p2.id]
    assert len(mine) == 1 and "Biscuit" in mine[0]["body"]


async def test_week_budgets(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    normal = await run_week(tmp_path / "a", messy=False)
    for name, v in normal["by_person"].items():
        assert v["max_day"] <= 1 and normal["night"][name] == 0, (name, v)
    messy = await run_week(tmp_path / "b", messy=True)
    for name, v in messy["by_person"].items():
        assert messy["night"][name] == 0, (name, messy["titles"][name])     # late, offline, empty side: nothing at night
        assert v["max_day"] <= 3 and v["total"] <= 10, (name, v)
        assert "problem" not in messy["kinds"][name]                         # told in the morning summary instead
