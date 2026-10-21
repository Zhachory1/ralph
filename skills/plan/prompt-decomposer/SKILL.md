---
name: prompt-decomposer
description: "Transform messy multi-part prompts into structured dependency-ordered task lists, no scope added. Reorders steps, clarifies pronouns, resolves ambiguity, preserves user intent. Triggers: decompose prompt, restructure tasks, ambiguous prompt, multi-part request, task ordering, sequence steps."
group: orchestration
---

# Prompt Decomposer

Transform messy, multi-part prompts into clear, ordered, executable task lists. Fixes structure and ordering without adding scope. Preserves exact user intent.

**Decompose when:** 3+ tasks in one sentence, run-on "and" chains, unclear pronouns, missing context, reversed dependencies. **Skip when:** single clear task, steps already ordered.

## Core Principles

**Structural fix only** -- Reorder, clarify, and organize. Never add features,
solutions, or scope the user did not mention.

**Preserve concepts** -- If the user said "make it faster", do not invent "add
caching" or "optimize queries". Clarify what "it" refers to and what "faster"
means in context.

**Dependency-aware ordering** -- Research before implementation. Read before edit.
Test after change.

**Minimal transformation** -- Only decompose when truly needed. Clean prompts pass
through untouched.

## Execution

### Extract Tasks

Pull out distinct actionable items from the messy prompt. Every extracted task
must trace back to something the user explicitly mentioned. Do NOT invent new
tasks.

### Add Minimal Context

Use codebase knowledge only to clarify what the user meant, not to expand scope:

- "fix the chart" becomes "fix the DashboardMetricsCard chart"
- "add error handling" becomes "add error handling to the UserProfileCard component"

Resolve pronouns by identifying the specific component, file, or feature.

### Order by Dependency

Arrange tasks in execution order:

- **Research/investigation** -- understand before changing
- **Prerequisites** -- create before use
- **Implementation** -- build the thing
- **Verification** -- test after build

### Present the Decomposition

Provide a brief summary, then present the ordered task list for the user to
confirm before execution.

## Common Patterns

**Pronoun Clarification**

```
Input:  "make it faster and fix that bug"
Output:
  1. Investigate performance issue in [specific component from context]
  2. Fix rendering bug in [specific component]
```

**Dependency Reordering**

```
Input:  "add tests and refactor the component and fix the types"
Output:
  1. Fix TypeScript types in component
  2. Refactor component structure
  3. Add tests for refactored component
```

**Context Injection**

```
Input:  "add error handling"
Output:
  1. Add error boundaries to UserProfileCard components
  (Used codebase context to clarify "where", not "how")
```

**Ambiguity Flagging**

```
Input:  "optimize everything"
Output: Ask user -- "Which component or feature needs optimization?"
  (Cannot decompose without knowing scope)
```

## Common Pitfalls

| Pitfall                     | Fix                                                                          |
| --------------------------- | ---------------------------------------------------------------------------- |
| Scope creep                 | Never add "and also we should..." when user did not mention it               |
| Implementation prescription | User says "make faster" -- do not add "use useMemo". Clarify the target only |
| Over-decomposition          | Do not break "fix the bug" into 8 micro-steps. Keep it actionable            |
| Missing ambiguity           | Do not decompose "fix it" without knowing what "it" refers to                |

## Constraints

- **DO NOT** add tasks, features, or solutions the user did not mention
- **DO NOT** prescribe implementation approaches (that is the implementer's job)
- **DO NOT** over-decompose simple tasks into excessive micro-steps
- **DO NOT** decompose prompts that are already clear and well-structured
- **DO** preserve the user's exact intent and terminology
- **DO** resolve pronouns using codebase context
- **DO** order tasks by logical dependency
- **DO** flag genuine ambiguity and ask for clarification
- **DO** present a brief summary of what changed before the task list

## Output Format

**Summary line** -- "Restructured your prompt into N tasks ordered by dependency.
No concepts added." (or note what was clarified/what ambiguity remains)

**Ordered task list** with:

- Clear, actionable descriptions
- Specific component/file references where pronouns were resolved
- Dependency ordering: research -> prerequisites -> implementation -> verification

**Ambiguity handling** -- If ambiguity cannot be resolved from context, ask the
user for clarification instead of guessing.

Target response: the user says "yes, exactly" -- not "that's not what I meant."

## Success Criteria

- Every extracted task traces back to the user's original prompt
- No new scope, features, or implementation details added
- Tasks ordered by logical dependency (research -> build -> verify)
- All pronouns and vague references resolved to specific components/files
- Genuinely ambiguous prompts trigger clarification questions, not guesses
- Clean prompts pass through untouched

## Self-Improvement

Append discoveries and pattern refinements below as they emerge from usage.
