#!/usr/bin/env python3
"""Pre-publish checker (standard library only).

Scans the content of every tracked file (`git ls-files`) for
  (a) generic secret patterns: private keys, et_ API tokens, os_live_ keys, Google API keys, JWTs,
      user:password@ in URLs, password=... literals;
  (b) a denylist of private words: one regex per line (blank lines and # comments ignored), read from the
      file named by $PUBLISH_DENYLIST, else ../ember-tide-private/publish-denylist.txt (outside this repo).
With --history it also scans every commit message (subject + body) against the denylist.

Exit status 1 if anything is found; hits print as file:line: reason. Run from anywhere inside the repo.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

SECRET_PATTERNS = [
    ("private key block", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("et_ API token", re.compile(r"\bet_[A-Za-z0-9_-]{24,}")),
    ("os_live_ key", re.compile(r"\bos_live_(?!x+\b)[A-Za-z0-9]{8,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("password in URL", re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@'\"]+:(?!pw\b|pass\b|password\b|secret\b|x\b|\*+|\$|\{|<)[^/\s@'\"]{4,}@[\w.-]+")),
    ("password literal", re.compile(r"""(?i)\bpassword\s*=\s*["'](?!["']|test|pw|secret|x+["']|changeme|\{|<)[^"'\s]{6,}["']""")),
]
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".jar", ".keystore", ".jks", ".woff", ".woff2", ".ttf", ".pdf", ".zip")
SKIP_NAMES = ("package-lock.json", "uv.lock", "publish_check.py", "test_publish_check.py")


def run_git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout


def repo_root(start: Path) -> Path:
    return Path(run_git("rev-parse", "--show-toplevel", cwd=start).strip())


def load_denylist(path: Path | None) -> list[re.Pattern]:
    if path is None or not path.is_file():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s and not s.startswith("#"):
            out.append(re.compile(s, re.IGNORECASE))
    return out


def scan_text(name: str, text: str, deny: list[re.Pattern]) -> list[str]:
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if "publish-check: ok" in line:  # explicit allowance for a reviewed fake value
            continue
        for label, rx in SECRET_PATTERNS:
            if rx.search(line):
                hits.append(f"{name}:{n}: {label}")
        for rx in deny:
            if rx.search(line):
                hits.append(f"{name}:{n}: denylist /{rx.pattern}/")
    return hits


def scan_tree(root: Path, deny: list[re.Pattern]) -> list[str]:
    hits = []
    for rel in run_git("ls-files", "-z", cwd=root).split("\0"):
        if not rel or rel.lower().endswith(SKIP_SUFFIXES) or Path(rel).name in SKIP_NAMES:
            continue
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        if b"\0" in data[:4096]:
            continue
        hits += scan_text(rel, data.decode("utf-8", "replace"), deny)
    return hits


def scan_history(root: Path, deny: list[re.Pattern]) -> list[str]:
    hits = []
    log = run_git("log", "--format=%x00%h%x01%s%n%b", cwd=root)
    for chunk in log.split("\0"):
        if not chunk.strip():
            continue
        h, _, msg = chunk.partition("\x01")
        for rx in deny:
            if rx.search(msg):
                hits.append(f"commit {h}: message matches /{rx.pattern}/: {msg.splitlines()[0][:80] if msg else ''}")
    return hits


def default_denylist(root: Path) -> Path:
    env = os.environ.get("PUBLISH_DENYLIST")
    return Path(env) if env else (root.parent / "ember-tide-private" / "publish-denylist.txt")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--history", action="store_true", help="also scan commit messages against the denylist")
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="a path inside the repo (default: cwd)")
    a = ap.parse_args(argv)
    root = repo_root(a.root)
    dl = default_denylist(root)
    deny = load_denylist(dl)
    if not deny:
        print(f"warning: no private-word denylist at {dl}; only generic secret patterns are checked", file=sys.stderr)
    hits = scan_tree(root, deny)
    if a.history:
        hits += scan_history(root, deny)
    for h in hits:
        print(h)
    print(f"publish_check: {len(hits)} hit(s)", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
