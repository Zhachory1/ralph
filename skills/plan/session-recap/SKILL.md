---
name: session-recap
description: "Fast context restore after breaks/idle. Scans recent git, open files, conversation, session state for 3-line summary: completed work, current state, next step. Triggers: session recap, where was I, restore context, catch me up, resume work, recap, pick up where I left off, after break."
group: workflow
---

# Session Recap

Provide instant reorientation after breaks: scan session history, identify completed work and artifacts, output a 3-line executive summary.

**Not for:** active conversations (no break occurred), specific new questions (just answer them), sessions with < 3 interactions.

## Core Principles

**Brevity wins.** Three structured lines maximum. No scrolling. No filler.

**Status over history.** Report what is done and what is next, not what was
discussed.

**Action-oriented.** Always end with a clear next step.

## The Process

**Extract Essentials**

Review the session history and identify:

- The last completed action (what shipped, what was built, what was fixed)
- Current progress status (X complete, Y in progress, Z remaining)
- Key artifacts created (files written, commits made, decisions reached)

**Generate Recap**

Produce a three-line executive summary in this format:

```
Shipped: [What got done in 1 sentence]
Status: [X complete, Y in progress]
Next: [Clear action item]
```

**Use strong verbs:** Shipped. Completed. Built. Delivered. Fixed. Resolved.
Ready. Done.

**Avoid weak language:** We discussed. I think. Maybe. Possibly. Might. We
looked at.

**Present Inline**

Output the recap directly in conversation. Never create files for recaps.

## Examples

**After a skill creation session:**

```
Shipped: CI Watcher skill with auto-diagnosis and self-healing loop.
Status: 3/7 skills created, all validated.
Next: Create session-recap skill or move to a different task.
```

**After a code change session:**

```
Shipped: Refactored DashboardMetricsCard with empty state handling.
Status: Tests passing, ready for commit.
Next: Create PR or continue with analytics card.
```

**After an investigation session:**

```
Shipped: Identified 5 performance bottlenecks in chart rendering.
Status: Analysis complete, fix plan documented.
Next: Implement useMemo optimizations in ChartContainer.
```

## Common Mistakes

| Mistake                                  | Fix                                       |
| ---------------------------------------- | ----------------------------------------- |
| Too much detail                          | User needs orientation, not explanation   |
| Passive language ("We looked at...")     | Use active verbs ("Analyzed", "Built")    |
| Missing next step                        | Always include a concrete action          |
| Premature invocation                     | Do not interrupt active conversation flow |
| Recapping discussion instead of outcomes | Focus on artifacts and deliverables       |

## Constraints

- **DO** keep recaps to 3 structured lines maximum
- **DO** focus on outcomes and deliverables, not discussion history
- **DO** always include a clear next action
- **DO** use strong, active verbs
- **DO NOT** create files for recaps (present inline only)
- **DO NOT** interrupt active conversation to provide unsolicited recaps
- **DO NOT** use passive or tentative language
- **DO NOT** recap what was discussed--recap what was accomplished

## Output Format

A three-line inline summary:

```
Shipped: [Completed deliverable in one sentence]
Status: [Progress metrics--what is done, what remains]
Next: [Specific next action item]
```

Readable in under 5 seconds. User immediately knows where things stand and what
to do next.
