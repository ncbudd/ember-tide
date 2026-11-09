"""Tiny test for publish_check: `python -m pytest scripts/test_publish_check.py`."""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import publish_check as pc


def test_secret_patterns_and_denylist():
    deny = [re.compile(r"\bzed\b", re.I)]
    assert pc.scan_text("f", "-----BEGIN PRIVATE KEY-----", [])
    assert pc.scan_text("f", "tok = et_" + "a" * 30, [])
    assert pc.scan_text("f", "mqtt://bob:hunter22@host:1883", [])
    assert pc.scan_text("f", 'x = "AIza' + "B" * 35 + '"', [])
    assert pc.scan_text("f", "walked Zed today", deny)
    assert not pc.scan_text("f", "mqtt://user:pw@host.example.com and password=\"test\"", deny)
    assert not pc.scan_text("f", "nothing to see", deny)


def test_repo_scan_end_to_end(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()

    def g(*a):
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)

    g("init", "-q")
    (repo / "a.txt").write_text("hello zed\n")
    g("add", "a.txt")
    g("-c", "user.name=t", "-c", "user.email=t@e.x", "commit", "-qm", "walk zed")
    dl = tmp_path / "deny.txt"
    dl.write_text("# comment\n\\bzed\\b\n")
    os.environ["PUBLISH_DENYLIST"] = str(dl)
    try:
        assert pc.main(["--root", str(repo), "--history"]) == 1
        assert len(pc.scan_history(repo, pc.load_denylist(dl))) == 1
    finally:
        del os.environ["PUBLISH_DENYLIST"]
