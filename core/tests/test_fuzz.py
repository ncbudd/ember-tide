"""API fuzz: every operation in the OpenAPI schema gets malformed, boundary and random payloads and odd path
parameters. Nothing may answer 500, and the household must still load (also after a restart from the same data
folder). Deterministic seed; FUZZ_ROUNDS raises the number of random payloads per operation (default 6).

Network-touching endpoints are pointed at a closed local port so the fuzz never reaches the internet.
"""

from __future__ import annotations

import json
import os
import random

from fastapi.testclient import TestClient

from embertide.app import create_app
from embertide.config import Config

ROUNDS = int(os.environ.get("FUZZ_ROUNDS", "6"))
H = {"X-Embertide": "1", "Content-Type": "application/json"}
DEAD = "http://127.0.0.1:9/x"                        # nothing listens here: connection refused, fast
WEIRD_STRINGS = ["", " ", "x" * 5000, "😴🔥" * 50, "../../etc/passwd", "<script>alert(1)</script>", "\u0000", "' OR 1=1 --",
                 "NaN", "-1", "1e309", "null", "true", "{}", "[]", "p1/../p2", "%00", "\r\nX-Header: y"]
WEIRD_NUMBERS = [0, -1, -1e9, 1e9, 1e308, 2 ** 63, -0.0, 0.5, 3.14159, float("nan"), float("inf"), float("-inf")]
PATH_VALUES = ["p1", "p2", "zz", "weeknights", "0", "-1", "x" * 300, "%2e%2e", "😴", "null"]


def make_app(tmp_path, auth=False):
    cfg = Config()
    cfg.data_dir, cfg.tz, cfg.bed, cfg.web_dir, cfg.auth, cfg.llm_url = tmp_path, "America/New_York", "sim", tmp_path / "noweb", auth, None
    return create_app(cfg)


def resolve(schema, spec):
    if "$ref" in schema:
        return resolve(spec["components"]["schemas"][schema["$ref"].split("/")[-1]], spec)
    if "anyOf" in schema:
        non_null = [s for s in schema["anyOf"] if s.get("type") != "null"]
        return resolve(non_null[0], spec) if non_null else {"type": "null"}
    if "allOf" in schema:
        return resolve(schema["allOf"][0], spec)
    return schema


def sample(schema, spec, rnd, depth=0):
    """A plausible value for a schema (defaults first), used as the base that mutations start from."""
    s = resolve(schema, spec)
    if "default" in s and s["default"] is not None:
        return s["default"]
    if "enum" in s:
        return rnd.choice(s["enum"])
    if "const" in s:
        return s["const"]
    t = s.get("type")
    if t == "object" or "properties" in s:
        props = s.get("properties", {})
        out = {}
        for k, v in props.items():
            if k in s.get("required", []) or rnd.random() < 0.5:
                out[k] = sample(v, spec, rnd, depth + 1) if depth < 4 else None
        for k in list(out):
            if k in ("url", "public_url", "push_server", "host") and isinstance(out[k], str):
                out[k] = DEAD
        return out
    if t == "array":
        return [sample(s.get("items", {}), spec, rnd, depth + 1) for _ in range(rnd.randint(0, 2))] if depth < 4 else []
    if t == "string":
        if s.get("pattern"):
            return "22:15"
        return rnd.choice(["p1", "left", "hot", "test", "22:15"])
    if t == "integer":
        lo, hi = s.get("minimum", 0), s.get("maximum", 100)
        return rnd.randint(int(lo), int(min(hi, lo + 100)))
    if t == "number":
        return float(s.get("minimum", 0)) + rnd.random() * 5
    if t == "boolean":
        return rnd.random() < 0.5
    return None


def mutate(base, rnd):
    """One malformed variant of a payload."""
    choice = rnd.randrange(12)
    if choice == 0:
        return rnd.choice([None, [], "string", 42, [base], {"wrapped": base}])
    if not isinstance(base, dict) or not base:
        return rnd.choice([{}, {"x": 1}, WEIRD_STRINGS[2], [1] * 100000])
    b = dict(base)
    k = rnd.choice(list(b))
    if choice == 1:
        b.pop(k)
    elif choice == 2:
        b[k] = rnd.choice(WEIRD_STRINGS)
    elif choice == 3:
        b[k] = rnd.choice(WEIRD_NUMBERS)
    elif choice == 4:
        b[k] = rnd.choice([None, True, [], {}, [None], {"$ne": 1}])
    elif choice == 5:
        b["extra_" + rnd.choice(["field", "x", "__proto__"])] = rnd.choice(WEIRD_STRINGS + [1, None])
    elif choice == 6:
        b[k] = [rnd.choice(WEIRD_NUMBERS + [None, "x"]) for _ in range(rnd.choice([1, 10, 60000]))]
    elif choice == 7:
        b[k] = {"nested": {"deep": [1, 2, {"x": "y" * 1000}]}}
    elif choice == 8:
        b = {kk: rnd.choice(WEIRD_NUMBERS) for kk in b}
    elif choice == 9:
        b = {kk: rnd.choice(WEIRD_STRINGS) for kk in b}
    elif choice == 10:
        b[k] = -abs(b[k]) if isinstance(b[k], (int, float)) and not isinstance(b[k], bool) else b[k]
    else:
        b[k] = "x" * 200_000
    return b


def dumps(obj) -> str:
    return json.dumps(obj, allow_nan=True, default=str)       # NaN / Infinity on purpose: Python's JSON accepts them


def ops(spec):
    for path, items in spec["paths"].items():
        if not path.startswith("/api"):
            continue
        for method, op in items.items():
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            params = op.get("parameters", [])
            yield method.upper(), path, body, params


def fill_path(path, rnd, valid=True):
    out = path
    for name in ("pid", "rid", "sid", "uid", "iid", "tid", "nid", "code"):
        if "{" + name + "}" in out:
            v = {"pid": "p1", "rid": "weeknights"}.get(name, "zz") if valid else rnd.choice(PATH_VALUES)
            out = out.replace("{" + name + "}", v)
    return out


def query(params, rnd, odd=False):
    q = {}
    for p in params:
        if p.get("in") != "query":
            continue
        q[p["name"]] = rnd.choice([str(x) for x in WEIRD_NUMBERS] + WEIRD_STRINGS[:4]) if odd else (
            {"date": "2026-11-05", "fmt": "json", "zone": "left"}.get(p["name"], "1"))
    return q


SKIP_BODY_FUZZ = set()


def run_fuzz(c, spec, rnd, failures, rounds=ROUNDS):
    for method, path, body, params in ops(spec):
        for valid_path in (True, False):
            url = fill_path(path, rnd, valid_path)
            payloads = []
            if body is not None:
                base = sample(body, spec, rnd)
                payloads.append(dumps(base))
                payloads += [dumps(mutate(base, rnd)) for _ in range(rounds)]
                payloads += ["not json", "{", "", "[" * 500 + "]" * 500]
            else:
                payloads.append(None)
            for i, data in enumerate(payloads):
                q = query(params, rnd, odd=i % 2 == 1)
                headers = dict(H) if rnd.random() < 0.85 else {"Content-Type": "application/json"}
                try:
                    r = c.request(method, url, content=data, params=q, headers=headers)
                except Exception as e:  # noqa: BLE001   the test client re-raises server exceptions
                    failures.append((method, url, (data or "")[:200], f"EXCEPTION {type(e).__name__}: {e}"[:300]))
                    continue
                if r.status_code >= 500:
                    failures.append((method, url, (data or "")[:200], f"{r.status_code} {r.text[:200]}"))


def assert_healthy(c):
    assert c.get("/api/household").status_code == 200
    s = c.get("/api/snapshot")
    assert s.status_code == 200 and len(s.json()["people"]) == 2


def test_fuzz_every_route_auth_off(tmp_path):
    rnd = random.Random(1234)
    app = make_app(tmp_path)
    failures: list = []
    with TestClient(app, raise_server_exceptions=False) as c:
        spec = c.get("/openapi.json").json()
        assert sum(1 for _ in ops(spec)) >= 80
        run_fuzz(c, spec, rnd, failures)
        assert_healthy(c)
        hh = c.get("/api/household").json()
    assert not failures, "\n".join(f"{m} {u} {d!r} -> {why}" for m, u, d, why in failures[:40])
    # restart from the same data folder: everything that was stored still loads
    with TestClient(make_app(tmp_path), raise_server_exceptions=False) as c2:
        assert_healthy(c2)
        assert [p["id"] for p in c2.get("/api/household").json()["people"]] == [p["id"] for p in hh["people"]]


def test_fuzz_signed_out_with_auth_on(tmp_path):
    """Sign-in on and nobody signed in: everything that isn't public refuses politely (401/403), never 500."""
    rnd = random.Random(99)
    app = make_app(tmp_path, auth=True)
    failures, leaks = [], []
    with TestClient(app, raise_server_exceptions=False) as c:
        spec = c.get("/openapi.json").json()
        public = {("GET", "/api/auth/state"), ("POST", "/api/auth/login"), ("POST", "/api/auth/setup"), ("POST", "/api/auth/logout"),
                  ("GET", "/api/auth/invite"), ("POST", "/api/auth/accept"), ("GET", "/api/notify/act/{code}"), ("POST", "/api/notify/act/{code}"),
                  ("POST", "/api/client-error")}     # app crash reports: public on purpose (small, rate-limited)
        for method, path, body, params in ops(spec):
            url = fill_path(path, rnd)
            for hdr in ({}, {"Authorization": "Bearer et_garbage"}, {"Authorization": "Bearer " + "x" * 5000},
                        {"Cookie": "et_session=forged"}, {"Authorization": "Basic YWRtaW46YWRtaW4="}):
                data = dumps(sample(body, spec, rnd)) if body else None
                r = c.request(method, url, content=data, headers={**H, **hdr}, params=query(params, rnd))
                if r.status_code >= 500:
                    failures.append((method, url, hdr, r.status_code, r.text[:150]))
                elif (method, path) not in public and r.status_code < 400:
                    leaks.append((method, url, hdr, r.status_code))
        assert c.get("/api/auth/state").status_code == 200
    assert not failures, failures[:20]
    assert not leaks, f"reachable without signing in: {leaks[:20]}"


def test_huge_lists_and_bodies_are_refused(tmp_path):
    with TestClient(make_app(tmp_path), raise_server_exceptions=False) as c:
        pts = [[1_700_000_000 + i, 60] for i in range(60_000)]
        assert c.post("/api/people/p1/wearable", headers=H, json={"device": "W", "metric": "hr", "points": pts}).status_code == 422
        segs = [{"start": 1_700_000_000 + i * 60, "end": 1_700_000_000 + i * 60 + 30, "stage": "light"} for i in range(60_000)]
        r = c.post("/api/people/p1/stages", headers=H, json={"source": "x", "segments": segs})
        assert r.status_code in (400, 413, 422), r.status_code
        assert_healthy(c)


def test_nan_and_infinity_never_get_stored(tmp_path):
    with TestClient(make_app(tmp_path), raise_server_exceptions=False) as c:
        for body in ('{"delta_c": NaN}', '{"temp_c": Infinity}', '{"delta_c": -Infinity, "until": "next_phase"}'):
            r = c.post("/api/people/p1/adjust", headers=H, content=body)
            assert r.status_code in (400, 422), (body, r.status_code, r.text[:200])
        r = c.post("/api/people/p1/context", headers=H, content='{"wake": NaN}')
        assert r.status_code in (400, 422), r.text[:200]
        s = c.get("/api/snapshot").json()
        txt = json.dumps(s)
        assert "NaN" not in txt and "Infinity" not in txt
        assert_healthy(c)


def test_regressions_found_by_fuzz(tmp_path):
    """Each of these used to answer 500."""
    with TestClient(make_app(tmp_path), raise_server_exceptions=True) as c:
        # a validation error carrying NaN/inf in its input could not be encoded
        assert c.post("/api/auth/invites", headers=H, content='{"person": NaN}').status_code == 422
        # bad dates on the Autopilot endpoints
        for path, body in (("/api/people/p1/autopilot/replay", {"date": "test", "policy": {}}),
                           ("/api/people/p1/autopilot/recheck", {"date": "<script>"}),
                           ("/api/people/p1/autopilot/ai-review", {"date": "p1"})):
            assert c.post(path, headers=H, json=body).status_code == 400, path
        # a rhythm with impossible weekdays or a clock anchor that isn't a time
        rh = c.get("/api/people/p1/rhythms").json()[0]
        assert c.put("/api/people/p1/rhythms/weeknights", headers=H, json=dict(rh, days=[89])).status_code == 422
        ph = [dict(x) for x in rh["phases"]]
        ph[2] = dict(ph[2], start={"kind": "clock", "time": "hot", "offset_min": 0})
        assert c.put("/api/people/p1/rhythms/weeknights", headers=H, json=dict(rh, phases=ph)).status_code == 422
        # both people on one side made "swap sides" crash
        assert c.put("/api/people/p2/profile", headers=H, json={"zone": "left"}).status_code == 409
        assert c.post("/api/household/swap-sides", headers=H).status_code == 200
        assert_healthy(c)


def test_simulator_inputs_are_checked(tmp_path):
    with TestClient(make_app(tmp_path), raise_server_exceptions=True) as c:
        assert c.post("/api/sim", headers=H, json={"jump_to": "hot"}).status_code == 422
        assert c.post("/api/sim", headers=H, json={"jump_to": "22:15"}).status_code == 422
        assert c.post("/api/sim", headers=H, content='{"speed": NaN}').status_code == 422
        assert c.post("/api/sim", headers=H, json={"speed": -5}).status_code == 422
        assert c.post("/api/sim", headers=H, json={"presence": {"middle": True}}).status_code == 422
        assert c.post("/api/sim", headers=H, json={"jump_to": "2026-11-06T22:20", "speed": 60}).status_code == 200
        assert_healthy(c)


def test_calibration_rejects_non_finite_and_absurd_values(tmp_path):
    with TestClient(make_app(tmp_path), raise_server_exceptions=True) as c:
        before = c.get("/api/calibration").json()
        for body in ('{"weights": {}, "weight_threshold_kg": Infinity, "tare_kg": {}}', '{"weights": {"bed": NaN}}',
                     '{"weights": {}, "person_kg": {"p1": 9999}}', '{"weights": {"bed": -3}}'):
            assert c.put("/api/calibration", headers=H, content=body).status_code == 422, body
        assert c.get("/api/calibration").json() == before                     # nothing was stored
        assert c.put("/api/calibration", headers=H, json=dict(before, weight_threshold_kg=25)).status_code == 200
