"""Oura ring (API v2, OAuth2; personal access tokens were retired in Dec 2025).

Authorize https://cloud.ouraring.com/oauth/authorize, token https://api.ouraring.com/oauth/token (form, client id
and secret in the body), single-use refresh tokens. Scopes: daily (sleep, readiness), heartrate. A new app can
connect up to 10 people before Oura's review, plenty for a home. Redirect URI: <public>/api/oura/callback.
"""

from __future__ import annotations

from datetime import date, datetime

from .base import CloudConnector, CloudData, CloudError, merge_segments, ts

API = "https://api.ouraring.com/v2/usercollection"
STAGE = {"1": "deep", "2": "light", "3": "rem", "4": "awake"}     # sleep_phase_5_min


class Oura(CloudConnector):
    id = "oura"
    name = "Oura"
    device = "Oura"
    auth_url = "https://cloud.ouraring.com/oauth/authorize"
    token_url = "https://api.ouraring.com/oauth/token"
    revoke_url = "https://api.ouraring.com/oauth/revoke"
    scopes = "daily heartrate"
    gives = "sleep stages, heart rate and HRV through the night, temperature compared with your normal, readiness and sleep score"
    docs_url = "https://developer.ouraring.com"
    callback_path = "/api/oura/callback"
    data_version = 3                     # 2: sleep score, temperature trend, lowest HR, breathing; 3: naps (every sleep period)

    async def _all(self, pid: str, path: str, params: dict) -> list[dict]:
        out: list[dict] = []
        q = dict(params)
        for _ in range(20):
            r = await self.request(pid, "GET", f"{API}/{path}", params=q)
            j = r.json() if r.status_code == 200 else {}
            out.extend(j.get("data") or [])
            if not j.get("next_token"):
                break
            q = {**params, "next_token": j["next_token"]}
        return out

    async def fetch(self, pid: str, start: date, end: date, now: float) -> CloudData:
        tz = self.c.clock.tz
        days = {"start_date": start.isoformat(), "end_date": end.isoformat()}
        sleeps = await self._all(pid, "sleep", days)
        ready = await self._all(pid, "daily_readiness", days)
        daily_sleep = await self._all(pid, "daily_sleep", days)
        try:
            hr_rows = await self._all(pid, "heartrate", {"start_datetime": datetime(start.year, start.month, start.day, tzinfo=tz).isoformat(),
                                                         "end_datetime": datetime(end.year, end.month, end.day, tzinfo=tz).isoformat()})
        except CloudError as e:
            if e.status != 403:
                raise
            hr_rows = []                      # no heartrate scope: the sleep sessions still carry 5-minute heart rate
        out = CloudData()
        by_day: dict[str, float] = {}
        ready_days = {r.get("day") for r in ready}
        for s in sleeps:
            if s.get("type") not in (None, "long_sleep", "sleep", "late_nap"):
                continue                                          # naps and rest periods aren't nights
            t0, t1 = ts(s.get("bedtime_start")), ts(s.get("bedtime_end"))
            if t0 is None or t1 is None or t1 <= t0:
                continue
            if s.get("day"):
                by_day[s["day"]] = max(by_day.get(s["day"], 0), t1)
            for key, metric in (("heart_rate", "hr"), ("hrv", "hrv")):
                if metric == "hr" and hr_rows:
                    continue                                      # the dense heart-rate feed covers it
                for t, v in series(s.get(key) or {}):
                    out.add(metric, t, v)
            segs = [{"start": t0 + i * 300, "end": t0 + i * 300 + 300, "stage": STAGE[ch]}
                    for i, ch in enumerate(s.get("sleep_phase_5_min") or "") if ch in STAGE]
            kind = "night" if s.get("type") == "long_sleep" or t1 - t0 >= 3 * 3600 else "nap"
            if segs and kind == "night":
                out.stages.append((t0, t1, merge_segments(segs)))        # a nap's stages stay with the nap
            out.periods.append(period(s, t0, t1, kind, merge_segments(segs) if segs and kind == "nap" else []))
            if s.get("type") in (None, "long_sleep"):                # the night's own numbers, stamped at its end
                out.add("resting_hr", t1, s.get("lowest_heart_rate"))
                out.add("night_breath", t1, s.get("average_breath"))
            dev = (s.get("readiness") or {}).get("temperature_deviation")
            if s.get("day") not in ready_days:
                out.add("temp_dev", t1, dev)
        for r in hr_rows:
            if r.get("source") in ("sleep", "rest", "awake", "live", "workout", "session", None):
                out.add("hr", ts(r.get("timestamp")), r.get("bpm"))
        for r in ready:
            t = by_day.get(r.get("day") or "") or morning(r.get("day"), tz)
            out.add("temp_dev", t, r.get("temperature_deviation"))
            out.add("readiness", t, r.get("score"))
            out.add("temp_trend", t, r.get("temperature_trend_deviation"))     # the 3-day trend (cycle-friendly)
        for r in daily_sleep:
            t = by_day.get(r.get("day") or "") or morning(r.get("day"), tz)
            out.add("sleep_score", t, r.get("score"))
        return out


def period(s: dict, t0: float, t1: float, kind: str, segs: list[dict]) -> dict:
    """One Oura sleep (a night or a nap) as a sleep record (engine/sleeps.py)."""
    mins = lambda k: round(s[k] / 60) if isinstance(s.get(k), (int, float)) else None     # noqa: E731
    num = lambda k: s[k] if isinstance(s.get(k), (int, float)) and not isinstance(s.get(k), bool) else None   # noqa: E731
    return {"id": f"oura:{s.get('id') or int(t0)}", "source": "oura", "device": "Oura", "kind": kind, "start": t0, "end": t1,
            "asleep_min": mins("total_sleep_duration"), "deep_min": mins("deep_sleep_duration"), "rem_min": mins("rem_sleep_duration"),
            "light_min": mins("light_sleep_duration"), "awake_min": mins("awake_time"), "efficiency": num("efficiency"),
            "latency_min": mins("latency"), "hr_avg": num("average_heart_rate"), "hr_low": num("lowest_heart_rate"),
            "hrv_avg": num("average_hrv"), "breath": num("average_breath"), "segments": segs}


def morning(day: str | None, tz) -> float | None:
    try:
        d = date.fromisoformat(day or "")
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, 7, 0, tzinfo=tz).timestamp()


def series(ser: dict) -> list[tuple[float, float]]:
    """Oura's {interval, items, timestamp} → [(t, v), ...], skipping gaps (None)."""
    t0 = ts(ser.get("timestamp"))
    step = ser.get("interval")
    if t0 is None or not isinstance(step, (int, float)) or step <= 0:
        return []
    return [(t0 + i * step, float(v)) for i, v in enumerate(ser.get("items") or []) if isinstance(v, (int, float))]
