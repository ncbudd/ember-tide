"""A data folder the app can't write gets a plain message, not a sqlite traceback."""
import os
import sys

import pytest

from embertide.store import Store, cant_write_message


def test_message_names_the_folder_and_the_fix(tmp_path):
    m = cant_write_message(tmp_path)
    assert str(tmp_path) in m and "chown" in m and "named volume" in m


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX permissions and a non-root user")
def test_unwritable_folder_stops_with_the_message(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        with pytest.raises(SystemExit) as e:
            Store(ro / "embertide.db")
        assert "can't write" in str(e.value)
    finally:
        ro.chmod(0o755)


def test_writable_folder_still_works(tmp_path):
    s = Store(tmp_path / "sub" / "embertide.db")
    s.put("k", 1)
    assert s.get("k") == 1
