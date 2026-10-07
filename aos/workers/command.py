"""Generic argv-template worker (tests, other agent CLIs). Never goes through a shell."""

from __future__ import annotations

import os

from ..errors import AosError
from .common import Launch, RunSpec, write_mcp_config

EXIT_REASONS: dict[int, str] = {}


def prepare(spec: RunSpec, settings: dict) -> Launch:
    template = settings.get("command")
    if not isinstance(template, list) or not template:
        raise AosError("command worker needs workers.<profile>.command as a list of arguments")
    values = {"prompt": spec.prompt, "context_file": str(spec.context_file),
              "mcp_config": str(write_mcp_config(spec)), "task_id": str(spec.task["id"]),
              "worktree": str(spec.worktree), "role": spec.role}
    try:
        argv = [str(part).format_map(values) for part in template]
    except (KeyError, IndexError, ValueError) as e:
        raise AosError(f"bad placeholder in worker command: {e}", "use {prompt} {context_file} {mcp_config} {task_id} {worktree} {role}")
    return Launch(argv=argv, cwd=spec.worktree, env=dict(os.environ))
