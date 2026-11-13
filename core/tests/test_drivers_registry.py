"""The bed driver registry and the ET_BED=python:module:Class escape hatch."""

import pytest
from fastapi.testclient import TestClient

from embertide.app import DRIVERS, create_app, load_driver_class, make_driver
from embertide.clock import Clock
from embertide.config import Config
from embertide.drivers.base import Cap
from embertide.drivers.example_template import ExampleBed


def cfg(tmp_path, bed):
    c = Config()
    c.data_dir, c.tz, c.bed, c.web_dir, c.llm_url, c.auth = tmp_path, "America/New_York", bed, tmp_path / "noweb", None, False
    return c


def test_registry_has_the_builtins():
    assert set(DRIVERS) == {"sim", "orion-mqtt", "orion-cloud"}
    c = Config()
    c.bed = "sim"
    assert make_driver(c, Clock("UTC")).id == "sim"
    c.bed = "orion-mqtt"
    c.mqtt_url = "mqtt://broker:1883"
    assert make_driver(c, Clock("UTC")).id == "orion-mqtt"
    c.bed = "orion-cloud"
    c.orion_api_key = None
    with pytest.raises(SystemExit, match="ET_ORION_API_KEY"):
        make_driver(c, Clock("UTC"))


def test_unknown_driver_names_the_choices():
    c = Config()
    c.bed = "orion-mqt"
    with pytest.raises(SystemExit, match="orion-mqtt"):
        make_driver(c, Clock("UTC"))


def test_python_escape_hatch_loads_a_class():
    assert load_driver_class("python:embertide.drivers.example_template:ExampleBed") is ExampleBed
    c = Config()
    c.bed = "python:embertide.drivers.example_template:ExampleBed"
    d = make_driver(c, Clock("UTC"))
    assert isinstance(d, ExampleBed) and Cap.ZONE_TEMP in d.capabilities


@pytest.mark.parametrize("bad,why", [
    ("python:nothing", "use python:package.module:ClassName"),
    ("python:no_such_pkg_xyz:Bed", "can't import"),
    ("python:embertide.drivers.example_template:Missing", "no class"),
])
def test_bad_paths_give_a_clear_error(bad, why):
    with pytest.raises(ValueError, match=why):
        load_driver_class(bad)
    c = Config()
    c.bed = bad
    with pytest.raises(SystemExit, match=why):
        make_driver(c, Clock("UTC"))


def test_a_class_missing_methods_is_refused():
    c = Config()
    c.bed = "python:embertide.config:Config"
    with pytest.raises(SystemExit, match="missing"):
        make_driver(c, Clock("UTC"))


def test_example_driver_runs_the_app(tmp_path):
    with TestClient(create_app(cfg(tmp_path, "python:embertide.drivers.example_template:ExampleBed"))) as c:
        snap = c.get("/api/snapshot").json()
        assert snap["system"]["online"] is True
