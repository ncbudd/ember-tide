"""ET_TRUSTED_PROXIES: which peers may vouch for forwarding headers."""

import pytest

from embertide.app import create_app
from embertide.auth_api import client_ip, is_trusted_peer, parse_nets
from test_auth_hardening import cfg_for

OUT = "93.184.216.34"
HDR = {"x-forwarded-for": OUT}


def test_default_trusts_any_private_peer():
    assert client_ip(HDR, "172.18.0.5", True) == OUT
    assert client_ip(HDR, "10.1.2.3", True) == OUT
    assert client_ip(HDR, "8.8.8.8", True) == "8.8.8.8"          # straight from the internet: the header is just words
    assert client_ip(HDR, "172.18.0.5", False) == "172.18.0.5"


def test_listed_networks_only():
    nets = parse_nets("172.18.0.0/16, 10.0.0.5")
    assert client_ip(HDR, "172.18.4.4", True, nets) == OUT
    assert client_ip(HDR, "10.0.0.5", True, nets) == OUT
    assert client_ip(HDR, "192.168.1.9", True, nets) == "192.168.1.9"        # private, but not listed
    assert client_ip(HDR, "10.0.0.6", True, nets) == "10.0.0.6"
    assert client_ip({"cf-connecting-ip": OUT}, "192.168.1.9", True, nets, cloudflare=True) == "192.168.1.9"


def test_cf_connecting_ip_requires_proxy_cloudflare():
    h = {"cf-connecting-ip": "198.51.100.77", "x-forwarded-for": OUT}
    # Behind a non-Cloudflare proxy (cloudflare=False), a visitor-forged CF-Connecting-IP is ignored.
    assert client_ip(h, "172.18.0.5", True) == OUT
    assert client_ip({"cf-connecting-ip": "198.51.100.77"}, "172.18.0.5", True) == "172.18.0.5"
    assert client_ip(h, "172.18.0.5", True, cloudflare=True) == "198.51.100.77"


def test_parsing():
    assert parse_nets("") == () and parse_nets(None) == ()
    assert len(parse_nets("fd00::/8,127.0.0.1")) == 2
    assert not is_trusted_peer("fd00::1", parse_nets("127.0.0.1"))
    with pytest.raises(ValueError, match="ET_TRUSTED_PROXIES"):
        parse_nets("172.18.0.0/16, banana")


def test_bad_setting_stops_the_start(tmp_path):
    cfg = cfg_for(tmp_path, trust_proxy=True)
    cfg.trusted_proxies = "nope"
    with pytest.raises(ValueError, match="nope"):
        create_app(cfg)
