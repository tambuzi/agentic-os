# Spike (throwaway): can Kiro workflows drive agenticOS?

**Question.** Can a Kiro workflow be the front-end for the aos board?
- Can it start the board through aos tools?
- Can it follow each task with a zero-token `watch` node?
- Can it relay blockers and contract proposals back to you?

The aos board would still do the execution, with worktrees per project and Claude or Kiro workers.

Nothing here is product code except `aos board watch`. The answers decide whether we build `aos feature workflow <slug>`, which would generate a recipe per feature.

## Setup (on the machine with Kiro)

```bash
cd ~/agenticOS && git fetch && git checkout spike/kiro-workflows
~/.agenticos/venv/bin/pip install -e .          # or: python3 -m venv ~/.agenticos/venv first
aos init ~/agenticOS
aos link <some-git-project> --name spike --targets kiro    # gives it .kiro/settings/mcp.json with aos
mkdir -p <some-git-project>/.kiro/workflows <some-git-project>/.kiro/agents
cp spikes/kiro-workflows/aos-spike.yaml       <some-git-project>/.kiro/workflows/
cp spikes/kiro-workflows/aos-spike-agent.json <some-git-project>/.kiro/agents/
echo '{"cursor": null}' | aos board watch --demo 1        # sanity check: prints one JSON line
```

## Run

- **Kiro IDE:** open `<some-git-project>`. Turn on **Workflows** in Workspace Configuration, then ask in chat: *"run the aos-spike workflow"*.
- **Kiro CLI:** run `kiro-cli` in `<some-git-project>`, then `/workflow run aos-spike`.

## What to send back

| # | Check | Where to look |
|---|---|---|
| 1 | Do `watch-a` and `watch-b` run `aos board watch` and end with `terminal-state` (polls 3 and 6)? If not, the exact error (e.g. `aos: command not found` means the watch can't see your PATH). | Workflows panel → watches |
| 2a | Did `builtin-calls-aos` list `spike`, or say "NO AOS TOOLS"? | step output |
| 2b | Did `custom-calls-aos` run at all? Or did Kiro reject `aos-spike-agent` (e.g. "not a workflow-capable agent")? | step output / error |
| 3 | Did the two watches run side by side in the panel, and stay at zero model turns while waiting? | Workflows panel |
| 4 | Line 4 of the report: are `worktree_path` / `run_id` set by Kiro? | `report` step output |

**Optional:** watch a real board task instead.
1. Create a feature with one task: `aos feature new spike-f --title Spike`, then `aos task add spike-f spike "Say hello in HELLO.md"`.
2. Start the dispatcher: `aos board run --feature spike-f --until-done`.
3. In the recipe, replace `--demo 3` with `--task 1`.
4. Check that the watch shows progress events and ends when the task is done.
