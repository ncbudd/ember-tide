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
        assert q["start"][0][:10] == (date(2026, 11, 5) - timedelta(days=120)).isoformat()   # one-time backfill after the data_version bump

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


def test_whoop_naps_pending_score_and_efficiency(tmp_path):
    records = [
        {
            "id": "whoop-scored-night",
            "nap": False,
            "score_state": "SCORED",
            "start": iso("2026-11-04T22:30"),
            "end": iso("2026-11-05T06:30"),
            "score": {
                "sleep_efficiency_percentage": 91.5,
                "respiratory_rate": 14.2,
                "stage_summary": NIGHT["score"]["stage_summary"],
            },
        },
        {
            "id": "whoop-scored-nap",
            "nap": True,
            "score_state": "SCORED",
            "start": iso("2026-11-05T13:00"),
            "end": iso("2026-11-05T13:45"),
            "score": {
                "sleep_efficiency_percentage": 85.0,
                "respiratory_rate": 16.0,
                "stage_summary": {
                    "total_awake_time_milli": 600_000,
                    "total_light_sleep_time_milli": 1_200_000,
                    "total_slow_wave_sleep_time_milli": 600_000,
                    "total_rem_sleep_time_milli": 300_000,
                },
            },
        },
        {
            "id": "whoop-pending-night",
            "nap": False,
            "score_state": "PENDING_SCORE",
            "start": iso("2026-11-03T23:00"),
            "end": iso("2026-11-04T06:30"),
            "score": {
                "sleep_efficiency_percentage": 88.0,
                "respiratory_rate": 15.0,
                "stage_summary": NIGHT["score"]["stage_summary"],
            },
        },
    ]
    fake = whoop_fake(lambda req: httpx.Response(200, json={"records": records}))
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T15:00"}, headers={"X-Embertide": "1"})
        provider, _ = connect(c, "whoop", fake)
        r = c.post("/api/cloud/me/whoop/sync", json={"person": "p2"}, headers={"X-Embertide": "1"})
        assert r.status_code == 200, r.text

        by_id = {x["id"]: x for x in provider.store.get("sleeps:p2")}
        scored = by_id["whoop:whoop-scored-night"]
        assert scored["kind"] == "night"
        assert scored["efficiency"] == 91.5 and scored["latency_min"] is None
        assert scored["deep_min"] == 90 and scored["asleep_min"] == 420

        nap = by_id["whoop:whoop-scored-nap"]
        assert nap["kind"] == "nap"
        assert nap["efficiency"] == 85.0 and nap["latency_min"] is None
        assert nap["asleep_min"] == 35 and nap["deep_min"] == 10

        pending = by_id["whoop:whoop-pending-night"]
        assert pending["kind"] == "night"
        assert pending["efficiency"] is None and pending["latency_min"] is None
        for k in ("asleep_min", "deep_min", "rem_min", "light_min", "awake_min"):
            assert pending[k] is None

        # Only the scored night adds respiratory rate; naps and PENDING_SCORE records do not.
        resp = provider.store.samples("wear:p2:resp_rate:WHOOP", 0, 2e9)
        assert len(resp) == 1 and resp[0][1] == 14.2

        # PENDING_SCORE night has no stage totals, so it produces no summary stage source.
        pending_stages = c.get("/api/people/p2/stages?date=2026-11-03", headers={"X-Embertide": "1"}).json()
        assert all(x["kind"] != "summary" for x in pending_stages)


def test_whoop_backfill_is_one_time_and_new_connections_stay_short(tmp_path):
    fake = whoop_fake(lambda req: httpx.Response(200, json={"records": [NIGHT]}))
    with client(tmp_path) as c:
        c.post("/api/sim", json={"jump_to": "2026-11-05T12:00"}, headers={"X-Embertide": "1"})
        provider, _ = connect(c, "whoop", fake)

        def first_start() -> str:
            call = next(req for req in fake.calls if req.url.path.endswith("/activity/sleep"))
            return parse_qs(urlsplit(str(call.url)).query)["start"][0][:10]

        # a brand-new connection (never synced) looks back first_days, not the backfill
        provider.store.put(provider.k("status", "p2"), {})
        fake.calls.clear()
        assert c.post("/api/cloud/me/whoop/sync", json={"person": "p2"}, headers={"X-Embertide": "1"}).status_code == 200
        assert first_start() == (date(2026, 11, 5) - timedelta(days=14)).isoformat()

        # an account synced before the stage-totals version backfills once...
        provider._status("p2", last_sync=1.0, data_version=1)
        fake.calls.clear()
        assert c.post("/api/cloud/me/whoop/sync", json={"person": "p2"}, headers={"X-Embertide": "1"}).status_code == 200
        assert first_start() == (date(2026, 11, 5) - timedelta(days=120)).isoformat()

        # ...and the next sync is the usual short look-back
        fake.calls.clear()
        assert c.post("/api/cloud/me/whoop/sync", json={"person": "p2"}, headers={"X-Embertide": "1"}).status_code == 200
        assert first_start() == (date(2026, 11, 5) - timedelta(days=2)).isoformat()
