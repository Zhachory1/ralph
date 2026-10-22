# Wiring Tool Tests Into CI

A committed tool that never runs in CI rots: dependencies drift, upstream JSON
shapes change, and the tool silently breaks until someone needs it. Plumb every
committed tool's test suite through CI so drift is caught as it happens.

## Principles

- **Run the tool's own tests in CI.** `go test ./...` / `python -m unittest`
  (or `pytest`) for the tool's package.
- **Non-blocking by default.** A tool's test failure should surface (annotate
  the build) without gating unrelated CI steps. Use a separate, `soft_fail`
  step or a dedicated pipeline lane so tool drift is visible but does not block
  the primary build.
- **Trigger on change.** In a monorepo, run the step only when the tool's paths
  change (path-filtered trigger) to keep CI fast.
- **Fail loudly when the tool is on a critical path.** If a build or deploy
  depends on the tool, promote it from `soft_fail` to a hard gate.

## Buildkite sketch

```yaml
steps:
  - label: ":test_tube: tool tests (non-blocking)"
    command: |
      go test ./tools/cpu-by-region/...
    soft_fail: true # surface drift without blocking other steps
```

For pipeline structure, monorepo path filtering, and standard step conventions,
use your CI provider's own pipeline skill or documentation.

## Checklist item

Add to any tool you commit:

- [ ] The tool's tests run in CI (path-filtered, non-blocking) so drift and
      staleness are caught automatically.
