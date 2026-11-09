"""Polar (AccessLink API v3; any Polar Flow user can register a client at admin.polaraccesslink.com).

Authorize https://flow.polar.com/oauth2/authorization, token https://polarremote.com/v2/oauth2/token with HTTP Basic
client auth. Tokens are long-lived and there is no refresh. After connecting, the user must be registered once
(POST /v3/users with a member id; 409 = already registered).

Data (non-transactional endpoints, last 28 days): /v3/users/sleep → nights with a hypnogram ("HH:MM": stage, 0 wake,
1 REM, 2 lighter, 3 light, 4 deep) and heart-rate samples ("HH:MM": bpm); /v3/users/nightly-recharge → nightly
average heart rate, HRV and breathing rate.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .base import CloudConnector, CloudData, merge_segments, ts

API = "https://www.polaraccesslink.com/v3"
HYP = {0: "awake", 1: "rem", 2: "light", 3: "light", 4: "deep"}


class Polar(CloudConnector):
    id = "polar"
    name = "Polar"
    device = "Polar"
    auth_url = "https://flow.polar.com/oauth2/authorization"
    token_url = "https://polarremote.com/v2/oauth2/token"
    scopes = "accesslink.read_all"
    token_auth = "basic"
    refreshes = False
    gives = "sleep stages and heart rate through the night, nightly HRV and breathing rate (Nightly Recharge)"
    docs_url = "https://www.polar.com/accesslink-api"

    async def after_connect(self, pid: str, tok: dict) -> None:
        await self.request(pid, "POST", f"{API}/users", allow=(409,), json={"member-id": f"et-{pid}"})   # 409: already registered

    async def fetch(self, pid: str, start: date, end: date, now: float) -> CloudData:
        tz = self.c.clock.tz
        out = CloudData()
        r = await self.request(pid, "GET", f"{API}/users/sleep")
        nights = (r.json() or {}).get("nights", []) if r.status_code == 200 else []
        for n in nights:
            t0, t1 = ts(n.get("sleep_start_time")), ts(n.get("sleep_end_time"))
            if t0 is None or t1 is None or t1 <= t0:
                continue
            day = datetime.fromtimestamp(t0, tz).date()
            if day < start - timedelta(days=1) or day > end:
                continue

            def at(hm: str) -> float | None:
                """'HH:MM' inside this night → seconds: before the start's clock time means the next morning."""
                try:
                    h, m = (int(x) for x in hm.split(":")[:2])
                except ValueError:
                    return None
                start_dt = datetime.fromtimestamp(t0, tz)
                cand = start_dt.replace(hour=h, minute=m, second=0, microsecond=0)
                if cand.timestamp() < t0 - 1800:
                    cand += timedelta(days=1)
                return cand.timestamp()
            pts = sorted((t, v) for k, v in (n.get("hypnogram") or {}).items() if (t := at(k)) is not None and v in HYP)
            segs = [{"start": t, "end": (pts[i + 1][0] if i + 1 < len(pts) else t1), "stage": HYP[v]} for i, (t, v) in enumerate(pts)]
            segs = [g for g in segs if g["end"] > g["start"]]
            if segs:
                out.stages.append((t0, t1, merge_segments(segs)))
            for k, v in (n.get("heart_rate_samples") or {}).items():
                out.add("hr", at(k), v)
        r = await self.request(pid, "GET", f"{API}/users/nightly-recharge")
        for x in ((r.json() or {}).get("recharges", []) if r.status_code == 200 else []):
            try:
                d = date.fromisoformat(x.get("date") or "")
            except ValueError:
                continue
            t = datetime(d.year, d.month, d.day, 7, tzinfo=tz).timestamp()
            out.add("resting_hr", t, x.get("heart_rate_avg"))
            out.add("hrv", t, x.get("heart_rate_variability_avg"))
            out.add("resp_rate", t, x.get("breathing_rate_avg"))
        return out
