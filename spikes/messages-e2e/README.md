# Spike: worker-to-worker messages with a real agent tool

This is a throwaway end-to-end check of worker messages (spec `docs/superpowers/specs/2026-10-30-worker-messages-design.md`), not product code.

```bash
~/.agenticos/venv/bin/python spikes/messages-e2e/run.py --tool claude   # or --tool kiro
```

The script runs two tasks of one feature in a throwaway `AOS_HOME`:
- **api:** renames the order field `total` to `total_cents` and broadcasts it;
- **web:** formats the order total, and asks the api task which field holds the amount.

It checks five things:
- api broadcast the rename;
- the broadcast reached web (live or in web's starting context);
- web asked a question, and it was answered;
- both tasks finished;
- `client.py` uses `total_cents`.

## Runs

`REPORT-2026-10-30-claude.md`: all 8 checks passed, but the checks were looser then than in the current runner, and the two tasks did not run at the same time:
- **One after the other:** the machine had 2.8 GB available. The memory gate (2 GB kept free, 1 GB per worker) started one worker at a time, so web got the broadcast in its starting context, not live.
- **Automatic reply:** web's question went to a task already waiting for review, so it got the automatic reply. That reply was delivered live (`message_delivered`).
- **Result:** `client.py` used `total_cents`, as confirmed by the broadcast and the reply.

The runner was then tightened: it now reports how the broadcast reached web, and whether the tasks ran at the same time. A machine with more free memory runs both tasks at once and exercises live delivery of the broadcast.
