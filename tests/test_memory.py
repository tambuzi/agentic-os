import pytest

from aos.errors import AosError
from aos.memory import Memory, parse, render


def mem(tmp_path, cap=200):
    return Memory(tmp_path / "m.md", cap)


def test_add_and_read(tmp_path):
    m = mem(tmp_path)
    m.add("Prices are in EUR.")
    snap = m.add("Deploys on Tuesdays.\nNever Fridays.")
    assert snap["entries"] == ["Prices are in EUR.", "Deploys on Tuesdays.\nNever Fridays."]
    assert snap["used"] == len((tmp_path / "m.md").read_text())
    assert snap["cap"] == 200


def test_parse_roundtrip():
    text = "a\n§\nb\nc\n"
    assert parse(text) == ["a", "b\nc"]
    assert render(parse(text)) == text


def test_add_duplicate_is_noop(tmp_path):
    m = mem(tmp_path)
    m.add("x")
    assert m.add("x")["entries"] == ["x"]


def test_cap_overflow_errors_without_writing(tmp_path):
    m = mem(tmp_path, cap=20)
    m.add("0123456789")
    with pytest.raises(AosError) as e:
        m.add("abcdefghijklmnop")
    assert "consolidate" in e.value.hint
    assert m.entries() == ["0123456789"]


def test_replace_requires_unique_match(tmp_path):
    m = mem(tmp_path)
    m.add("deploy staging")
    m.add("deploy prod")
    with pytest.raises(AosError) as e:
        m.replace("deploy", "x")
    assert "2 entries" in e.value.message
    m.replace("prod", "deploy prod via CI")
    assert m.entries() == ["deploy staging", "deploy prod via CI"]


def test_remove_and_missing(tmp_path):
    m = mem(tmp_path)
    m.add("a1")
    m.remove("a1")
    assert m.entries() == []
    with pytest.raises(AosError):
        m.remove("zzz")


def test_rejects_separator_and_empty(tmp_path):
    m = mem(tmp_path)
    with pytest.raises(AosError):
        m.add("a\n§\nb")
    with pytest.raises(AosError):
        m.add("   ")


def test_section_sign_inline_is_fine(tmp_path):
    m = mem(tmp_path)
    m.add("see § 4.1")
    assert m.entries() == ["see § 4.1"]
