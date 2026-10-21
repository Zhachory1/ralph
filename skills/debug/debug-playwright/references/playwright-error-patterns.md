# Playwright Error Patterns Reference

Reference companion to `debug-playwright/SKILL.md`. Quick-reference for classifying Playwright E2E test failures and identifying root causes.

## Failure Classification

### Selector/Locator Failures

- **Locator not found** -- element does not exist or selector is wrong
- **Element not visible** -- hidden, opacity 0, or off-screen
- **Element not attached** -- DOM updated, element was removed
- **Multiple elements match** -- selector not specific enough

### Timeout Failures

- **Navigation timeout** -- `page.goto()` exceeded limit
- **Locator timeout** -- element never appeared in DOM
- **Assertion timeout** -- `expect()` condition never became true
- **Network idle timeout** -- requests keep firing, never settle

### Assertion Failures

- **toBeVisible failed** -- element hidden or not rendered
- **toHaveText failed** -- text content does not match
- **toContainText failed** -- partial text match not found
- **Screenshot mismatch** -- visual regression detected

### Flaky Test Issues

- **Passes locally, fails CI** -- timing, viewport, or environment difference
- **Intermittent failure** -- race condition or insufficient wait
- **Test order dependency** -- tests not properly isolated
- **Retry exhausted** -- fails across all retry attempts

### Infrastructure Issues

- **Browser context closed** -- test cleanup error
- **Authentication state lost** -- cookies or storage not persisted
- **Fixture error** -- beforeEach/afterEach crashed
- **Test timeout** -- exceeded global timeout setting

## Common Root Causes

### Selector not found

- Element does not exist in the DOM
- Selector string is incorrect
- Element is inside an iframe (need `frame()` or `frameLocator()`)
- Element is in shadow DOM

### Timeout waiting for element

- Element never rendered (conditional logic, API failure)
- Network request is slow
- Animation delay prevents visibility
- Wrong load state waited for

### Flaky test

- Race condition between test and application state
- Viewport size difference between local and CI
- Test depends on execution order of other tests
- CI environment has different timing characteristics

### Authentication state lost

- Cookies not persisted between test contexts
- Session expired during test run
- localStorage cleared between tests
- Browser context closed prematurely
