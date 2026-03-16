# Forge Plan

Adversarial dual-architecture planning system. A gatekeeper verifies spec completeness,
a router determines pipeline complexity, two independent architects design solutions,
adversarial critics evaluate them, refiners improve them, a judge synthesizes the final plan,
and an approver verifies the plan against the original requirements.

## Usage

```bash
forge plan drive --problem "your problem statement"
```

Options:
- `--preset`: Preset name for enrichment plugins and conventions
- `--lite`: Force lite pipeline mode (single architect/critic/refiner, skip judge)
- `--auto-approve`: Skip assumption gate — proceed without human review of spec gaps
- `--force`: Override plan approval gate
- `--clean`: Start a fresh run ignoring prior session data
- `--resume`: Resume the most recent session
- `--session`: Resume a specific session directory

The planner runs 8 phases: gatekeeper, router, architects, critics, refiners, judge, enrichment, approval.
Output: `final-plan.md` in the session directory.
