# Jest Error Patterns Reference

Reference companion to `debug-jest/SKILL.md`. Quick-reference for classifying Jest test failures and identifying root causes.

## Failure Classification

### Assertion Failures

- **Expected value wrong** -- logic bug or incorrect test assumption
- **Element not found** -- RTL query wrong or component did not render
- **Timing** -- async state not resolved before assertion
- **Mock return value** -- mock does not match production shape

### Mock Failures

- **Mock not called** -- code path not executed
- **Wrong arguments** -- test passing incorrect data
- **Mock implementation broken** -- returns wrong type or shape
- **Module mock not hoisted** -- import happens before `jest.mock()`

### Component Rendering Failures

- **Missing provider** -- Context, QueryClient, Router required
- **Props wrong** -- required prop missing or wrong type
- **Hook error** -- useEffect/useState breaking in test environment
- **Dependency injection** -- component expects global that does not exist in test

### Async Failures

- **Act warning** -- state update outside `act()`
- **Timeout** -- `waitFor()` exceeded, async operation never resolved
- **Race condition** -- multiple state updates, test checks too early
- **Promise not awaited** -- test finishes before async completes

## Common Root Causes

### RTL query selection

- `getBy*` throws immediately if not found (use for sync elements)
- `findBy*` awaits appearance (use for async-rendered elements)
- `queryBy*` returns null if not found (use for negative assertions)
- `getAllBy*` returns array (use when multiple elements match)

### Mock not working

- Module path wrong in `jest.mock()` call
- Mock not hoisted (must be at module top level, before imports)
- Mock implementation returns wrong type or shape
- Spy not applied before the code under test executes

### Provider missing

- Component uses `useQuery` -- needs `QueryClientProvider`
- Component uses `useNavigate` -- needs `MemoryRouter`
- Component uses context -- needs the appropriate provider wrapper
- Component uses theme -- needs `ThemeProvider`

### Async timing

- Not awaiting `findBy*` queries
- Not wrapping assertions in `waitFor()`
- Using `getBy*` on async-rendered elements
- Test finishes before state updates complete

### Coverage gaps

- Code path never executed in tests -- 0% coverage
- Branch not tested -- only one path of if/else covered
- Error handling not tested -- catch blocks never triggered
