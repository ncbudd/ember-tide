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
