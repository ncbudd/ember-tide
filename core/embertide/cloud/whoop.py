"""WHOOP (Developer API v2, OAuth2; open to any WHOOP member who creates an app in the developer dashboard).

Authorize https://api.prod.whoop.com/oauth/oauth2/auth, token https://api.prod.whoop.com/oauth/oauth2/token (form,
client id/secret in the body). `offline` is needed for a refresh token. Scopes: read:sleep read:recovery.

WHOOP doesn't publish continuous heart rate or a stage timeline, only per-sleep and per-recovery scores, so this
brings nightly numbers: resting heart rate, HRV (RMSSD), skin temperature, blood oxygen, breathing rate, recovery.
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
    gives = "nightly resting heart rate, HRV, skin temperature, blood oxygen, breathing rate and recovery (no minute-by-minute data)"
    docs_url = "https://developer.whoop.com"

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
            t1 = ts(s.get("end"))
            if t1 is None:
                continue
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
