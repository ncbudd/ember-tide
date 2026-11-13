"""web/package.json declares the Node versions docs/install.md asks for."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_web_package_declares_node_engine():
    pkg = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    node = pkg.get("engines", {}).get("node")
    assert node, "web/package.json needs engines.node"
    docs = (ROOT / "docs" / "install.md").read_text(encoding="utf-8")
    major = re.search(r"Node (\d+) or newer", docs).group(1)
    assert node == f">={major}"
