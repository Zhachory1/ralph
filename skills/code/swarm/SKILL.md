---
name: swarm
description: "Spawn parallel agents for multi-file work across exploration, implementation, audit, fix phases until production-ready. Triggers: swarm, parallel agents, multi-file implementation, complex refactor, parallel execution, big refactor, fan-out task, multi-phase implementation, audit-and-fix loop."
group: orchestration
---

# Perfect Implementation Swarm

Decompose large features, refactors, or implementations into parallel workstreams, coordinate execution across multiple phases, and drive iterative quality audits until production-ready.

**Not for:** simple bug fixes, single-file changes, tasks completable in one focused pass.

## The Swarm Process

### Phase 1: Parallel Exploration

Dispatch independent exploration tasks, one per area of concern:

- **Each explorer** maps a specific area: existing patterns, dependencies,
  constraints, best practices
- **Exploration outputs**: Key files, discovered patterns, potential risks,
  recommended approaches
- **Goal**: Build comprehensive understanding before writing any code

Example exploration tracks:

- Track A: Existing code patterns and conventions in the target area
- Track B: Dependencies, imports, and integration points
- Track C: Test patterns and coverage expectations
- Track D: External best practices and documentation

### Phase 2: Parallel Implementation + Testing

Based on exploration findings, dispatch implementation tasks in parallel:

- **Each implementer** handles an independent module or component
- **TDD approach**: Write failing tests first, then implement until tests pass
- **Follow discovered patterns**: Use conventions found in Phase 1
- **Capture evidence**: For UI work, take screenshots; for APIs, capture responses
- **Document deviations**: Record any decisions that diverge from the plan

### Phase 3: Skeptical Audit

After implementation, dispatch independent audit tasks across these dimensions:

- **Security audit**: Injection risks, auth gaps, data exposure
- **Performance audit**: N+1 queries, missing indexes, unbounded operations
- **UX audit**: Accessibility, error states, loading states, edge cases
- **Test coverage audit**: Missing scenarios, boundary conditions, error paths

Auditor requirements:

- Assume nothing works correctly until proven
- Provide specific `file:line` references for every issue
- Rate each issue: **critical** / **major** / **minor**
- Provide concrete fix suggestions

### Phase 4: Fix and Verify

For each audit finding:

- Dispatch a targeted fix task with the specific issue and suggested resolution
- After each fix, dispatch a separate verification task to confirm the fix works
  and check for regressions

### Phase 5: Convergence Loop

Repeat the audit-fix-verify cycle until:

- All tests pass (build green, test green)
- All critical and major audit findings are resolved
- Minor findings are tracked but do not block convergence
- No regressions introduced by fixes

**Stop condition**: Exit the loop when remaining issues are minor and the risk of
regression from further changes exceeds the value of those changes.

## Coordination Principles

**Parallel by default**: Independent tasks run simultaneously, not sequentially.

**Maintain a task checklist**: Track every task, its status, and blocking
dependencies. Update the checklist after every phase completion.

**Evidence over claims**: No UI claim without a screenshot. No performance claim
without measurements. No test claim without a passing run.

**Verification is mandatory**: Never trust a fix without independent
verification. The fixer and verifier must be separate concerns.

**Diminishing returns awareness**: Exit the convergence loop when fixes are minor
and regression risk exceeds the value of further changes.

## Constraints

- **DO NOT** skip the exploration phase — coding without understanding leads to rework
- **DO NOT** run phases sequentially when they can be parallelized
- **DO NOT** accept fix claims without verification
- **DO NOT** ignore audit findings of any severity — track all, resolve critical and major before convergence
- **DO** maintain a running checklist of all tasks and their status
- **DO** use TDD (failing tests first, then implementation) for all implementation tasks
- **DO** dispatch auditors who are skeptical by default
- **DO** capture evidence (screenshots, test output, measurements) at every phase
- **DO** loop until build and tests are green with all critical/major issues resolved

## Visualization Requirements

Include a task dependency graph (Mermaid) before execution begins showing phases and dependencies. Include convergence loop diagram (`Explore → Implement → Audit → Fix → Verify → [loop or Done]`).

## Output Format

Deliver a phased execution report with these sections:

**Task Dependency Graph**: Mermaid diagram showing all tasks and their
dependencies.

**Exploration Summary**: Key findings per area, patterns discovered, risks identified

**Implementation Log**: What was built, test results, evidence captured

**Audit Findings**: Issues by severity with `file:line` references

**Fix Record**: Each fix applied, verification result, regression check outcome

**Final Status**: Build status, test status, outstanding minor items, production readiness assessment
