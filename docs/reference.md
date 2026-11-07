# Forge reference

This page collects the details the [README](../README.md) leaves out: every command and flag, the preset manifest format, evidence rules, models, environment variables, and a map of the source tree. Everything here comes from the code in `src/forge/`; file paths point at the source of truth.

## Commands

`forge` routes to six subcommands (`src/forge/cli.py`).

| Command | What it does |
|---|---|
| `forge plan <subcommand>` | The adversarial planner. Use `forge plan drive` to run it end to end. |
| `forge drive` | Runs a plan through a preset's pipeline, from code to pull request. |
| `forge execute <subcommand>` | Low-level control over a pipeline session, one step at a time. |
| `forge status` | Prints executor and planner sessions as JSON. |
| `forge setup` | Installs `eslint` and `prettier` globally with npm, and runs a preset's setup entries. |
| `forge dashboard` | Starts a local web dashboard for your sessions. |

### `forge plan drive`

Runs every planning phase and writes `final-plan.md` (`src/forge/planner/commands.py`).

| Flag | Meaning |
|---|---|
| `--problem TEXT` | The problem statement. Required unless you pass `--resume`. |
| `--repo PATH` | Repository to plan against. Defaults to the current directory. |
| `--slug NAME` | Session name. Forge asks a fast model for one if you leave it out. |
| `--preset NAME` | Preset that supplies enrichment plugins and planning conventions. |
| `--workers N` | Parallel agents per phase. Default `2`. |
| `--lite` | Forces the single-track pipeline: one architect, one critic, one refiner, no judge. |
| `--auto-approve` | Skips the pause for human review when the gatekeeper finds gaps in the spec. |
| `--force` | Lets a run proceed even if the plan was already approved. |
| `--clean` | Starts fresh and ignores earlier session data. |
| `--resume` | Resumes the most recent planner session. |
| `--session DIR` | With `--resume`, resumes this session directory. |

Other `forge plan` subcommands:

| Subcommand | What it does |
|---|---|
| `approve [--session DIR] [--answers FILE]` | Unblocks a run that the gatekeeper paused. `--answers` appends your markdown answers to `spec-gaps.md`. |
| `kill [--reason TEXT]` | Stops the active planner. |
| `status` | Prints the active session's state as JSON. |
| `sessions` | Lists planner sessions. |
| `complete` | Checks that every phase finished and `final-plan.md` exists. |
| `cleanup [--older-than DAYS]` | Removes old sessions. Default 30 days. |
| `init`, `set-context`, `next`, `dispatch`, `run`, `pass`, `fail`, `skip`, `reset`, `log` | Manual, phase-by-phase control. `phase` is one of `gatekeeper`, `router`, `architects`, `critics`, `refiners`, `judge`, `enrichment`, `approval`. |

### `forge drive`

Runs the pipeline (`src/forge/executor/driver.py`).

| Flag | Meaning |
|---|---|
| `--plan FILE` | The plan to implement. |
| `--plan-dir DIR` | A directory that holds the plan. Forge picks `final-plan.md`, then `plan.md`, then the first `*.md` file. Point it at a planner session directory. |
| `--preset NAME` | Required for a new run. A bundled preset name, a user preset name, or a path to a preset directory. |
| `--pipeline NAME` | Which pipeline in the preset to run. Default `full`. |
| `--packages PKG ...` | Hints for affected packages. Forge also detects them from git after the code step. |
| `--resume` | Resumes the existing session. Steps left running by a crash go back to pending. |
| `--skip STEP ...` | With `--resume`, marks these steps complete without evidence. |
| `--dry-run` | Prints what would run and exits. Doesn't call any model. |
| `--fast` | Uses Haiku for every AI step. Meant for testing pipelines. |
| `--no-worktree` | Allows running outside a git worktree. |

### `forge execute`

Each subcommand acts on the active pipeline session (`src/forge/executor/commands.py`).

| Subcommand | What it does |
|---|---|
| `init PIPELINE --preset NAME [--plan FILE] [--no-worktree] [PACKAGES...]` | Creates a session. |
| `status`, `summary [--json]`, `next`, `sessions` | Inspect state. |
| `execute STEP` | Runs a command step directly, with no AI. |
| `dispatch STEP [run\|fix] [--failed-packages A,B]` | Prints the agent configuration or fix prompt for a step. |
| `run STEP [--skip-deps]`, `pass STEP`, `fail STEP [ERROR]` | Move a step through its states. `pass` checks evidence first. |
| `reset STEP [--no-retry-inc]`, `skip STEP`, `resume-from STEP` | Rewind or override. `skip` marks a step complete with no evidence. |
| `model STEP [run\|fix]` | Prints the model a step uses. |
| `add-packages PKG...`, `verify`, `kill [--reason TEXT]` | Housekeeping. |
| `dev-server allocate\|status` | Allocates or checks a dev server port. |
| `cleanup [--older-than DAYS]` | Removes old sessions. Default 7 days. |

### `forge status`, `forge setup`, `forge dashboard`

| Command | Flags |
|---|---|
| `forge status` | `--active` shows only running sessions. `--limit N` caps the list. Default 20. |
| `forge setup` | `--force` reruns setup. `--preset NAME` also runs that preset's `setup` entries. Setup state lives in `~/.forge/setup.json`. |
| `forge dashboard` | `--port N` (default 8765). `--no-browser` starts the server without opening a browser. |

## Presets

A preset tells Forge how to build, lint, and test your stack, and which steps make up a pipeline. Forge ships two in `src/forge/presets/`:

| Preset | Stack | Lint | Test |
|---|---|---|---|
| `npm-ts` | TypeScript with npm | `npx eslint --fix .` | `npx vitest run` |
| `python-uv` | Python with uv | `uv run ruff check --fix . && uv run ruff format .` | `uv run pytest` |

Both define two pipelines:

- `full`: `code`, then `lint` and `test` in parallel, then `code_review`, `report`, and `create_pr`.
- `lightweight`: `code`, then `lint` and `test`, then `report`. No review and no pull request.

### Where Forge looks for a preset

`resolve_preset_dir()` in `src/forge/executor/engine/pipeline_ops.py` checks, in order:

1. A path, if the name contains `/` or is absolute.
2. `~/.forge/presets/<name>/`.
3. Each directory in `FORGE_PRESETS_PATH`, separated by colons.
4. The bundled presets.

### Layout

```
my-preset/
├── manifest.json         # pipelines, steps, evidence rules, models
├── skills/               # step-specific prompt templates
│   ├── code-review.md
│   └── create-pr.md
├── references/
│   └── planner.md        # optional: conventions handed to the planner
└── bundled-skills/       # optional: skills for planner enrichment
```

### `manifest.json` fields

Top level (`load_preset()` in `src/forge/executor/engine/registry.py`):

| Field | Meaning |
|---|---|
| `preset` | Name. Required. |
| `version`, `description` | Metadata. |
| `base_ref` | Branch to diff against. Default `main`. |
| `models` | Model per role: `code`, `fix`, `code_review`, `create_pr`, `judge`, `visual_test`. |
| `pipelines` | Map of pipeline name to `{ "steps": [...], "dependencies": {...}, "revalidation_targets": [...] }`. Forge rejects unknown steps and dependency cycles. A plain list of steps still works and runs in order. |
| `steps` | Map of step name to step definition (below). |
| `build_command`, `bazel_build_command` | Build command the code step and the stop hook run. The Bazel variant applies in Bazel repos. |
| `eslint_config` | Path to an ESLint config for the lint-on-edit hook. |
| `dev_server`, `visual_test_config` | Settings for steps that need a running app. |
| `setup` | List of `{ name, command, cwd, check, description }`. Forge skips an entry when its `check` file exists. `${PRESET_DIR}` expands to the preset directory. |
| `enrichment` | List of `{ skill, model, output_suffix }` plugins the planner runs after the judge. |

Step definition:

| Field | Meaning |
|---|---|
| `type` | `command` (a shell command), `ai` (an agent), or `inline` (handled by Forge, like `report`). |
| `run_command`, `bazel_run_command` | Command for a `command` step. |
| `error_file`, `fix_hints` | Where failure output goes, and what the fix agent is told. |
| `timeout` | Seconds. Default 600. |
| `parallel`, `per_package` | Scheduling hints. |
| `skill`, `subagent_type`, `two_phase` | Prompt template and agent settings for an `ai` step. `two_phase` splits review from fixing. |
| `judge` | `{ criteria_source, max_retries, model }`. Adds a judge loop. `max_retries` defaults to 3; `model` defaults to the preset's `judge` model, then `opus`. |
| `evidence` | List of evidence rules (below). |
| `allowed_file_patterns` | Files the step may change. |

### Evidence rules

A step only passes when every rule holds (`src/forge/executor/engine/evidence.py`).

| Rule | Checks |
|---|---|
| `file_exists` | A file matching `file_glob` exists in the session directory. Optional `min_file_size` and `max_age_seconds`. |
| `json_schema` | The newest JSON file matching `file_glob` validates against `schema`. |
| `json_field_equals` | Field `field_name` in `file` (or `file_glob`) equals `expected_value`. |
| `command_succeeds` | `command` exits 0. Set `cwd` to `repo_root` to run it from the repository root. |
| `all_predecessors_checkpointed` | Every upstream step has a checkpoint. |

The bundled presets require `code-review-verdict.json` (at least 10 bytes) from `code_review` and `pr-url.txt` (at least 5 bytes) from `create_pr`.

### Revalidation

If a step passes only after a retry, Forge resets the pipeline's `revalidation_targets` that had already passed, so they run again against the changed code (`mark_passed()` in `pipeline_ops.py`).

## Planner phases and models

Phase order, models, and limits come from `src/forge/planner/driver.py`.

| Phase | Agents | Writes | Model | Timeout |
|---|---|---|---|---|
| `gatekeeper` | 1 | `spec-verdict` (`PASS` or `BLOCKED`), `spec-gaps.md`, `complexity-verdict` | opus | 600 s |
| `router` | 1, only if no `complexity-verdict` exists yet | `complexity-verdict` (`FULL` or `LITE`) | haiku | 120 s |
| `architects` | 2: top-down and bottom-up | `design-a.md`, `design-b.md` | opus | 900 s |
| `critics` | 2, one per design | `critique-a.md`, `critique-b.md` | opus | 600 s |
| `refiners` | 2, one per design | `refined-a.md`, `refined-b.md` | sonnet | 600 s |
| `judge` | 1 | `final-plan.md` | opus | 600 s |
| `enrichment` | one per preset plugin | plugin output | sonnet | 600 s |
| `approval` | 1 | `approval-verdict` (`APPROVED` or `FAILED`) | sonnet | 300 s |

In lite mode only the "a" agents run, and `refined-a.md` becomes `final-plan.md` in place of the judge. A failed phase retries up to 2 times, and agents whose output already exists are skipped on retry.

## Executor limits

| Limit | Value | Source |
|---|---|---|
| Pipeline wall clock | 6 hours | `PIPELINE_TIMEOUT_S` in `executor/driver.py` |
| Fix or retry attempts per step | 3 | `MAX_STEP_RETRIES` in `executor/driver.py` |
| Agent killed after no output for | 15 minutes | `_STALL_TIMEOUT_S` in `providers/claude.py` |
| Dev server start failures before giving up | 2 | `MAX_DEV_SERVER_PRECHECK_FAILURES` in `executor/driver.py` |

The pre-PR gate (`src/forge/executor/pre_pr_gate.py`) blocks `create_pr` unless every other step has a checkpoint. `test` and `visual_test` can't pass the gate through a manual skip.

## Claude Code plugin

`skins/claude-code/` is a Claude Code plugin named `forge`. Forge passes it to every agent with `--plugin-dir` when `FORGE_PLUGIN_DIR` points at it, and runs each agent as `forge:<agent-name>`.

- `agents/`: one definition per role, such as `planner-architect`, `planner-critic`, `executor-code`, `executor-fix`, and `executor-judge`.
- `hooks/hooks.json`: after every edit or write, flags writes outside an agent's allowed files, validates judge verdicts, and runs ESLint and Prettier on JavaScript and TypeScript files that code-writing agents edit. When a code-writing agent tries to stop, it runs the build command and blocks the stop if the build fails.
- `skills/plan` and `skills/execute`: skills that describe the `forge plan drive` and `forge drive` workflows to an agent inside Claude Code.

`forge drive` sets `FORGE_PLUGIN_DIR` itself when Forge runs from a source checkout. The wheel doesn't include `skins/`, so set the variable yourself in any other setup.

## Providers

Agents run through a small protocol (`src/forge/providers/protocol.py`):

```python
class Provider(Protocol):
    def run_agent(self, prompt, model, max_turns, cwd, ...) -> AgentResult: ...
    def run_judge(self, prompt, model, ...) -> str: ...
    def format_subagent_type(self, agent_type, namespace="") -> str: ...
```

This release ships one implementation, `ClaudeProvider` (`src/forge/providers/claude.py`). It runs `claude -p --dangerously-skip-permissions --output-format stream-json`, streams the transcript to the session directory, and enforces the stall timeout.

`src/forge/providers/models.py` maps model tiers to vendor model names:

| Tier | Purpose | Claude | Codex |
|---|---|---|---|
| `reasoning` | Planning, judging | opus | o3 |
| `balanced` | Implementation | sonnet | gpt-4.1 |
| `fast` | Quick fixes | haiku | gpt-4.1-mini |

## Environment variables

| Variable | Meaning |
|---|---|
| `FORGE_SESSIONS` | Root for session directories. Default `~/.forge/sessions`. |
| `FORGE_PRESETS_PATH` | Extra preset directories, separated by colons. |
| `FORGE_PLUGIN_DIR` | Path to `skins/claude-code`. Passed to each agent as `--plugin-dir`. |
| `FORGE_SKILLS_BASE` | Fallback directory for planner enrichment skills, after `~/.claude/skills/` and the preset's `bundled-skills/`. |
| `FORGE_TICKET_PATTERN` | Regex for pulling a ticket key from the branch name into the session name. Default `[A-Z]+-\d+`. |
| `JIRA_BASE_URL`, `JIRA_TOKEN` | Used by `JiraSessionStore` (`src/forge/integrations/jira_store.py`). Without both, Jira uploads and comments are skipped. |

`forge drive` sets `FORGE_SESSION_DIR`, `FORGE_BUILD_CMD`, and `FORGE_ESLINT_CONFIG` for the hooks. You don't set these.

## Sessions on disk

```
~/.forge/sessions/
├── registry.jsonl                  # lifecycle events for every session; rotates at 512 KB
├── planner/<slug>_<timestamp>/     # .planner-state.json, design and critique files, final-plan.md
└── executor/<name>_<timestamp>/    # agent-state.json, checkpoints/, transcripts, pipeline-activity.log
```

Each session also has an `events.jsonl` timeline (`src/forge/core/events.py`). Executor session names start with a ticket key from the branch name when one matches, and fall back to the branch name.

## Source map

```
src/forge/
├── cli.py              # routes forge <command>
├── setup.py            # forge setup
├── core/               # sessions, locked state files, events, logging, session zips and hashes
├── planner/            # planner CLI, driver, phase state, evidence checks
├── prompts/            # system prompts for planner agents
├── executor/
│   ├── driver.py       # forge drive: the dispatch loop
│   ├── commands.py     # forge execute
│   ├── pre_pr_gate.py  # fail-closed gate before create_pr
│   └── engine/         # preset registry, evidence, judge, checkpoints, step agents
├── providers/          # Provider protocol, ClaudeProvider, model tiers
├── dashboard/          # forge dashboard server and page
├── integrations/       # JiraSessionStore
└── presets/            # npm-ts, python-uv
```

`executor/engine/` also holds `triage.py` (classifies the root cause of a failed run, such as a code, plan, input, or environment fault), `resume_advisor.py`, and `pipeline_summary.py`. The `forge drive` loop in this release doesn't call the triage or resume-advisor modules.
