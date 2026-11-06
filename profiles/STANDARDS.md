# Engineering standards

Every profile follows these when it writes code, tests, or documents, or reviews them. They condense the owner's engineering practices for code, tests, ownership, and documents. A repository's own rules come first where they are stricter.

## Code

- **Read before write.** Optimize for the reader. Lines: 100 soft, 120 hard; let the repository's formatter decide (Prettier, Ruff, gofmt).
- **Names say intent.** Variables are nouns (`bid_price_micros`), functions are verb plus outcome (`fetch_user_profile`), booleans are questions (`is_active`). No single letters outside `i`/`j` loops.
- **Comments say why, never what.** Required for business-logic anomalies, magic numbers, and non-obvious choices.
- **Small units.** A file you cannot describe in one sentence without "and" is too big. Over 800 lines: refactor candidate. A function over 100 lines: decompose. Guard clauses; happy path last.
- **Inject dependencies.** Never construct DB or API clients inside business logic. Compose rather than inherit. Depend on interfaces. Keep state private.
- **No magic values.** Any literal but `0`, `1`, `-1` is a named constant. Environment-dependent or tunable values are configuration, never hard-coded.
- **Rule of three.** Write it, copy it, then abstract on the third use. DRY applies to business logic; tests stay DAMP.
- **Defend boundaries.** Validate inputs from outside the component, including internal services. Catch specific errors; handle them, or log and re-raise with context. Never swallow one. Interfaces have timeouts, fallbacks, and circuit breakers.
- **ML and money.** Seed every RNG. Never use floating point for currency (micros, cents, or Decimal). Impute nulls explicitly.
- **Delete replaced code.** No commented-out code. Every file starts with a header stating its responsibility, without author or date.
- **PRs under 400 changed lines**, one concern each. The description explains the logic and links the design doc. Update it when the implementation diverges.

## Tests

- **Any PR that changes business logic adds unit tests.** Pyramid: about 70% unit, 20% integration (happy path and one failure path), 10% E2E on critical revenue paths.
- **Three pillars.** Fidelity: it finds real bugs. Resilience: it fails only for real bugs. Precision: a failure points at the cause. Weigh suites by speed, maintainability, utilization, and reliability (SMURF).
- **One behavior per test**, through public APIs, named `test_<scenario>_<expected result>`. Setup, act, assert; no loops or logic; state inputs and outputs literally.
- **Doubles, in order of preference:** real implementation, fake, then mock as a last resort. Never mock a type you do not own; wrap it and mock the wrapper. Keep tests hermetic and deterministic.

## Ownership

- **You own the outcome end to end:** design, build, test, deploy, and monitor. Work is done when its SLOs are green, not when the PR merges.
- **Lifecycle for non-trivial work:** understand (PRD with success metrics), measure (baseline first), brainstorm (design doc with at least two options and trade-offs), implement, release (ramp with monitoring), monitor.
- **Observability is a deliverable.** Define the SLO and the golden signals (latency, traffic, errors, saturation) in Datadog before release.
- **Automate a task the third time you repeat it.** Spend excellence where risk is highest; perfection is the enemy of deployment.
- **Stuck for 30 minutes?** Ask, then write the answer down where the next person will find it.

## Documentation

Two tracks. **Neither is Google Docs or Drive.** Do not create, store, or link new documents there.

- **Why (PRDs, design docs, ADRs, strategy, research, post-mortems):** in this instance's docs repository (`RALPH_DOCS_REPO`), approved by merging its PR. Each document carries front matter (id, status, owner, the card it came from, related PRs) and links to other documents by id, so its state is readable from the repository. To share one, send the link to the merged file or its PR; do not copy it elsewhere.
- **How (as-built):** each service's repository, in `/docs` or its root `README.md`.
  - `README.md`: one-line purpose, owner, and links to the design, the on-call runbook, and the SLO dashboard.
  - `ARCHITECTURE.md`: dependencies in and out, and API contracts.
  - `ONCALL.md`: alerts, debug steps, rollback.
  - `SLO.md`: targets and the dashboard.
  - A feature is not done until these exist.
- **Flow:** strategy and roadmap lead to a PRD, the PRD to a design doc, and the design doc to code. An ADR records each hard-to-reverse decision and links its design doc.
- **Bugs:** fix a trivial bug with a clear commit. A non-trivial bug also adds its symptom and fix to `ONCALL.md`.
