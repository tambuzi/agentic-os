# agenticOS

A shared, self-improving brain for **Claude Code** and **Kiro** across all your projects. It is modelled on the learning loop of [hermes-agent](https://github.com/NousResearch/hermes-agent), and code knowledge comes from **graphskill**.

agenticOS is not another agent. Claude Code and Kiro still do the work. agenticOS gives them six things:

| | What it is | Where it lives |
|---|---|---|
| **Identity & rules** | `SOUL.md` (how the agent behaves) and `AGENTS.md` (org-wide rules) | this repo |
| **Memory** | Small, capped facts that are injected into every session: `org`, `project`, `user` | local: `~/.agenticos/data/memory`, `~/.agenticos/user.md` |
| **Knowledge** | Long-form business docs that agents search on demand | local: `~/.agenticos/data/knowledge` |
| **Skills** | Step-by-step procedures that agents load when relevant, and can write themselves | this repo: `skills/` |
| **Lessons** | Scored rules learned from the corrections in board work, confirmed by you, promoted to org memory when they hold across projects | local: `~/.agenticos/data/lessons` |
| **Code graph** | Symbols, callers, dependencies (graphskill) | per project, managed by graphskill |

**Business data never leaves your machine.** Memory, knowledge, lessons, the inbox, the board and the project list live in `~/.agenticos/data`. That folder is outside this repo and outside iCloud-synced folders. Only code, skills, `SOUL.md`, `AGENTS.md` and `aos.yaml` go to GitHub.

## New here? agenticOS for dummies

Someone told you to "use agenticOS". This section explains what that means and gets you started. The rest of this README has the details.

**What it is.** Think of agenticOS as a shared notebook plus a team lead for your AI assistants (Claude Code or Kiro). The notebook holds the team's rules, business facts and how-tos, and every assistant reads it when it starts. The team lead can split a bigger job across several projects, hand the pieces to helper assistants, and check their work. agenticOS does not write code itself: your assistant still does the work, it just knows more and is better organised.

### What you get

- **Your assistant knows the team's rules and business.** It starts every session with them, so you don't repeat yourself.
- **It remembers what it learns.** Facts you tell it are kept for next time, in every linked project.
- **It can deliver one feature across several projects.** It plans with you, then runs helpers in parallel, one per project.
- **It asks before risky things.** Helpers can only use the tools a project allows; anything else waits for your yes or no.
- **It learns from mistakes.** When a helper gets corrected, agenticOS writes a short lesson, and you confirm or reject it.
- **Costs are tracked.** Each helper run records what it cost, and you can set budgets.

Your business data stays on your machine, in `~/.agenticos/data`. Only code and shared how-tos go to GitHub.

### Your first 15 minutes

Full details and options are in [1. Setup](#1-setup-once-per-machine). The short version:

1. Install the `aos` command (Python 3.11 or newer, macOS or Linux):
   ```bash
   git clone https://github.com/tambuzi/agentic-os.git ~/agenticOS
   python3 -m venv ~/.agenticos/venv
   ~/.agenticos/venv/bin/pip install -e ~/agenticOS
   mkdir -p ~/.local/bin && ln -sf ~/.agenticos/venv/bin/aos ~/.local/bin/aos
   ```
   On a Mac, keep the venv out of `~/Desktop` and `~/Documents` (iCloud breaks it).
2. Point it at the repo: `aos init ~/agenticOS`
3. Say which assistant you use: `aos worker claude` or `aos worker kiro`. With Claude, also run `aos acp install claude` (needs Node).
4. Check everything: `aos doctor`. You want `ok` on the main lines. A graphskill warning is fine for now.
5. Connect one project: `aos link ~/code/my-project`
6. Open (or restart) Claude Code or Kiro in that project. If Claude Code asks to approve the `aos` server, say yes.
7. Try it: ask *"What do you know about this project and our team rules?"* Then ask *"Onboard this project into agenticOS"*. It will ask you up to five questions and save the answers.

### Everyday use in plain words

**Ask about the business.** *"What do we know about refunds?"* The assistant searches the team's knowledge docs and memory before it answers. To add a doc, drop a markdown file in `~/.agenticos/data/knowledge/`.

**Teach it something.** *"Remember that invoices go out on the 1st."* Facts about you or this project are saved right away. Facts for the whole team wait in the inbox until someone approves them: `aos inbox list`, then `aos inbox apply <id>` or `aos inbox reject <id>`.

**Deliver a feature across projects.** Say *"deliver a feature"* and describe it. The `aos-feature` skill plans it with you, waits for your OK, starts one helper per project, and only comes back to you when it needs a decision. Each helper works on its own copy of the project, so your checkout is never touched and nothing is pushed.
- Watch progress in another terminal: `aos board`
- A helper asks permission: `aos task approve <request id>` or `aos task deny <request id>` (the number is shown by `aos board`)
- Change a helper's direction while it works: `aos task steer <task id> "use the v2 endpoint"`

**Review what it learned.** At the end of a feature it lists the lessons it learned. Confirm or reject them in the chat, or later with `aos lessons`, then `aos lessons confirm <project>/<id>` or `aos lessons reject <project>/<id>`.

### When something looks wrong

| You see | Run this |
|---|---|
| Something seems broken or not installed | `aos doctor` |
| The assistant doesn't mention agenticOS | restart the tool in the project; `aos context --project .` shows what it should get |
| A feature seems stuck | `aos board` (lists blocked tasks and pending permissions) |
| You want to know what a helper did | `aos task show <id> --log` |
| `No module named 'aos'` | the venv is in an iCloud folder; recreate it at `~/.agenticos/venv` |

More fixes: [Troubleshooting](#troubleshooting).

### Words you'll see

| Word | Plain meaning |
|---|---|
| Memory | Short facts (team, project, you) that the assistant reads at the start of every session. |
| Knowledge | Longer business docs the assistant searches when it needs them. |
| Skill | A written step-by-step procedure the assistant follows for a kind of task. |
| Lesson | A rule learned from a correction, like "when changing a model, add a migration". |
| Inbox | Proposed changes to team-wide memory, waiting for a person to approve. |
| Board | The local list of tasks for a feature, and who is working on what. |
| Worker | A helper assistant (Claude Code or Kiro) that does one board task. |
| Worktree | A separate copy of a project's code where a worker makes its changes. |
| MCP | The standard way assistants get extra tools; agenticOS adds its tools this way. |
| ACP | The protocol aos uses to talk to a running worker, so it can ask you and take guidance live. |

---

## 1. Setup (once per machine)

**Requirements:** macOS or Linux, Python ≥ 3.11, git. Claude Code and/or Kiro.

```bash
# 1. Get the code
git clone https://github.com/tambuzi/agentic-os.git ~/agenticOS
cd ~/agenticOS

# 2. Install the `aos` command in its own venv (outside ~/Desktop and ~/Documents, see note)
python3 -m venv ~/.agenticos/venv
~/.agenticos/venv/bin/pip install -e .
mkdir -p ~/.local/bin && ln -sf ~/.agenticos/venv/bin/aos ~/.local/bin/aos
aos --help                       # if "command not found": add ~/.local/bin to your PATH

# 3. Point aos at this repo (also creates ~/.agenticos/data)
aos init ~/agenticOS --graphskill "graphskill"
#   --graphskill: how to run graphskill, e.g. "/path/to/venv/bin/python -m graphskill"
#   --data-dir:   optional other place for business data (must be outside the repo)
#   --worker:     claude or kiro, the tool that runs board work on this machine (same as `aos worker`)

# 4. Live worker sessions (ACP). Claude needs Node and a small adapter; Kiro needs nothing extra
aos acp install claude

# 5. Check
aos doctor
```

You want `doctor` to show `ok` for `aos on PATH`, `repo`, and `graphskill`, and `claude: acp` / `kiro: acp` for the tool you use (otherwise workers fall back to one-shot CLI runs). graphskill is optional: without it, linking still works and code-graph setup is skipped with a warning.

> **macOS + iCloud:** do not put the venv under `~/Desktop` or `~/Documents`. iCloud marks files there
> `hidden`, and Python 3.13 silently ignores hidden `.pth` files, so the install breaks with
> `No module named 'aos'`. Check with `ls -lO <venv>/lib/python3.*/site-packages/*.pth`.

## 2. Connect a project

```bash
aos link ~/code/shop-api --name shop          # Claude Code + Kiro + graphskill
aos link ~/code/crm --targets kiro            # only one tool
```

Then **restart Claude Code / Kiro in that project.** `aos link` writes:

| Claude Code | Kiro |
|---|---|
| `.mcp.json`: `aos` server (portable, safe to commit) | `.kiro/settings/mcp.json`: `aos` server (+ graphskill) |
| `.claude/settings.local.json`: SessionStart hook | `.kiro/hooks/aos.json`: SessionStart hook |
| `.claude/skills/<name>/` (copies) | `.kiro/skills/<name>/`, `.kiro/steering/aos.md` |

aos records everything it adds in `<project>/.aos/manifest.json` and **never edits files or settings it didn't create**. It also adds a managed block to the project's `.gitignore`. `aos unlink` removes exactly what it added.

**First session in a new project:** ask the agent to *"onboard this project into agenticOS"*. The `aos-onboard-project` skill reads the code graph, asks you up to five questions about what the code can't tell (purpose, dependencies, invariants, environments, gotchas), and saves the answers as project memory.

## 3. Everyday use

You mostly just work as usual. Every session in a linked project automatically starts with:

1. `SOUL.md` and `AGENTS.md`
2. the memory protocol (how the agent should save what it learns)
3. org memory, this project's memory, your user memory
4. a list of the other linked projects

The agent also gets these tools (MCP server `aos`):

| Tool | Use |
|---|---|
| `memory_read / memory_add / memory_replace / memory_remove` | read and curate memory (`org`, `project`, `user`) |
| `knowledge_search / knowledge_read` | search business docs, memory and skills |
| `skill_list / skill_view / skill_manage` | find, read, create and patch skills |
| `project_list / project_context` | read another project's memory |
| `code_tools / code_query` | query **any** linked project's code graph (graphskill), not only the current one |
| graphskill tools (`repo_map`, `search_symbols`, `callers`, …) | navigate code without reading whole files |

Things you can say to the agent:
- *"Remember that invoices are issued on the 1st because finance closes on the last business day."* → memory
- *"Save how we just fixed the deploy as a skill."* → skill
- *"What do we know about refunds?"* → knowledge search
- *"What does the crm project depend on?"* → another project's context

### Commands

```bash
aos inbox list                    # org-memory proposals waiting for approval
aos inbox show <id>
aos inbox apply <id>              # accept → written to org memory
aos inbox reject <id>
aos lessons [project] [--all]     # lessons learned from board corrections
aos lessons confirm|reject <project>/<id>
aos worker [claude|kiro]          # show or set the tool that runs board work on this machine
aos acp install claude            # live worker sessions for Claude (Kiro needs nothing)
aos sync <project>                # re-copy skills/config (also runs at every session start)
aos status                        # uncommitted skill changes + inbox count
aos doctor                        # health check
aos unlink <project>
```

### Settings: `aos.yaml` (in this repo)

```yaml
memory:
  org:     {cap: 4000, mode: inbox}   # inbox = agent changes need `aos inbox apply`
  project: {cap: 2200, mode: direct}  # direct = written immediately
  user:    {cap: 1375, mode: direct}
skills:
  mode: direct                        # or inbox, to review agent-written skills
context:
  max_chars: 16000                    # size limit of the session-start context
learning:
  enabled: true                       # learner runs after board tasks that needed corrections
  max_in_context: 8                   # lessons per project in a session or worker context
  min_confidence: 0.5
workers:
  claude:
    model: sonnet                     # the default when nothing else decides
    learning_model: haiku             # the cheap model for learner runs
    tiers: {trivial: haiku, small: sonnet, standard: sonnet, large: opus}   # auto: a task's size picks one
```

Release notes: [CHANGELOG.md](CHANGELOG.md).

---

## 4. How the knowledge grows

agenticOS keeps two kinds of knowledge, and they grow differently.

### Memory: small facts, always loaded

Memory is deliberately small: it is injected into *every* session, so every character costs tokens. Caps: org 4000, project 2200, user 1375 characters.

It grows when:
1. **You tell the agent something worth keeping.** The agent calls `memory_add`.
2. **The agent learns something durable on its own**, such as a business rule, a decision and its reason, a quirk, or a preference. The memory protocol in every session tells it to save these, and tells it to skip task progress or anything the code already says.
3. **Onboarding a project** with the `aos-onboard-project` skill.

Write rules per scope:
- `project` and `user` changes are written immediately.
- `org` changes are **staged in the inbox**. Nothing becomes org-wide truth until a human runs `aos inbox apply`.

Memory also **shrinks on purpose**. When a scope is full, the write fails and the agent must consolidate: merge entries, replace outdated ones, remove stale ones. Over time memory converges to the facts that matter most, instead of piling up.

### Knowledge docs: long-form, searched on demand

`~/.agenticos/data/knowledge/` is for anything too long for memory: domain model, glossary, pricing rules, ADRs, onboarding notes, pages copied from Notion/Confluence.

It grows when **you add markdown files** there:

```bash
$EDITOR ~/.agenticos/data/knowledge/pricing.md      # one topic per file, "# Title" on line 1
```

They are searchable on the agent's next `knowledge_search`. There is no rebuild step: the index notices new and changed files by itself.

### Skills: procedures that improve with use

When the agent finishes a non-trivial task, finds its way past a dead end, or gets corrected by you, the protocol tells it to save or patch a skill (`skill_manage`). Skills are written into this repo's `skills/`. Review them with `git diff` / `aos status`, then commit and push to share them with the team.

### Lessons: learned from the board's corrections

When a board task finishes after a correction, agenticOS runs a short **learner**. A correction is a failed review or check, a retry, your steer, a permission you denied, or a block.
- The learner is read-only, uses a cheap model (`workers.<tool>.learning_model`, `haiku` on Claude), and has lesson tools only.
- It turns what went wrong into a **lesson**: one trigger and one action, such as "when changing a database model: add a migration in the same commit".
- It records the board events the lesson comes from as evidence, and gives it a confidence score.
- Lessons are files in `~/.agenticos/data/lessons/<project>/` (local only, like all business data).

How lessons are used:
- The best ones go into that project's worker contexts and your sessions, ranked and capped (`learning.max_in_context`, 8). If space runs out, lessons are cut before memory.
- New lessons apply straight away as *unreviewed*, starting at 0.5 (0.6 with strong evidence).
- When the learner sees a lesson hold again, confidence rises by 0.1, up to 0.9. When it's contradicted, confidence drops by 0.2, and below 0.3 the lesson is rejected. Unused lessons fade slowly.
- You decide with `aos lessons`, `aos lessons confirm|reject <project>/<id>`, or `lesson_decide` in the `aos-feature` end-of-feature report. Confirming sets at least 0.8 and the lesson no longer fades; rejecting is final.
- **Across projects:** when the same lesson is confirmed in two or more projects (the learner links them), agenticOS proposes it as one **org memory** entry in the inbox. Accept it with `aos inbox apply` or decline it with `aos inbox reject`.
- `aos lessons learn <task>` asks for a learner run by hand. `learning.enabled: false` turns learning off.

### Today vs. next

| Growth path | Status |
|---|---|
| Agent saves memory and skills during a session (when prompted by the protocol or by you) | ✅ works now |
| Org memory gated by human approval (inbox) | ✅ works now |
| Humans add knowledge docs by dropping markdown in `data/knowledge/` | ✅ works now |
| Agent writes long-form knowledge docs itself | ❌ not yet: agents have no `knowledge_write` tool; they save to memory or a skill instead |
| **Learning loop from the board**: lessons from corrections, ranked into context, promoted to org memory when confirmed in 2+ projects | ✅ works now |
| Learning from interactive sessions (Claude `Stop`, Kiro `agentStop` hooks) | 🔜 next phase of the learning loop |
| Search past sessions; prune unused or duplicate skills | 🔜 sub-project 3 |
| Import from Notion/Confluence into `knowledge/` | 🔜 sub-project 4 |
| Multi-project features with parallel Claude Code / Kiro workers (board) | ✅ works now |

In interactive sessions (outside the board), learning still happens only while the session runs: the agent saves things when the protocol or you prompt it. Saying *"save what you learned"* at the end of a session is a good habit.

---

## 5. Multi-project features (board)

One feature, several projects, several agents in parallel. Each agent works in its own project, knows the shared feature context, and coordinates through a local board.

```bash
# 1. Plan. Interactively: ask the agent to "deliver a feature" (aos-feature skill). In one conversation
#    it plans with you, creates the tasks, starts the workers, waits for them without
#    spending tokens, asks you only when a task is blocked/failed or proposes a contract
#    change, and reports at the end. It never does the tasks itself.
#    Or by hand:
aos feature new checkout-v2 --title "Checkout v2" --projects shop-api,web,billing
$EDITOR ~/.agenticos/data/features/checkout-v2/brief.md      # goal, scope
$EDITOR ~/.agenticos/data/features/checkout-v2/contract.md   # APIs/events shared between projects
aos task add checkout-v2 shop-api "Orders endpoint"
aos task add checkout-v2 web "Checkout UI" --after 1
aos task add checkout-v2 billing "Invoice on order" --after 1 --worker kiro

# 2. Run: workers start in parallel (one per project at a time), each in its own git worktree
aos board run --feature checkout-v2 --until-done   # exits when nothing more can run

# 3. Watch and steer (another terminal)
aos board                                   # status, blockers, stuck tasks, pending proposals
aos task show 2 --log                       # what a worker did
aos feature approve 4 | reject 4            # contract change proposed by a worker
aos task unblock 3 --note "key is in vault" # answer a blocker
aos task retry 2 --resume --note "rename the button"   # feedback on a finished task
```

**What each worker gets.**
- *General:* SOUL, rules, org memory, the feature brief, the current contract, and the latest timeline.
- *Specific:* its project's memory, its task spec, the results of the tasks it depends on, and graphskill.
- *Tools:* `task_show`, `board_read`, `task_comment`, `task_block`, `task_propose_contract`, `task_create`, `task_complete`.

**Where the work lands.** `~/.agenticos/worktrees/<feature>/<project>` on branch `feature/<feature>`. Your own checkouts are never touched, and nothing is pushed. Review the branches, then merge or open PRs yourself.

**Claude Code or Kiro.** Set it once per machine: `aos worker kiro` (or `claude`; `aos worker` shows the current one and where it comes from). Overrides, most specific first:
1. `--worker` on the task;
2. `aos feature new <name> --worker kiro` for every task of a feature;
3. `projects.<slug>.worker.profile` in `~/.agenticos/data/projects.yaml`;
4. your global `aos worker` (in `~/.agenticos/config.yaml`, never in the repo);
5. `board.default_worker` in `aos.yaml`.

`aos board run --worker kiro` switches every task it starts for that run.

**Which model.** Set it by hand, or let the planner pick it per task (an "auto" mode like Kiro's):
- **By hand**, most specific first: `aos task add … --model opus` > `aos feature new … --model haiku` > `worker: {model: …}` for a project in `projects.yaml`.
- **Auto:** when nobody chose one, the task's **size** picks the tool's tier. The `aos-feature` planner sizes every task (`trivial | small | standard | large`), and you can pass `--size` by hand. Claude's default tiers are trivial → `haiku`, small and standard → `sonnet`, large → `opus` (`workers.claude.tiers` in `aos.yaml`). Kiro has no tiers by default, so Kiro's own auto mode decides.
- Otherwise the tool's default (`workers.<tool>.model`).
- `aos task retry <id> --model opus` retries a task on a stronger model, and `--model auto` puts a task back on auto.
- `aos board run --model …` uses that model for every task in this run only; nothing is stored.
- Claude takes `opus`/`sonnet`/`haiku`/`fable` or hyphenated ids like `claude-opus-4-1`. Kiro takes its own names, like the dotted `claude-sonnet-4.5`, or the exact list in `workers.kiro.models` if you set one.
- What happens when a model doesn't fit the task's tool depends on where it came from:
  - typed in a command or an MCP call: refused;
  - from a feature or project setting: skipped, and auto decides;
  - from `board run --model`: skipped for that task, with a note on the timeline;
  - after `--worker` switches the task's tool: a hand-chosen model goes back to auto, with a note.
- `aos board` shows `tool/model/transport` per task, e.g. `claude/opus/acp`. `aos task show` also says where the model came from.

**Live sessions (ACP).** Workers and reviewers run as live sessions over the Agent Client Protocol (`workers.<tool>.transport: acp`, the default). If the Claude adapter or Node is missing, that attempt falls back to the CLI and says so on the timeline. A live session gives:
- **permission requests:** a tool call outside the allow-list doesn't fail the attempt. It waits for you: `aos board` lists it, `aos task approve <id> [--always]` / `aos task deny <id>` answer it (`--always` adds it to the project's `allowed_tools`). Unanswered after `board.approval_timeout_min` (30) → rejected. Dangerous ones (`git push`, `sudo`, edits outside the worktree) are rejected outright. A plain chain (`npm test; echo "exit=$?"`, `a && b`) is allowed when every command in it is; redirects, `$(…)` and `$VAR` still ask. Once you approve a request, the exact same one isn't asked again during that attempt. Each run's log (`aos task show <id> --log`) has the agent's stderr and the ACP messages (`>` sent, `<` received);
- **stall detection:** no activity for `board.stall_min` (10) minutes, with no permission pending, ends the attempt;
- **one nudge:** a worker that ends its turn without `task_complete` / `task_block` gets one reminder in the same session before the attempt counts as failed;
- **mid-turn steering:** `aos task steer <id> "..."` reaches Claude inside its current turn; Kiro gets it after the running tool, or `--now` interrupts the turn and re-prompts;
- **resume:** a retry reloads the previous session when the tool supports it;
- **cost from the stream:** USD for Claude, credits for Kiro.

Kiro workers need `kiro-cli` with `KIRO_API_KEY` (or a login). Allowed tools per project:
```yaml
# ~/.agenticos/data/projects.yaml
projects:
  shop-api:
    worker: {profile: claude, allowed_tools: ["shell:npm test", "shell:npm run lint"]}
```
Workers never get "allow everything": only the listed tools, the project's `verify_command` (each command of it), and file edits inside their worktree.

**In Kiro: drive it from the Workflows panel.** After planning, the agent can call `feature_workflow`, or you run:
```bash
aos feature workflow checkout-v2 --out ~/code/shop-api      # writes .kiro/workflows/aos-checkout-v2.workflow.yaml
```
Then enable **Workflows** in Kiro's Workspace Configuration and run `aos-checkout-v2`:
- It starts the board.
- It shows one live, zero-token watch per task.
- When a task is blocked, fails or proposes a contract change, a step pauses and asks you in Kiro. Your answer is passed on (`task_unblock`, `task_retry`, `proposal_decide`), and the board starts again.
- At the end it summarises the results and where each worktree is.

The board still does the execution, so Claude Code users keep `aos board run`. Limit: about 15 tasks per workflow (Kiro allows 50 nodes).

**Workers talk to each other.** Workers on the same feature can send each other information while they work:
- **heads-up broadcasts** (`task_broadcast`), e.g. "orders API now returns total_cents";
- **direct questions** (`task_message(..., question=true)`), answered later by the other worker with `task_reply`.

How it works:
- **Live delivery:** messages arrive live, the same way your steers do. A worker that hasn't started yet finds them in its context.
- **Finished tasks:** a question to a task that already finished gets an automatic reply with its result. If that isn't enough, the worker escalates the question, and the planner answers it (`message_answer`), asking you if needed.
- **Limits and rules:** at most 5 messages per attempt (`board.messages.max_per_attempt`; replies don't count), and a reply only answers a question, once. No worker ever waits for an answer.
- **Information, not instructions:** messages never override a task's spec or the contract; contract changes still need your approval.
- **Where to see them:** `aos board` lists open questions, and `aos task show <id> --messages` shows a task's thread.

**Before a task counts as done:**
1. The worker's `task_complete` is only a claim.
2. aos checks the task actually added commits and that the project's `verify_command` passes. Set it in `~/.agenticos/data/projects.yaml`, e.g. `worker: {verify_command: "npm ci && npm test"}`. It runs as a shell command line, so every part of a chain runs. Workers are told the command and run it themselves before `task_complete`.
3. A **separate reviewer** (same tool, fresh session, read-only) checks the task's own diff against the spec and the contract.
4. If either step rejects the work, the worktree is reset to where the attempt started, and the findings go to the next attempt.

**How much review a task gets:**
- **Trivial tasks** (`--size trivial`) are accepted once their checks pass, without a reviewer (`board.skip_review_sizes: [trivial]`; set `[]` to always review).
- **Security-sensitive changes** get a reviewer with a security checklist: authentication and authorization, input validation, injection, secrets, path traversal and crypto. Any real security finding fails the review.
  - A change counts as sensitive when the planner marks it (`--security`, `task_create(security=true)`), or when aos detects it with fixed rules. The rules look at file paths (auth, session, token, secret, crypto, permission, `.env`) and at added lines (secrets, SQL built from strings, shell calls, `eval`, unsafe deserialising, crypto, file paths built from input).
  - The timeline says why.
- **Two independent reviewers** for risky tasks are optional: `board.second_review: [large, security]`. Both must pass, and the second one starts fresh, without the first one's verdict.

**Operation types.** The planner can tag each task as a `feature`, `change`, `fix` or `refactor` (`aos task add --op fix`, `task_create(operation="fix")`). The tag sets the worker's first move and what its reviewer checks:
- a `fix` starts by reproducing the bug as a failing test, and fails review without that regression test;
- a `refactor` must not change behaviour, and fails review if tests are deleted, skipped or weakened;
- a `change` updates the existing tests to the new behaviour, and a `feature` adds tests for the new behaviour.

Untagged tasks work as before. `aos task show`, `aos board` (`[fix] title`) and `board_status` show the tag.

**Safety rails:**
- the same failure twice stops a task;
- an interrupted attempt makes the next one inspect the repository first;
- a stale worker can't act on a newer attempt;
- workers never skip the project's git hooks (`--no-verify`, `commit -n`, `core.hooksPath`), and editing an existing lint, format or hook config asks you first;
- starts are staggered and gated by free memory (`board.parallel: auto`).

**Cost:**
- cost is recorded per attempt, in USD (Claude) or credits (Kiro);
- optional `board.task_budget_usd` / `feature_budget_usd` and `task_budget_credits` / `feature_budget_credits` block further attempts once reached;
- `aos task steer <id> "..."` sends guidance to a running worker.

**Data.** The board is one SQLite file, `~/.agenticos/data/board.db` (no server). Feature files, logs and run contexts sit next to it. All of it is local.

## Layout

```
agenticOS repo (GitHub)               ~/.agenticos (local only, never pushed)
  SOUL.md  AGENTS.md  aos.yaml          config.yaml   which repo, linked projects, graphskill cmd
  skills/<name>/SKILL.md                user.md       your personal memory
  aos/   python package                 aos.db        search index (derived, safe to delete)
  tests/ docs/                          venv/         the installed aos
                                        data/
                                          memory/org.md, memory/projects/<slug>.md
                                          knowledge/*.md
                                          lessons/<project>/*.md
                                          inbox/*.yaml
                                          board.db, logs/, runs/   the board (SQLite file)
                                          projects.yaml
                                        worktrees/<feature>/<project>   board work
                                        acp/          Claude ACP adapter (aos acp install)
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No module named 'aos'` | venv is in an iCloud folder; recreate it at `~/.agenticos/venv` |
| Agent doesn't mention agenticOS context | restart the tool in the project; run `aos context --project <path>` to see what it should get |
| `aos` MCP server not available in Claude Code | approve the project's MCP server when prompted; check `aos` is on PATH |
| `conflict, left untouched: ...` from `aos link` | a file or setting with that name already exists and isn't aos's; rename or remove it, then re-link |
| graphskill warning in `aos doctor` | install graphskill, then `aos init <repo> --graphskill "<cmd>"` and re-run `aos link` |
| `aos doctor`: `graphskill <project>: … Connection closed` | graphskill's server crashes on start. A fresh install pulls `mcp` 2.x, where `FastMCP` was removed: `pip install "mcp[cli]<2"` in graphskill's venv (until graphskill pins it) |

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

- Specs and plans: `docs/superpowers/specs/` and `docs/superpowers/plans/`.
- Roadmap: [ROADMAP.md](ROADMAP.md).
- Release notes: [CHANGELOG.md](CHANGELOG.md).
- CI: `.github/workflows/tests.yml` runs the suite on every pull request to `main` (Ubuntu with Python 3.11 and 3.13, plus macOS) and on pushes to `main`.
- Throwaway end-to-end runners for real Claude/Kiro checks: `spikes/`.
