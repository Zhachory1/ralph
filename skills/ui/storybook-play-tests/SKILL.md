---
name: storybook-play-tests
description: "Generate Storybook play tests: data-testid hierarchy, AAA (Arrange-Act-Assert), within(canvasElement), step fns, userEvent, waitFor, Ant Design select utils. Triggers: play test, storybook test, interaction test, story interaction, component test, antd select test, userEvent, canvasElement."
group: storybook
---

# Storybook Play Tests

Produce reliable, fast interaction tests for React/TypeScript components using strict selector hierarchies and the AAA (Arrange-Act-Assert) pattern. Stack: React, TypeScript, Storybook, TanStack Query, Ant Design.

**Not for:** unit tests (use Jest), E2E tests (use Playwright), non-interactive stories.

## Selector Hierarchy (Strictly Enforced)

**Always prefer data-testid** for cross-framework consistency across Storybook
play tests, Jest, Playwright, and Cypress.

### Mandatory Process

**Step 1 -- Read component source** to find existing `data-testid` attributes.

**Step 2 -- Add missing `data-testid`** to the component BEFORE writing any test.

**Step 3 -- Verify `data-testid` values** by inspecting the rendered component.

### Priority Order

1. **`getByTestId()`** -- REQUIRED first choice; add `data-testid` to component if missing
2. **`getByRole()`** -- Only when the component is third-party or otherwise unmodifiable (no `data-testid` possible)
3. **Text matchers** -- Last resort, restricted to content that changes at runtime (e.g., API-driven strings)

**FORBIDDEN:** `querySelector`, CSS class selectors, string-based selection.

## Story Structure

Every play test follows this structure:

```tsx
export const StoryName: Story = {
  decorators: [createStorybookForm(initialValues)],
  play: async ({ canvasElement, step, args }) => {
    const canvas = within(canvasElement);
    const user = userEvent.setup({
      pointerEventsCheck: PointerEventsCheckLevel.Never,
    });

    await step("Descriptive verification or action", async () => {
      // Scoped AAA logic
    });
  },
};
```

### AAA Pattern Within Steps

- **Arrange**: Query elements, set up preconditions
- **Act**: Perform user interactions (`await user.click()`, `await user.type()`)
- **Assert**: Verify outcomes (`expect()` with `waitFor()` for async)

### Step Naming

Use subject+verb present tense, human-readable descriptions:

- "User clicks Run button"
- "Verify chart displays data"

NOT bare imperatives: "Click button", "Check visible"

## Query Patterns

### Data-testid (Primary)

```tsx
const button = canvas.getByTestId("component__action-button");
const input = canvas.getByTestId("search-input");
```

### Role (Fallback)

```tsx
const button = canvas.getByRole("button", { name: "Save" });
```

### Existence Checking

- `getByTestId()`: Must exist (throws if missing)
- `queryByTestId()`: May not exist (returns null)
- `findByTestId()`: Async (includes waitFor)

### Dynamic Content

```tsx
const getName = (content) => content.includes(dynamicValue);
await waitFor(() => canvas.getByText(getName));
```

## Ant Design Select Handling

Use utilities from `test/unit/utils/test-utils`:

- `selectAntOption(testId, optionText, user)` -- Complete open + select workflow
- `openAntSelect(testId, user)` -- Open dropdown only

Never click Ant Select directly. Options render in a portal outside the component tree.

## Play Test Patterns

- **Loading**: Trigger action, wait, verify skeleton/spinner
- **Error**: Trigger action, wait, verify error message
- **Empty**: Trigger action, wait, verify empty state
- **Success**: Trigger action, wait, verify rendered data

## Coverage Requirements

- **Single-interaction components** (buttons, toggles, inputs): Default state + variations + primary interaction
- **Multi-interaction components** (forms, wizards, dashboards): Add multi-step flows, error/edge cases, conditional rendering

## Running Tests

```bash
npx test-storybook --url http://localhost:6006 --grep "ComponentName"  # Specific
npx test-storybook  # Full suite
```

## Common Pitfalls

| Pitfall                | Fix                                                            |
| ---------------------- | -------------------------------------------------------------- |
| `querySelector` usage  | Replace with `canvas.getByTestId()`                            |
| Unscoped queries       | Always `canvas = within(canvasElement)`                        |
| Missing `waitFor`      | Async state changes require `await waitFor(() => expect(...))` |
| String text selection  | Use function matchers for dynamic content                      |
| No step descriptions   | Every `step()` must have a descriptive name                    |
| Testing implementation | Test user-visible behavior, not internal state                 |
| Duplicate test logic   | Each story validates unique behavior or state                  |

## Global State Management

Global config resets via `beforeEach` in `.storybook/preview.ts` before every
story. Stories use config decorators to override specific properties. This
prevents test pollution between stories.

## Context Provider Stability

Decorator context providers require `useState(() => new Instance())` for
referential stability. Direct instantiation breaks the React context contract--
every render creates a new instance, invalidating all subscribers.

Applies to: QueryClient, Redux stores, theme objects, router instances.

## Companion Files

- [references/selector-patterns.md](references/selector-patterns.md) --
  Selector hierarchy, data-testid conventions, query patterns, existence
  checking methods, Ant Design select handling, AAA pattern within steps,
  step naming conventions, and common pitfalls table

## Constraints

- **DO NOT** use `querySelector` or CSS class selectors
- **DO NOT** write tests without reading the component source first
- **DO NOT** skip `within(canvasElement)` scoping
- **DO NOT** test implementation details over user-visible behavior
- **DO** add `data-testid` to components before writing selectors
- **DO** use the AAA pattern within `step()` blocks
- **DO** use `waitFor()` for all async assertions
- **DO** name steps with subject+verb present-tense descriptions
- **DO** use Ant Design select utilities for dropdown interactions

## Output Format

Deliver a complete `.stories.tsx` file (or additions to an existing one) containing:

- All necessary imports
- Story definitions with `play` functions
- Step-by-step interactions following AAA pattern
- Coverage for the component's interactive behaviors (per Coverage Requirements)
- A summary of any `data-testid` attributes added to the component source
