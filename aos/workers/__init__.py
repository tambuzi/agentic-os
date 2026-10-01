"""Worker adapters: one per agent tool, all fed by workers.common."""

from ..errors import AosError
from . import claude, command, kiro

ADAPTERS = {"claude": claude, "kiro": kiro, "command": command}


def adapter(name: str):
    if name not in ADAPTERS:
        raise AosError(f"unknown worker adapter {name!r}", "one of: " + ", ".join(ADAPTERS))
    return ADAPTERS[name]
