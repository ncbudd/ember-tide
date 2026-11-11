"""The night trace line: everything that decided who's in bed, readable without the live server."""

import json

from test_sides import make, ctl
from embertide.trace import frame


def test_trace_frame_has_pads_votes_people_and_is_json(tmp_path):
    with make(tmp_path) as c:
        k = ctl(c)
        k.fuse("left"); k.fuse("right")
        f = frame(k)
        json.dumps(f, default=str)
        assert "ts" in f and set(f["fused"]) >= {"left", "right"}
        assert {p["zone"] for p in f["people"]} == {"left", "right"}
        assert "filter" in f and "pads" in f


def test_trace_frame_carries_the_settle_gate_and_its_reason(tmp_path):
    with make(tmp_path) as c:
        k = ctl(c)
        pid = k.household.people[0].id
        k.__dict__["_settle_info"] = {pid: {"gate": False, "why": "settling in", "share": 0.25, "offer_start": True}}
        f = frame(k)
        json.dumps(f, default=str)
        row = next(p for p in f["people"] if p["id"] == pid)
        assert row["night"]["settle"] == {"gate": False, "why": "settling in", "share": 0.25}
        other = next(p for p in f["people"] if p["id"] != pid)
        assert "settle" not in other["night"]
