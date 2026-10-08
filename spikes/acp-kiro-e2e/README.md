# Spike: ACP board workers with real Kiro

Throwaway verification for the ACP plan's Task 9 on a machine with `kiro-cli`. It is not product code.

## Run it

```bash
cd ~/agenticOS && git fetch origin && git checkout feat/acp-workers && git pull
~/.agenticos/venv/bin/pip install -e .
kiro-cli --version                        # logged in, or KIRO_API_KEY set

~/.agenticos/venv/bin/python spikes/acp-kiro-e2e/run.py
```

It takes a few minutes and uses real Kiro credits. Send back the `kiro-e2e-report.md` it writes, or commit it here as `REPORT-<date>.md`.

Options:
- `--keep`: keep the throwaway home for poking around. The script prints the `AOS_HOME` to export.
- `--timeout 45`: give up after 45 minutes (default 30).
- `--fake`: rehearse with the test agent; no Kiro needed.

## What it does

Everything runs in a throwaway `~/aos-kiro-e2e-*` home that is deleted at the end, so `~/.agenticos/data` is never touched.

1. Sets the global tool with `aos worker kiro`. `aos doctor` must report `kiro: acp`.
2. Creates a demo git project with `calc.py`. The project's worker allows only `read`, `write` and `mcp:aos`; its verify command is `python3 test_calc.py`.
3. Adds two tasks and runs `aos board run --until-done`:
   - **Task 1, "Add mul":**
     - At the first permission request (shell command), the script sends `aos task steer 1 "Also give mul a one-line docstring…"`.
     - It then approves every request from the worker and the reviewer with `aos task approve`.
   - **Task 2, "Add div":**
     - At its first permission request (or after 90 s), the script sends `aos task steer 2 "Stop. Use ZeroDivisionError…" --now`.
     - That open request must be answered as cancelled and expired, not left hanging. Kiro is then re-prompted.
4. Checks the results and writes the report: the timeline, the permissions, the costs, the worktree's git log, `calc.py`, and the tails of the worker and dispatcher logs.

## Checks

- Both tasks reach `done` over ACP, with no fallback to the CLI.
- Task 1's steer is recorded as `steer_queued`, and the docstring is in `calc.py`.
- Task 2's `--now` cancels the turn (`steer_delivered`, "turn cancelled") while a permission request is open. That request ends as expired, and `div` raises `ZeroDivisionError`.
- No permission is left pending.
- Costs are recorded in credits.
- The per-run `~/.kiro/agents/aos-acpdemo-*.json` files are removed.

## Open questions this answers

- Does the queued steer reach Kiro once the running tool finishes?
- Does `--now` end the turn cleanly? The trailing `_kiro.dev/metadata` should arrive, and the re-prompt should not be refused.
- Does `_kiro.dev/metadata` ever arrive mid-turn? A timeline with a nudge or an early close would suggest it does.
- Do your global Kiro MCP servers load into the sessions? The worker logs now carry the ACP messages; the report says whether each global server's name appears in them.

## Runs

- `REPORT-2026-10-08.md`: first real run, 16/16. `--now` arrived before any permission request was open, and the logs had no ACP messages yet; both are fixed since.
- `REPORT-2026-10-08-2.md`: rerun on 26c5a7a, 18/18.
  - `--now` arrived while a permission request was open; the request was refused and Kiro was re-prompted.
  - An approved command wasn't asked again in the same attempt.
  - The user's global Kiro MCP server (`sonarqube`) is named in the worker logs, which suggests it loads into worker sessions (not confirmed). Worker sessions aren't isolated from global Kiro MCP servers.
