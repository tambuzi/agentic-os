"""Small, curated, always-in-context memory (hermes MEMORY.md style).

A memory file is a list of entries separated by lines containing only `§`.
Writes that would exceed the cap fail loudly so the agent consolidates
instead of silently losing entries.
"""

from __future__ import annotations

from pathlib import Path

from .errors import AosError
from .store import locked, read_text, write_atomic

SEP = "§"


def parse(text: str) -> list[str]:
    entries, cur = [], []
    for line in text.splitlines():
        if line.strip() == SEP:
            if "\n".join(cur).strip():
                entries.append("\n".join(cur).strip())
            cur = []
        else:
            cur.append(line)
    if "\n".join(cur).strip():
        entries.append("\n".join(cur).strip())
    return entries


def render(entries: list[str]) -> str:
    return f"\n{SEP}\n".join(entries) + "\n" if entries else ""


def _clean(text: str) -> str:
    t = (text or "").strip()
    if not t:
        raise AosError("memory entry is empty")
    if any(line.strip() == SEP for line in t.splitlines()):
        raise AosError(f"memory entry may not contain a line with only '{SEP}'")
    return t


class Memory:
    def __init__(self, path: str | Path, cap: int):
        self.path = Path(path)
        self.cap = cap

    def entries(self) -> list[str]:
        return parse(read_text(self.path))

    def snapshot(self) -> dict:
        es = self.entries()
        return {"entries": es, "used": len(render(es)), "cap": self.cap}

    def _save(self, entries: list[str]) -> None:
        size = len(render(entries))
        if size > self.cap:
            raise AosError(
                f"memory full: change would use {size}/{self.cap} chars",
                "consolidate or remove entries (memory_replace / memory_remove), then retry",
            )
        write_atomic(self.path, render(entries))

    @staticmethod
    def _match(entries: list[str], old: str) -> int:
        if not (old or "").strip():
            raise AosError("match text is empty")
        hits = [i for i, e in enumerate(entries) if old in e]
        if not hits:
            raise AosError(f"no entry contains {old!r}", "call memory_read to see current entries")
        if len(hits) > 1:
            candidates = " | ".join(entries[i][:80] for i in hits)
            raise AosError(f"{len(hits)} entries contain {old!r}", f"use a longer unique substring; candidates: {candidates}")
        return hits[0]

    def add(self, text: str) -> dict:
        text = _clean(text)
        with locked(self.path):
            es = self.entries()
            if text not in es:
                self._save(es + [text])
        return self.snapshot()

    def replace(self, old: str, new: str) -> dict:
        new = _clean(new)
        with locked(self.path):
            es = self.entries()
            es[self._match(es, old)] = new
            self._save(es)
        return self.snapshot()

    def remove(self, old: str) -> dict:
        with locked(self.path):
            es = self.entries()
            del es[self._match(es, old)]
            self._save(es)
        return self.snapshot()
