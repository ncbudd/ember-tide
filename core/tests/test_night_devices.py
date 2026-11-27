"""The "Readings from …" list names each watch or ring once, however its sources spell it."""

from types import SimpleNamespace

from embertide.engine import wearables as W


def test_same_device_listed_once(monkeypatch):
    series = {"hr": {"a": [(1, 60.0)]}, "skin_temp": {"b": [(1, 33.0)]}}
    names = {"a": "Simulated watch", "b": "Simulated  Watch"}
    monkeypatch.setattr(W, "ROLES", ("hr", "skin_temp"))
    monkeypatch.setattr(W, "device_series", lambda c, p, metric, s, e, worn_only=False: series.get(metric, {}))
    monkeypatch.setattr(W, "device_name", lambda c, k: names[k])
    store = SimpleNamespace(get=lambda key, default=None: default,       # no saved sleeps (engine/sleeps.py)
                            get_stages=lambda pid, s, e: {"demo_watch": {"label": "Simulated Watch · sim"},
                                                          "ring": {"label": "Oura Ring"}})
    out = W.night_devices(SimpleNamespace(store=store), SimpleNamespace(id="p1"), 0, 10)
    assert [d["name"] for d in out] == ["Oura Ring", "Simulated watch"]
    assert out[1]["what"] == ["hr", "skin_temp", "stages"]
