"""Calendar feeds (Fastmail private link, iCloud, Google): what counts as tonight's evening event and an
early start tomorrow."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from embertide.sources import ics_tonight, ics_url
from test_autopilot_suite import H, make

TZ = ZoneInfo("America/New_York")


def ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()


def cal(*events):
    body = "\r\n".join(events)
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n" + body + "\r\nEND:VCALENDAR\r\n")


def ev(uid, start, end, extra=""):
    return f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261101T000000Z\r\n{start}\r\n{end}\r\nSUMMARY:{uid}\r\n{extra}END:VEVENT"


NOW = ts("2026-11-05T17:00")


def test_evening_event_with_time_zone():
    end, early = ics_tonight(cal(ev("concert", "DTSTART;TZID=America/New_York:20261105T190000", "DTEND;TZID=America/New_York:20261105T233000")), NOW, TZ)
    assert end == ts("2026-11-05T23:30") and early is None


def test_utc_times_and_floating_times():
    end, _ = ics_tonight(cal(ev("dinner", "DTSTART:20261106T000000Z", "DTEND:20261106T030000Z")), NOW, TZ)     # 19:00-22:00 local
    assert end == ts("2026-11-05T22:00")
    end, _ = ics_tonight(cal(ev("floating", "DTSTART:20261105T200000", "DTEND:20261105T213000")), NOW, TZ)
    assert end == ts("2026-11-05T21:30")


def test_weekly_recurring_event_is_expanded():
    e = ev("gym", "DTSTART;TZID=America/New_York:20261008T180000", "DTEND;TZID=America/New_York:20261008T193000", "RRULE:FREQ=WEEKLY;BYDAY=TH\r\n")
    end, _ = ics_tonight(cal(e), NOW, TZ)                                     # 2026-11-05 is a Thursday
    assert end == ts("2026-11-05T19:30")


def test_all_day_cancelled_and_free_events_are_skipped():
    c = cal(ev("birthday", "DTSTART;VALUE=DATE:20261105", "DTEND;VALUE=DATE:20261106"),
            ev("off", "DTSTART;TZID=America/New_York:20261105T190000", "DTEND;TZID=America/New_York:20261105T230000", "STATUS:CANCELLED\r\n"),
            ev("reminder", "DTSTART;TZID=America/New_York:20261105T200000", "DTEND;TZID=America/New_York:20261105T235900", "TRANSP:TRANSPARENT\r\n"))
    assert ics_tonight(c, NOW, TZ) == (0.0, None)


def test_event_far_off_or_finished_doesnt_count():
    c = cal(ev("tomorrow-evening", "DTSTART;TZID=America/New_York:20261106T190000", "DTEND;TZID=America/New_York:20261106T220000"),
            ev("lunch", "DTSTART;TZID=America/New_York:20261105T120000", "DTEND;TZID=America/New_York:20261105T130000"))
    assert ics_tonight(c, NOW, TZ)[0] == 0.0


def test_early_start_tomorrow():
    c = cal(ev("flight", "DTSTART;TZID=America/New_York:20261106T063000", "DTEND;TZID=America/New_York:20261106T090000"),
            ev("standup", "DTSTART;TZID=America/New_York:20261106T093000", "DTEND;TZID=America/New_York:20261106T094500"))
    _, early = ics_tonight(c, NOW, TZ)
    assert early == ts("2026-11-06T06:30")
    _, early_after_midnight = ics_tonight(c, ts("2026-11-06T01:00"), TZ)      # still the same morning after midnight
    assert early_after_midnight == ts("2026-11-06T06:30")


def test_feed_address_rules():
    assert ics_url("webcal://p01-caldav.icloud.com/published/2/abc") == "https://p01-caldav.icloud.com/published/2/abc"
    for bad in ("", "file:///etc/passwd", "ftp://x/y", "javascript:alert(1)"):
        with pytest.raises(ValueError):
            ics_url(bad)


def test_api_accepts_feed_and_hides_its_address(tmp_path):
    with make(tmp_path) as c:
        body = {"name": "Calendar", "driver": "ics", "role": "calendar", "kind": "calendar", "person": "p1",
                "params": {"url": "https://user.fm/calendar/v1-SECRETSECRET/Calendar.ics"}, "enabled": False}
        r = c.post("/api/sources", headers=H, json=body)
        assert r.status_code == 200, r.text
        listed = [s for s in c.get("/api/sources").json()["sources"] if s["name"] == "Calendar"][0]
        assert "SECRET" not in str(listed) and listed["params"]["url"].startswith("user.fm/")
        bad = dict(body, params={"url": "file:///etc/passwd"})
        assert c.post("/api/sources", headers=H, json=bad).status_code == 422
        assert c.post("/api/sources", headers=H, json=dict(body, role="presence", kind="radar")).status_code == 422


def test_free_busy_only_feed():
    fb = ("BEGIN:VFREEBUSY\r\nUID:fb1\r\nDTSTAMP:20261101T000000Z\r\n"
          "FREEBUSY;FBTYPE=BUSY:20261106T000000Z/20261106T043000Z\r\n"          # 19:00-23:30 local
          "FREEBUSY;FBTYPE=FREE:20261106T050000Z/20261106T060000Z\r\n"
          "FREEBUSY:20261106T113000Z/PT1H\r\n"                                  # 06:30 tomorrow, as start + duration
          "END:VFREEBUSY")
    end, early = ics_tonight(cal(fb), NOW, TZ)
    assert end == ts("2026-11-05T23:30") and early == ts("2026-11-06T06:30")


def test_an_event_repeating_every_minute_is_left_out_quickly():
    """Review 2026-11-09: a FREQ=MINUTELY event expanded to thousands of copies (~2 s per parse, on the event loop)."""
    import time as _t
    from zoneinfo import ZoneInfo
    from embertide.sources import ics_tonight
    tz = ZoneInfo("America/New_York")
    from datetime import datetime
    now = datetime(2026, 11, 5, 19, 0, tzinfo=tz).timestamp()
    text = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//t//EN\r\n"
            "BEGIN:VEVENT\r\nUID:spam\r\nDTSTART:20260101T000000Z\r\nDTEND:20260101T000030Z\r\nRRULE:FREQ=MINUTELY\r\nSUMMARY:x\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:dinner\r\nDTSTART;TZID=America/New_York:20261105T200000\r\nDTEND;TZID=America/New_York:20261105T223000\r\nSUMMARY:Dinner\r\nEND:VEVENT\r\n"
            "END:VCALENDAR\r\n")
    t0 = _t.perf_counter()
    end, _ = ics_tonight(text, now, tz)
    assert _t.perf_counter() - t0 < 0.5
    assert end == datetime(2026, 11, 5, 22, 30, tzinfo=tz).timestamp()
