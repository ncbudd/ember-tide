"""A tap on a notification opens the screen that fixes it: every app push carries a `link`, owners get Studio pages,
everyone else the phone equivalent."""

import pytest

from embertide.notify import DEFAULT_LINKS, Message, link_for
from notify_rig import Rig

EXPECT = {
    "morning": "/sleep", "digest": "/sleep", "pet_question": "/", "away_check": "/", "maintenance": "/settings#care",
    "hub": "/settings#hub", "problem": "/", "left_on": "/", "empty_side": "/", "not_started": "/", "wearable": "/settings#watch",
}


@pytest.mark.parametrize("kind", sorted(EXPECT))
@pytest.mark.parametrize("via", ["fcm", "up"])
async def test_app_payload_has_the_kinds_usual_link(tmp_path, kind, via):
    r = Rig(tmp_path)
    r.phone("p1", via)
    r.nt.send(Message(kind, "A title", "A body.", "p1", priority=3))
    await r.drain()
    got = r.sent()
    assert len(got) == 1 and got[0].raw["link"] == EXPECT[kind]


async def test_an_explicit_link_is_kept_for_an_owner_and_mapped_for_others(tmp_path):
    r = Rig(tmp_path)
    r.phone("p1", "fcm")
    r.phone("p2", "fcm")
    r.nt.owner_persons = lambda: {"p1"}
    for who in ("p1", "p2"):
        r.nt.send(Message("problem", "T", "B", who, link="/studio/devices"))
        r.nt.send(Message("digest", "T", "B", who, link="/studio/data"))
        r.nt.send(Message("hub", "T", "B", who, link="/studio/hub"))
    await r.drain()
    links = {(g.person, g.kind): g.raw["link"] for g in r.sent()}
    assert links[("p1", "problem")] == "/studio/devices" and links[("p2", "problem")] == "/"
    assert links[("p1", "digest")] == "/studio/data" and links[("p2", "digest")] == "/settings"
    assert links[("p1", "hub")] == "/studio/hub" and links[("p2", "hub")] == "/settings#hub"


def test_link_for_defaults_and_unknown_studio_pages():
    assert link_for(None, "something_new") == "/"
    assert link_for("/studio/whatever", "x", owner=False) == "/settings"
    assert link_for("/studio/hub#a", "x", owner=False) == "/settings#hub"
    assert link_for("/settings#watch", "x", owner=False) == "/settings#watch"
    assert all(v.startswith("/") and not v.startswith("/studio") for v in DEFAULT_LINKS.values())
