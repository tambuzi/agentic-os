import pytest

from aos.errors import AosError
from aos.store import inside, locked, read_text, write_atomic


def test_read_missing_returns_empty(tmp_path):
    assert read_text(tmp_path / "nope.md") == ""


def test_write_atomic_creates_parents_and_leaves_no_temp(tmp_path):
    p = tmp_path / "a" / "b.md"
    write_atomic(p, "hi")
    assert p.read_text() == "hi"
    assert [x.name for x in p.parent.iterdir()] == ["b.md"]


def test_write_atomic_bytes(tmp_path):
    p = tmp_path / "x.bin"
    write_atomic(p, b"\x00\x01")
    assert p.read_bytes() == b"\x00\x01"


def test_locked_allows_sequential_use(tmp_path):
    p = tmp_path / "m.md"
    with locked(p):
        write_atomic(p, "1")
    with locked(p):
        assert read_text(p) == "1"


def test_inside_rejects_escape(tmp_path):
    assert inside(tmp_path, "a/b.md") == (tmp_path / "a" / "b.md").resolve()
    with pytest.raises(AosError):
        inside(tmp_path, "../etc/passwd")


def test_write_atomic_preserves_and_defaults_mode(tmp_path):
    import os
    umask = os.umask(0)
    os.umask(umask)
    new = tmp_path / "new.txt"
    write_atomic(new, "x")
    assert new.stat().st_mode & 0o777 == 0o666 & ~umask
    old = tmp_path / "old.sh"
    old.write_text("a")
    old.chmod(0o755)
    write_atomic(old, "b")
    assert old.stat().st_mode & 0o777 == 0o755
    write_atomic(new, "y", mode=0o700)
    assert new.stat().st_mode & 0o777 == 0o700
