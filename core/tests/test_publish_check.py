"""scripts/publish_check.py --history: past file contents are scanned too, not just commit messages."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "publish_check.py"


def load():
    spec = importlib.util.spec_from_file_location("publish_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "notes.txt").write_text("hello\nthe zebrafinch lives here\n", encoding="utf-8")
    (r / "keep.txt").write_text("nothing to see\n", encoding="utf-8")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "first")
    (r / "notes.txt").write_text("hello\n", encoding="utf-8")            # edited out again
    git(r, "commit", "-q", "-am", "tidy")
    git(r, "checkout", "-q", "-b", "side")
    (r / "side.txt").write_text("a quokka on a side branch\n", encoding="utf-8")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "side")
    git(r, "checkout", "-q", "main")
    deny = tmp_path / "deny.txt"
    deny.write_text("# made-up words\nzebra\\w+\nquokka\n", encoding="utf-8")
    return r, deny


def test_history_finds_a_word_that_was_removed_from_the_files(repo):
    r, deny = repo
    pc = load()
    words = pc.load_denylist(deny)
    assert pc.scan_tree(r, words) == []                                  # today's files are clean
    hits = pc.scan_history_contents(r, words)
    assert len(hits) == 1 and "notes.txt" in hits[0] and "zebra" in hits[0]
    assert not any("quokka" in h for h in hits)                         # other branches only with --all-refs
    assert any("side.txt" in h for h in pc.scan_history_contents(r, words, all_refs=True))


def test_main_history_flag_reports_past_contents(repo, monkeypatch, capsys):
    r, deny = repo
    pc = load()
    monkeypatch.setenv("PUBLISH_DENYLIST", str(deny))
    assert pc.main(["--root", str(r)]) == 0
    assert pc.main(["--root", str(r), "--history"]) == 1
    assert "notes.txt" in capsys.readouterr().out


def test_no_denylist_means_no_history_content_scan(repo):
    r, _ = repo
    assert load().scan_history_contents(r, []) == []
