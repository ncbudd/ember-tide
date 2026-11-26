"""WHOOP sleep-stage summary ingestion and historical backfill."""

from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx

from test_cloud import client, connect, iso
from test_sim_wear_cloud_more import whoop_fake


NIGHT = {
    "id": "whoop-night-synthetic-1",
    "nap": False,
    "start": iso("2026-11-04T22:30"),
    "end": iso("2026-11-05T06:30"),
    "score": {
        "respiratory_rate": 14.5,
        "stage_summary": {
            "total_in_bed_time_milli": 28_800_000,
            "total_awake_time_milli": 1_800_000,
            "total_light_sleep_time_milli": 14_400_000,
            "total_slow_wave_sleep_time_milli": 5_400_000,
            "total_rem_sleep_time_milli": 5_400_000,
            "total_no_data_time_milli": 1_800_000,
        },
    },
}


def test_whoop_stage_summary_backfills_and_is_idempotent(tmp_path):
    fake = whoop_fake(lambda req: httpx.Response(200, json={"records": [NIGHT]}))
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers={"X-Embertide": "1"})
        provider, _ = connect(c, "whoop", fake)
        # Model an existing account whose older sync predates stage-summary ingestion.
        provider.store.put("sleeps:p2", [])
        provider._status("p2", last_sync=1.0, data_version=1)
        fake.calls.clear()

        r = c.post("/api/cloud/me/whoop/sync", json={"person": "p2"}, headers={"X-Embertide": "1"})
        assert r.status_code == 200, r.text
        sleep_call = next(req for req in fake.calls if req.url.path.endswith("/activity/sleep"))
        q = parse_qs(urlsplit(str(sleep_call.url)).query)
        assert q["start"][0][:10] == (date(2026, 11, 5) - timedelta(days=120)).isoformat()

        sleeps = c.get("/api/people/p2/sleeps?days=120", headers={"X-Embertide": "1"}).json()["sleeps"]
        assert len(sleeps) == 1
        night = sleeps[0]
        assert night["id"] == "whoop:whoop-night-synthetic-1"
        assert night["deep_min"] == 90
        assert night["rem_min"] == 90
        assert night["light_min"] == 240
        assert night["awake_min"] == 30
        assert night["asleep_min"] == 420

        stage_sources = c.get("/api/people/p2/stages?date=2026-11-04", headers={"X-Embertide": "1"}).json()
        whoop = next(x for x in stage_sources if x["id"] == "whoop:whoop-night-synthetic-1")
        assert whoop["kind"] == "summary"
        assert whoop["totals"] == {"awake": 30, "rem": 90, "light": 240, "deep": 90}
        assert whoop["segments"] == []  # WHOOP provides totals, not a fabricated stage timeline.
        assert provider.store.get_stages("p2", NIGHT["start"], NIGHT["end"]) == {}

        again = c.post("/api/cloud/me/whoop/sync", json={"person": "p2"}, headers={"X-Embertide": "1"})
        assert again.status_code == 200, again.text
        assert again.json()["stored"]["sleeps"] == 0
        assert len(c.get("/api/people/p2/sleeps?days=120", headers={"X-Embertide": "1"}).json()["sleeps"]) == 1
