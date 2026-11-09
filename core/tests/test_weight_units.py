"""Weight units (lb / kg) are their own setting: "auto" follows the temperature units, and a pick sticks."""

from test_use_cases import H, make, night


def test_weight_units_default_auto_and_patch_persists(tmp_path):
    with make(tmp_path) as c:
        assert night(c)["settings"]["weight_units"] == "auto"
        r = c.patch("/api/people/p1/settings", json={"units": "F", "weight_units": "kg"}, headers=H)
        assert r.status_code == 200, r.text
        s = night(c)["settings"]
        assert s["weight_units"] == "kg" and s["units"] == "F"          # °F with kilograms is allowed
        assert night(c, "p2")["settings"]["weight_units"] == "auto"    # only that person changed
        # a later change to something else keeps the pick
        assert c.patch("/api/people/p1/settings", json={"units": "C"}, headers=H).status_code == 200
        assert night(c)["settings"]["weight_units"] == "kg"
    with make(tmp_path) as c:                                          # survives a restart (same data dir)
        assert night(c)["settings"]["weight_units"] == "kg"


def test_weight_units_rejects_unknown_values(tmp_path):
    with make(tmp_path) as c:
        for bad in ("stone", "LB", "", None):
            r = c.patch("/api/people/p1/settings", json={"weight_units": bad}, headers=H)
            assert r.status_code == 422, bad
        s = night(c)["settings"]
        bad = c.put("/api/people/p1/settings", json=s | {"weight_units": "pounds"}, headers=H)
        assert bad.status_code == 422
        assert night(c)["settings"]["weight_units"] == "auto"
        ok = c.put("/api/people/p1/settings", json=s | {"weight_units": "lb"}, headers=H)
        assert ok.status_code == 200 and night(c)["settings"]["weight_units"] == "lb"
