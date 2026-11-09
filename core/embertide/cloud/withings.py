"""Withings (Public API, free for individual developers): Sleep Analyzer mat, ScanWatch, scales.

Authorize https://account.withings.com/oauth2_user/authorize2 (scopes comma-separated: user.metrics,
user.activity, user.sleepevents). Token: POST https://wbsapi.withings.net/v2/oauth2 with action=requesttoken; the
answer is wrapped ({"status": 0, "body": {...}}) and errors come back as HTTP 200 with a non-zero status, so both are
unwrapped here. Refresh tokens are single-use (the newest is kept).

Data: /v2/sleep action=get → sleep states (0 awake, 1 light, 2 deep, 3 REM) with heart rate and breathing rate
per minute; /measure action=getmeas → weight (type 1) for the bed scale.
"""

from __future__ import annotations

from datetime import date, datetime

import httpx

from .base import CloudConnector, CloudData, CloudError, merge_segments

API = "https://wbsapi.withings.net"
STATE = {0: "awake", 1: "light", 2: "deep", 3: "rem"}


class Withings(CloudConnector):
    id = "withings"
    name = "Withings"
    device = "Withings"
    auth_url = "https://account.withings.com/oauth2_user/authorize2"
    token_url = f"{API}/v2/oauth2"
    scopes = "user.metrics user.activity user.sleepevents"
    scope_sep = ","
    gives = "sleep stages, heart rate and breathing rate through the night (Sleep Analyzer, ScanWatch), weight"
    docs_url = "https://developer.withings.com"

    async def _token_request(self, form: dict) -> dict:
        return await super()._token_request({**form, "action": "requesttoken"})

    def parse_token(self, r: httpx.Response) -> dict:
        j = r.json()
        if j.get("status") != 0:
            raise CloudError("Withings didn't accept the sign-in.", 401)
        return j.get("body") or {}

    async def _call(self, pid: str, path: str, form: dict) -> dict:
        r = await self.request(pid, "POST", f"{API}{path}", data=form)
        j = r.json()
        st = j.get("status")
        if st in (401, 100, 101, 102, 200):          # Withings' "token invalid" family, sent with HTTP 200
            r = await self.request(pid, "POST", f"{API}{path}", data=form, headers={"Authorization": f"Bearer {await self.fresh_token(pid, force=True)}"})
            j = r.json()
            if j.get("status") != 0:
                raise CloudError("Withings' sign-in ran out. Connect again.", 401)
        elif st == 601:
            raise CloudError("Withings is busy right now.", 429, 900)
        elif st != 0:
            raise CloudError(f"Withings answered status {st}.", 502)
        return j.get("body") or {}

    async def fetch(self, pid: str, start: date, end: date, now: float) -> CloudData:
        tz = self.c.clock.tz
        a = int(datetime(start.year, start.month, start.day, 12, tzinfo=tz).timestamp()) - 86400
        b = min(int(now), int(datetime(end.year, end.month, end.day, tzinfo=tz).timestamp()))
        out = CloudData()
        segs: list[dict] = []
        # the sleep endpoint takes at most 7 days per call
        t = a
        while t < b:
            body = await self._call(pid, "/v2/sleep", {"action": "get", "startdate": t, "enddate": min(b, t + 7 * 86400 - 1), "data_fields": "hr,rr"})
            for s in body.get("series") or []:
                st, x0, x1 = s.get("state"), s.get("startdate"), s.get("enddate")
                if st in STATE and isinstance(x0, (int, float)) and isinstance(x1, (int, float)) and x1 > x0:
                    segs.append({"start": float(x0), "end": float(x1), "stage": STATE[st]})
                for k, metric in (("hr", "hr"), ("rr", "resp_rate")):
                    for tt, v in (s.get(k) or {}).items():
                        try:
                            out.add(metric, float(tt), v)
                        except ValueError:
                            pass
            t += 7 * 86400
        # group segments into nights (a gap of 3 h or more starts a new one)
        night: list[dict] = []
        for g in sorted(segs, key=lambda x: x["start"]):
            if night and g["start"] - night[-1]["end"] > 3 * 3600:
                out.stages.append((night[0]["start"], night[-1]["end"], merge_segments(night)))
                night = []
            night.append(g)
        if night:
            out.stages.append((night[0]["start"], night[-1]["end"], merge_segments(night)))
        meas = await self._call(pid, "/measure", {"action": "getmeas", "meastype": 1, "category": 1, "startdate": a, "enddate": b})
        for g in meas.get("measuregrps") or []:
            for m in g.get("measures") or []:
                if m.get("type") == 1 and isinstance(m.get("value"), (int, float)) and isinstance(m.get("unit"), int):
                    out.add("weight_kg", g.get("date"), m["value"] * 10 ** m["unit"])
        return out
