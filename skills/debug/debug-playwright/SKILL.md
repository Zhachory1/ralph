---
name: debug-playwright
description: "Debug Playwright E2E failures: selector not found, locator timeout, navigation timeout, flaky, passes locally fails CI, screenshot mismatch, visual regression, auth state lost, retry exhausted. Triggers: playwright failed, E2E failure, flaky E2E, visual regression."
group: debugging
---

# Debug Playwright

Diagnose Playwright E2E failures through trace analysis, selector refinement, and timing resolution. Approach: capture traces, inspect DOM snapshots, apply targeted fixes.

**E2E Architecture:** Tests in `test/e2e-playwright/` or `src/UI/.../test/e2e-playwright/`; page objects in `pages/`; fixtures in `fixtures/`; config in `playwright.config.ts`.

**Not for:** Jest failures (debug-jest), API debugging without browser (debug-api), build errors (debug-build), writing new tests (test-maker).

### Test Case Override (Critical)

Use localStorage, NOT cookies, to switch mock responses in E2E tests:

- Set: `localStorage.setItem("mp:e2e-test-id", "my-test-case")`
- Clear: `localStorage.removeItem("mp:e2e-test-id")`
- This routes to `<test-case>.json` fixture files for mock scenarios

## Failure Classification

| Category             | Key Signals                                                              |
| -------------------- | ------------------------------------------------------------------------ |
| **Selector/Locator** | Locator not found, element not visible, not attached, multiple matches   |
| **Timeout**          | Navigation timeout, locator timeout, assertion timeout, network idle     |
| **Assertion**        | toBeVisible/toHaveText/toContainText failed, screenshot mismatch         |
| **Flaky**            | Passes locally fails CI, intermittent, order dependency, retry exhausted |
| **Infrastructure**   | Context closed, auth state lost, fixture error, global timeout           |

> **Deep dive**: See `references/playwright-error-patterns.md` for complete patterns and root causes.

## Investigation Strategy

**Run with Trace**
Enable tracing to capture full execution: screenshots, DOM snapshots, network
activity, and console logs at every step. Open the trace viewer and navigate to
the failure point. Inspect DOM state, network requests, and console output.

**Debug in Headed Mode**
Run the failing test in UI mode or headed debug mode to visually observe what
happens in the browser at the point of failure.

**Check Selector Specificity**
Validate locators against the actual DOM. Locator priority:
`getByRole` > `getByTestId` > `getByText` > `getByLabel` > CSS `locator`.
Never use CSS selectors when a semantic locator is available.

**Resolve Timing Issues**
Always await assertions for async conditions. Use soft assertions only when
the test must continue past a failure to gather more diagnostic information.
Never use arbitrary sleeps or hardcoded waits.

**Debug Network Activity**
Capture network activity to identify slow or failing API calls. Wait for specific
API responses before proceeding with assertions.

**Isolate Page Object Failures**
If a POM method fails, run that method in isolation to narrow whether the issue
is in the page object or the test logic.

**Identify Root Cause**
Classify the failure: selector, timing, state, or infrastructure. Apply the fix
to the test or page object.

**Verify Stability**
Re-run the fixed test 3+ consecutive times locally AND in CI (if CI was the
original failure environment) to confirm stability. Run the full suite only if
the change could affect other tests.

## Common Root Causes

Selector issues, element timeouts, flaky tests, and authentication state loss. See `references/playwright-error-patterns.md` for the complete root cause catalog with detailed patterns for each category.

## Constraints

- **DO NOT** use arbitrary sleeps or hardcoded waits to fix timing issues
- **DO NOT** ignore flakiness; intermittent failures indicate real problems
- **DO NOT** modify application code to make tests pass unless the app has a genuine bug
- **DO** use trace artifacts for every debugging session
- **DO** verify test stability with 3+ consecutive passes
- **DO** ensure tests are fully isolated (no shared state, no order dependency)
- **DO** capture artifacts on failure (screenshots, traces, videos)

## Companion Files

- **`references/playwright-error-patterns.md`** -- Complete error pattern classification and root cause catalog

## Output Format

Structured debugging report with these sections:

- **Failure type**: Classification (selector, timeout, assertion, flaky, infrastructure)
- **Root cause**: One sentence explaining why the test failed
- **Trace analysis**: Key observations from the trace viewer (DOM state, network, console)
- **Fix applied**: What was changed and why
- **Verification**: Confirmation the test passes consistently (3+ runs), specifying local and/or CI
- **CI impact**: Whether the fix addresses the CI-specific failure mode
