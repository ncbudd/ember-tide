"""What a person's own phone tells us (the Android app's "From this phone" switches): at home or away, on the way
home, on the home Wi-Fi, tonight's last calendar event and tomorrow's first, the next alarm, on the charger,
bedtime mode, the watch connected. No setup in Studio or Home Assistant: the app sends it, this merges it into the
person's context next to any devices.

Rules
- Phone location counts like a GPS device: "home" is definite and wins; "away" wins over a Wi-Fi device that
  merely hasn't seen the phone. On the way home with the arrival inside 30 min, the person counts as home (so the
  bed is ready on arrival).
- Home Wi-Fi from the phone counts as home; "not on Wi-Fi" alone never makes someone away.
- Every field goes stale: location/heading after 45 min, home/away after 6 h, Wi-Fi after 30 min, charging and
  bedtime mode after 20 min, the watch after 30 min. Calendar times and the alarm are used until they pass.
- Only times and yes/no values ever arrive: no event titles, no coordinates.
"""

from __future__ import annotations

import time as _time

READY_MIN = 30                    # heading home this close → get the bed ready
TRAVEL_S = 20 * 60                # after an evening event, time to get home
# charging/bedtime: Android batches the 15-min check; real check-ins came 15-35 min apart overnight (2026-11-08)
STALE = {"location": 45 * 60, "home": 6 * 3600, "wifi": 30 * 60, "charging": 50 * 60, "bedtime": 50 * 60, "watch": 30 * 60}

# person id → {"data": {...}, "ts": {field: when it was last reported}}, per store (tests run several apps)


def _bucket(store) -> dict[str, dict]:
    # kept on the store itself: keyed by id(store), a new store could inherit a freed one's address and its phones
    b = getattr(store, "_phone_ctx_cache", None)
    if b is None:
        b = {}
        store._phone_ctx_cache = b
    return b


def latest(store, pid: str) -> dict | None:
    b = _bucket(store)
    if pid not in b:
        saved = store.get(f"phone_ctx:{pid}")
        if saved:
            b[pid] = saved
    return b.get(pid)


def update(store, pid: str, fields: dict, now: float, device: str | None = None, sharing: list[str] | None = None) -> dict:
    """Store what the app sent. Only fields present are updated; each keeps its own time."""
    cur = latest(store, pid) or {"data": {}, "ts": {}}
    for k, v in fields.items():
        cur["data"][k] = v
        cur["ts"][k] = now
    if device:
        cur["device"] = device[:60]
    if sharing is not None:
        cur["sharing"] = [s for s in sharing if isinstance(s, str)][:12]
        # a switch turned off: forget what it used to say
        drop = {"location": ("home", "distance_km", "heading_home", "eta_min"), "wifi": ("wifi_home",),
                "calendar": ("evening_event_end", "early_start"), "alarm": ("next_alarm",),
                "charging": ("charging", "bedtime_mode"), "watch": ("watch_connected",), "dial": ("dials",)}
        if "watch_hr" not in cur["sharing"] and store.get(f"watch_live:{pid}"):
            store.put(f"watch_live:{pid}", None)       # live watch heart rate switched off: stop using it at once
        for src, keys in drop.items():
            if src not in cur["sharing"]:
                for k in keys:
                    cur["data"].pop(k, None)
                    cur["ts"].pop(k, None)
    # on the charger since when (a phone that hasn't been unplugged hasn't moved)
    if "charging" in fields:
        if fields["charging"] and not cur.get("charging_since"):
            cur["charging_since"] = now
        elif not fields["charging"]:
            cur["charging_since"] = None
            cur["near"] = None
    if "dials" in fields:
        side = _near_side(store, fields.get("dials") or [], now, now)
        if side:
            cur["near"] = {"side": side, "at": now}
    cur["seen"] = now
    _bucket(store)[pid] = cur
    store.put(f"phone_ctx:{pid}", cur)
    return cur


def forget(store, pid: str) -> None:
    _bucket(store).pop(pid, None)
    store.put(f"phone_ctx:{pid}", None)


def fresh(cur: dict, key: str, kind: str, now: float):
    ts = (cur.get("ts") or {}).get(key)
    if ts is None or now - ts > STALE[kind] or now < ts - 300:
        return None
    return (cur.get("data") or {}).get(key)


def merge(ctx, store, p, now: float | None = None) -> None:
    """Fold the phone's report into a PersonContext (in place)."""
    now = _time.time() if now is None else now
    cur = latest(store, p.id)
    if not cur:
        return
    ctx.phone_seen = cur.get("seen")
    home = fresh(cur, "home", "home", now)
    dist = fresh(cur, "distance_km", "location", now)
    heading = fresh(cur, "heading_home", "location", now)
    eta = fresh(cur, "eta_min", "location", now)
    wifi = fresh(cur, "wifi_home", "wifi", now)
    from_gps_device = (ctx.why or "").startswith(("phone location", "on the way home"))
    if dist is not None and ctx.distance_km is None:
        ctx.distance_km = round(float(dist), 2)
    if heading and eta is not None and not from_gps_device:
        ctx.heading_home, ctx.eta_min = True, round(float(eta), 1)
    if home is True:
        ctx.home, ctx.why = True, "your phone: home"
    elif heading and eta is not None and float(eta) <= READY_MIN and not (ctx.home is True and from_gps_device):
        ctx.home, ctx.why = True, f"your phone: on the way home, about {round(float(eta))} min out"
    elif home is False and not (ctx.home is True and from_gps_device):
        ctx.home = False
        ctx.why = f"your phone: {round(float(dist), 1)} km away" if dist is not None else "your phone: away from home"
    if wifi is True and ctx.home is not True:
        ctx.home, ctx.why = True, "your phone is on the home Wi-Fi"
    # calendar on the phone: only times
    ev = (cur.get("data") or {}).get("evening_event_end")
    if ev and ev > now and ev - now < 20 * 3600:
        ctx.event_end = max(ctx.event_end or 0, float(ev)) or None
        ctx.busy_until = max(ctx.busy_until or 0, float(ev) + TRAVEL_S) or None
    es = (cur.get("data") or {}).get("early_start")
    if es and es > now and es - now < 30 * 3600:
        ctx.early_start = float(es) if ctx.early_start is None else min(ctx.early_start, float(es))
    al = (cur.get("data") or {}).get("next_alarm")
    if al and al > now and al - now < 24 * 3600:
        ctx.next_alarm = float(al)
    ctx.charging = fresh(cur, "charging", "charging", now)
    ctx.bedtime_mode = fresh(cur, "bedtime_mode", "bedtime", now)
    ctx.watch_connected = fresh(cur, "watch_connected", "watch", now)
    side = bedside_side(store, cur, now)
    ctx.near_dial = side
    if side and ctx.home is not True:
        ctx.home, ctx.why = True, f"your phone is by the {side} dial"


# ---------- bedside dials (Bluetooth beacons) ----------
# Each bedside dial advertises an iBeacon (Ember & Tide UUID, major 1, minor = which dial). At night, with the phone
# on the charger or in bedtime mode, the app does a short low-power scan and reports the dials it hears. A phone
# lying next to the left dial is a good hint that its owner is in bed on the left. It's a WEAK in-bed vote for that
# person's own side only (like a motion sensor); hearing no dial is never a "not in bed" vote.
DIAL_UUID = "7c1f0e2a-5d3b-4e8a-9b6c-ed0a11e1de00"
DEFAULT_BEACONS = [{"minor": 1, "side": "left", "name": "Left dial"}, {"minor": 2, "side": "right", "name": "Right dial"}]
NEAR_RSSI = -70.0              # dBm: this strong means the phone is right by that dial
CLEARER_DB = 8.0               # or this much stronger than the other dial
SIGHTING_S = 20 * 60           # a sighting counts for this long (the phone scans every 15 min)
FAINT_RSSI = -88.0             # weaker than this the other dial is across the room
STICKY_S = 3 * 3600            # a near sighting holds while the phone stays plugged in, up to this long
BEDSIDE_WEIGHT = 0.15          # its share in the in-bed vote (the bed strip is 0.5)


def dial_beacons(store) -> list[dict]:
    v = store.get("dial_beacons")
    return v if isinstance(v, list) and v else DEFAULT_BEACONS


def bedside_side(store, cur: dict | None, now: float) -> str | None:
    """Which side's dial the phone is lying by right now, or None."""
    if not cur:
        return None
    ts = (cur.get("ts") or {}).get("dials")
    seen = (cur.get("data") or {}).get("dials") or []
    if fresh(cur, "charging", "charging", now) or fresh(cur, "bedtime_mode", "bedtime", now):
        side = _near_side(store, seen, ts, now) if ts is not None and now - ts <= SIGHTING_S else None
        if side:
            return side                               # only a phone put down for the night counts
    elif not (cur.get("data") or {}).get("charging"):
        return None
    # (no fresh report: Android 15+ can hold the phone's check for an hour or more; the last report said plugged in)
    # a scan with the screen off can miss the dial: if the phone was heard right by a dial and hasn't been unplugged
    # since, it's still lying there (2026-11-08: one 02:09 scan caught only the far dial)
    near, since = cur.get("near") or {}, cur.get("charging_since")
    if near.get("side") and since is not None and since <= near.get("at", 0) and now - near["at"] <= STICKY_S:
        # ...unless this scan hears the OTHER side's dial as the strongest, and not faintly: then it has moved
        sides = {int(b["minor"]): b["side"] for b in dial_beacons(store) if b.get("side") in ("left", "right")}
        strongest = max(((float(d.get("rssi", -999)), sides.get(int(d.get("minor", -1)))) for d in seen), default=None)
        if strongest and strongest[1] and strongest[1] != near["side"] and strongest[0] >= FAINT_RSSI:
            return None
        return near["side"]
    return None


def _near_side(store, seen: list, ts: float, now: float) -> str | None:
    """From one scan's dials: the side whose dial the phone is right by, or None."""
    sides = {int(b["minor"]): b["side"] for b in dial_beacons(store) if b.get("side") in ("left", "right")}
    heard = sorted(((float(d["rssi"]), sides.get(int(d["minor"]))) for d in seen
                    if sides.get(int(d.get("minor", -1))) and now - float(d.get("seen_at") or ts) <= SIGHTING_S),
                   reverse=True)
    if not heard:
        return None
    best_rssi, best_side = heard[0]
    other = next((r for r, s in heard[1:] if s != best_side), None)
    if best_rssi >= NEAR_RSSI or (other is not None and best_rssi - other >= CLEARER_DB):
        return best_side
    return None


def bedside_vote(store, household, zone: str, now: float) -> float | None:
    """1.0 when the phone of the person on `zone` is lying by that side's dial; otherwise no vote at all."""
    p = next((x for x in household.people if x.zone == zone), None)
    if p is None:
        return None
    return 1.0 if bedside_side(store, latest(store, p.id), now) == zone else None
