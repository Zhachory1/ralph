---
name: debug-jest
description: "Debug Jest unit test failures: assertion errors, expected/received, toBe/toEqual, RTL element not found, mock not called, missing provider, act warning, waitFor timeout, async failures, coverage gaps. Triggers: jest failed, test failure, mock not called, RTL, expect failed."
group: debugging
---

# Debug Jest

Systematically isolate assertion, mock, and component rendering issues in Jest tests. Approach: read the error message precisely, classify the failure, apply targeted fixes.

**Test Architecture:** Tests in `test/unit/` mirror `src/` structure; React Testing Library for components; MSW for API mocks; Jest for assertions and mocks.

**Not for:** Playwright/E2E failures (debug-playwright), build errors (debug-build), API debugging (debug-api), test strategy (test-maker).

## Failure Classification

| Category      | Key Signals                                                           |
| ------------- | --------------------------------------------------------------------- |
| **Assertion** | `Expected X, received Y`, element not found, timing, mock shape wrong |
| **Mock**      | Mock not called, wrong arguments, implementation broken, not hoisted  |
| **Rendering** | Missing provider, wrong props, hook error, missing global             |
| **Async**     | Act warning, `waitFor()` timeout, race condition, promise not awaited |

> **Deep dive**: See `references/jest-error-patterns.md` for complete patterns and root causes.

## Debugging Workflow

**Run the failing test in isolation**
Execute only the single failing test file and test name. Never run the entire test suite when debugging a single failure.

**Read and classify the error message**
Jest errors are precise. Classify immediately:

- `Expected X, received Y` -- assertion mismatch
- `Unable to find element` -- query selector wrong or element not rendered
- `Act warning` -- async state update not wrapped
- `Cannot read property X of undefined` -- mock return value wrong

**Isolate and inspect the component**
Render the component in isolation. Use `debug()` from the render result to print the DOM tree. Compare actual DOM output against expectations to identify what is missing.

**Validate mocks**
Inspect mock call history:

- Confirm the mock was called
- Confirm it received the expected arguments
- Confirm its return value matches the production shape

**Fix async behavior**

- Use `findBy*` queries for elements that appear asynchronously (waits up to 1000ms)
- Wrap assertions in `waitFor()` for async state changes
- Ensure all promises are properly awaited before assertions run

**Add missing providers**
If the component needs context, wrap it with the required providers before re-running.

**Verify stability**
Re-run 3+ times to confirm the fix is stable and not masking flakiness. Fix top-down: the first error often causes cascading failures.

## Common Root Causes

RTL query selection, mock setup, provider wiring, async timing, and coverage gaps. See `references/jest-error-patterns.md` for the complete root cause catalog with detailed patterns for each category.

## Constraints

- **DO NOT** run the entire test suite when debugging a single failure
- **DO NOT** introduce `any` types to silence TypeScript errors in tests
- **DO NOT** add arbitrary `setTimeout` or delays to fix timing issues
- **DO NOT** skip or disable tests to "fix" failures
- **DO** fix top-down (the first error often causes cascading failures)
- **DO** verify mock shapes match production responses
- **DO** confirm test passes consistently (3+ runs) before declaring fixed
- **DO** maintain or improve coverage after the fix

## Companion Files

- **`references/jest-error-patterns.md`** -- Complete error pattern classification and root cause catalog

## Output Format

Structured debugging report:

- **Failure type**: Classification (assertion, mock, rendering, async)
- **Root cause**: One sentence explaining why the test failed
- **Fix applied**: What was changed and why
- **Verification**: Confirmation the test passes consistently (3+ runs)
- **Side effects**: Any changes to coverage or other tests
