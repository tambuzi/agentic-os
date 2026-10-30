# Spike: learning from real Claude Code sessions

Throwaway end-to-end check for session learning (spec `docs/superpowers/specs/2026-10-30-session-learning-design.md`).

```bash
~/.agenticos/venv/bin/python spikes/session-learning-e2e/run.py
```

It uses a throwaway aos home with one linked demo project, and drives two real, headless Claude Code sessions: `claude -p`, then `--resume` with the user's correction. The real `SessionEnd` hook runs `aos lessons capture`, which starts the background learner. Each run costs a few cents with haiku.

Afterwards it deletes the demo project and its transcripts in `~/.claude/projects`.

## Runs

`REPORT-2026-10-30-claude.md`: 6/6.
- **Session 1** ("No, use snake_case for module names") produced *"When creating a new module file in this project → Use snake_case for module names, not PascalCase"* at 0.4. It was not in context.
- **Session 2**, with the same correction, confirmed it to 0.5, and it appeared in `aos context`.
