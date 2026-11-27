"""WHOOP (Developer API v2, OAuth2; open to any WHOOP member who creates an app in the developer dashboard).

Authorize https://api.prod.whoop.com/oauth/oauth2/auth, token https://api.prod.whoop.com/oauth/oauth2/token (form,
client id/secret in the body). `offline` is needed for a refresh token. Scopes: read:sleep read:recovery.

WHOOP doesn't publish continuous heart rate or a stage timeline. Its sleep records do include nightly totals for
awake, light, REM and slow-wave (deep) sleep, alongside per-sleep and per-recovery scores.
"""

from __future__ import annotations

import math
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
    data_version = 2                    # 2: stage totals, naps, efficiency
    backfill_days = 120                 # after that bump, fill stage totals for the whole kept sleep history (sleeps.KEEP_DAYS) once

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
            t0 = ts(s.get("start"))
            t1 = ts(s.get("end"))
            if t0 is None or t1 is None or t1 <= t0:
                continue
            kind = "nap" if s.get("nap") else "night"
            out.periods.append(period(s, t0, t1, kind))
            if kind == "night":
                ends[str(s.get("id"))] = t1
                out.add("resp_rate", t1, _scored(s).get("respiratory_rate"))
        for r in recs:
            sc = r.get("score") or {}
            t = ends.get(str(r.get("sleep_id"))) or ts(r.get("created_at"))
            out.add("resting_hr", t, sc.get("resting_heart_rate"))
            out.add("hrv", t, sc.get("hrv_rmssd_milli"))
            out.add("skin_temp", t, sc.get("skin_temp_celsius"))
            out.add("spo2", t, sc.get("spo2_percentage"))
            out.add("readiness", t, sc.get("recovery_score"))
        return out


def _scored(s: dict) -> dict:
    """Sleep scores are only valid when score_state is 'SCORED' (missing score_state with a score counts as scored)."""
    score = s.get("score")
    if not isinstance(score, dict):
        return {}
    state = s.get("score_state")
    return score if state is None or state == "SCORED" else {}


def _num(value, lo: float = 0.0, hi: float | None = None) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        if value >= lo and (hi is None or value <= hi):
            return float(value)
    return None


def _minutes(value) -> int | None:
    """WHOOP stage totals are milliseconds; reject malformed, negative and non-finite values."""
    v = _num(value, 0.0)
    return round(v / 60_000) if v is not None else None


def period(s: dict, t0: float, t1: float, kind: str) -> dict:
    """A WHOOP sleep summary. The API has no stage timestamps, so do not invent segments."""
    score = _scored(s)
    summary = score.get("stage_summary")
    summary = summary if isinstance(summary, dict) else {}
    deep = _minutes(summary.get("total_slow_wave_sleep_time_milli"))
    light = _minutes(summary.get("total_light_sleep_time_milli"))
    rem = _minutes(summary.get("total_rem_sleep_time_milli"))
    awake = _minutes(summary.get("total_awake_time_milli"))
    asleep_parts = (deep, light, rem)
    asleep = sum(x for x in asleep_parts if x is not None) if any(x is not None for x in asleep_parts) else None
    efficiency = _num(score.get("sleep_efficiency_percentage"), 0.0, 100.0)
    return {"id": f"whoop:{s.get('id') or int(t0)}", "source": "whoop", "device": "WHOOP", "kind": kind,
            "start": t0, "end": t1, "asleep_min": asleep, "deep_min": deep, "rem_min": rem,
            "light_min": light, "awake_min": awake, "efficiency": efficiency, "latency_min": None, "segments": []}
