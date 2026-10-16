# Orchestration tests

These assert that the board behaves the way the design claims: a blocked card is never dispatched, a reviewed card is not a human gate, a completion without evidence is refused, a child waits for its parent.

They run against a **real** Kanban board and the **real** dispatcher, but never spawn a worker and never call a model. `dispatch --dry-run` reports what would have happened, so the tests are free to run and still catch what only appears in a live run.

## Running them

```bash
cd compose
docker compose exec hermes python3 /ralph/evals/test_orchestration.py
```

They use a throwaway `ralph-evals` board, created on first run, and archive every card they create. Set `RALPH_EVAL_BOARD` to use a different one. Never point them at a board carrying real work.

Expect roughly two minutes: each dispatch probe waits on a real dispatcher tick.

## Kill-switch tests

```bash
python3 evals/test_killswitch.py
```

Runs the real `bootstrap/stop` in drill mode and checks that it changes nothing: credentials untouched, containers still up, every destructive step shown. A drill that quietly changes something is worse than no drill.

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
