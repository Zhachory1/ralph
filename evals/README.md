# Orchestration tests

These assert that the board behaves the way the design claims: a blocked card is never dispatched, a reviewed card is not a human gate, a completion without evidence is refused, a child waits for its parent.

They run against a **real** Kanban board and the **real** dispatcher, but never spawn a worker and never call a model. `dispatch --dry-run` reports what would have happened, so the tests are free to run and still catch what only appears in a live run.

## Running them

```bash
cd compose
docker compose exec -u hermes hermes python3 /ralph/evals/test_orchestration.py
```

They use a private Kanban database in a temp directory (`HERMES_KANBAN_DB`), deleted after the run. The gateway's dispatcher ticks every listed board, so on a listed board it spawned real workers on these cards mid-test; a private file is never listed. Run as `hermes` so nothing under the Hermes home becomes root-owned.

Expect roughly two minutes: each dispatch probe waits on a real dispatcher tick.

## Kill-switch tests

```bash
python3 evals/test_killswitch.py
```

Runs the real `bootstrap/stop` in drill mode and checks that it changes nothing: credentials untouched, containers still up, every destructive step shown. A drill that quietly changes something is worse than no drill.

## Isolation tests

```bash
python3 evals/test_sandbox_isolation.py
```

Attempts to escape a sandbox and expects to fail. The escape tests skip while the terminal backend is `local`, and fail loudly the moment someone enables the Docker backend before the gap in `docs/spikes/sandbox-isolation.md` is closed.

Written because a configuration test would have passed on the day the sandbox was wide open: the socket proxy was configured exactly as its documentation describes, and the documentation was misleading. Test the escape, not the configuration.

## Scope-check tests

```bash
python3 evals/test_scope_check.py
```

Includes the case the design names: a builder that sneaks in an unrequested change, caught only because the plan came from the card rather than from the pull request. Also checks that a card with no plan fails rather than passing, since a check that waves through anything it cannot measure is not a check.

## Profile-config tests

```bash
python3 evals/test_profile_config.py
```

Checks the installer applies what a repo profile declares, and that the shipped profiles declare the toolsets their jobs need. It used to copy only the model block, so the orchestrator ran a whole feature without the kanban toolset: able to reason about a card graph and unable to create one, with nothing erroring to say so.

## What each group is for

| Group | Claim under test |
| --- | --- |
| `HumanGateTests` | A blocked card is the human gate, and nothing but a person moves it |
| `RespawnGuardTests` | A card holding a pull-request URL needs a handoff, not just an unblock, before it runs again |
| `ReviewGateTests` | `review` is the agents' queue; `--reviewer` is what makes the critique independent |
| `CompletionTests` | Kanban refuses an evidence-free completion, so the handoff contract sits below the prompt layer |
| `DependencyTests` | The card graph orders work, and a blocked parent holds its children |

Most of these exist because something broke. `test_unblock_alone_does_not_lift_the_guard` is the one that started the suite: a design doc revised after human feedback sat in `ready` forever, with a single gateway log line as the only symptom.

Add a test here whenever a live run surprises you. A failure that reached a real worker once will reach one again.
