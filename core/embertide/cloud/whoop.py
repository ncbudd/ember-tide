"""WHOOP (Developer API v2, OAuth2; open to any WHOOP member who creates an app in the developer dashboard).

Authorize https://api.prod.whoop.com/oauth/oauth2/auth, token https://api.prod.whoop.com/oauth/oauth2/token (form,
client id/secret in the body). `offline` is needed for a refresh token. Scopes: read:sleep read:recovery.

WHOOP doesn't publish continuous heart rate or a stage timeline. Its sleep records do include nightly totals for
awake, light, REM and slow-wave (deep) sleep, alongside per-sleep and per-recovery scores.
"""

from __future__ import annotations

from datetime import date, datetime

from .base import CloudConnector, CloudData, ts

API = "https://api.prod.whoop.com/developer/v2"


class Whoop(CloudConnector):
    id = "whoop"
    name = "WHOOP"
    device = "WHOOP"
    auth_url = "https://api.prod.whoop.com/oauth/oauth2/auth"
    token_url = "https://api.prod.whoop.com/oauth/oauth2/token"
    scopes = "offline read:sleep read:recovery"
    gives = "nightly sleep-stage totals, resting heart rate, HRV, skin temperature, blood oxygen, breathing rate and recovery (no stage timeline or minute-by-minute heart rate)"
    docs_url = "https://developer.whoop.com"
    first_days = 120
    data_version = 2                    # stage summaries; re-fetch the retained sleep-history window once

    async def _all(self, pid: str, path: str, params: dict) -> list[dict]:
        out: list[dict] = []
        q = dict(params)
        for _ in range(20):
            r = await self.request(pid, "GET", f"{API}{path}", params=q)
            j = r.json() if r.status_code == 200 else {}
            out.extend(j.get("records") or [])
            if not j.get("next_token"):
                break
            q = {**params, "nextToken": j["next_token"]}
        return out

    async def fetch(self, pid: str, start: date, end: date, now: float) -> CloudData:
        tz = self.c.clock.tz
        q = {"start": datetime(start.year, start.month, start.day, tzinfo=tz).isoformat(),
             "end": datetime(end.year, end.month, end.day, tzinfo=tz).isoformat(), "limit": 25}
        sleeps = await self._all(pid, "/activity/sleep", q)
        recs = await self._all(pid, "/recovery", q)
        out = CloudData()
        ends: dict[str, float] = {}
        for s in sleeps:
            if s.get("nap"):
                continue
            t0 = ts(s.get("start"))
            t1 = ts(s.get("end"))
            if t0 is None or t1 is None or t1 <= t0:
                continue
            out.periods.append(period(s, t0, t1, "night"))
            ends[str(s.get("id"))] = t1
            out.add("resp_rate", t1, (s.get("score") or {}).get("respiratory_rate"))
        for r in recs:
            sc = r.get("score") or {}
            t = ends.get(str(r.get("sleep_id"))) or ts(r.get("created_at"))
            out.add("resting_hr", t, sc.get("resting_heart_rate"))
            out.add("hrv", t, sc.get("hrv_rmssd_milli"))
            out.add("skin_temp", t, sc.get("skin_temp_celsius"))
            out.add("spo2", t, sc.get("spo2_percentage"))
            out.add("readiness", t, sc.get("recovery_score"))
        return out


def _minutes(value) -> int | None:
    """WHOOP stage totals are milliseconds; reject malformed, negative and non-finite values."""
    import math

    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return round(value / 60_000)
    return None


def period(s: dict, t0: float, t1: float, kind: str) -> dict:
    """A WHOOP sleep summary. The API has no stage timestamps, so do not invent segments."""
    score = s.get("score") or {}
    summary = score.get("stage_summary") or {}
    deep = _minutes(summary.get("total_slow_wave_sleep_time_milli"))
    light = _minutes(summary.get("total_light_sleep_time_milli"))
    rem = _minutes(summary.get("total_rem_sleep_time_milli"))
    awake = _minutes(summary.get("total_awake_time_milli"))
    asleep_parts = (deep, light, rem)
    asleep = sum(x for x in asleep_parts if x is not None) if any(x is not None for x in asleep_parts) else None
    return {"id": f"whoop:{s.get('id') or int(t0)}", "source": "whoop", "device": "WHOOP", "kind": kind,
            "start": t0, "end": t1, "asleep_min": asleep, "deep_min": deep, "rem_min": rem,
            "light_min": light, "awake_min": awake, "segments": []}
