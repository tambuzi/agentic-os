# Spike: how Kiro exposes a finished conversation

Session learning needs the conversation of a session that just ended. Claude Code gives its end-of-session hook a transcript path; Kiro's equivalent is unknown. This throwaway probe finds out. It is plan Task 0 in `docs/superpowers/plans/2026-10-30-session-learning.md`.

On the Kiro machine, in any Kiro project:

```bash
python3 spikes/kiro-session-capture/probe.py install ~/code/some-project
# open Kiro there, chat a little, include the phrase it prints in one message, let the turn end
python3 spikes/kiro-session-capture/probe.py report ~/code/some-project   # writes FINDINGS-<date>.md here
python3 spikes/kiro-session-capture/probe.py remove ~/code/some-project
```

Commit the findings file. Until then, Kiro sessions support only the manual trigger.
