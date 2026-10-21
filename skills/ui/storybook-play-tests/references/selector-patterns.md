# Storybook Play Test Selector Patterns

## Selector Hierarchy (Strictly Enforced)

1. **`getByTestId()`** -- REQUIRED first choice. Add `data-testid` if missing.
2. **`getByRole()`** -- Only for third-party/unmodifiable components.
3. **Text matchers** -- Last resort, for runtime-dynamic content only.

**FORBIDDEN**: `querySelector`, CSS class selectors, string-based selection.

## data-testid Convention

```
component-name__element-description
```

Examples:

- `dashboard__run-button`
- `search-input`
- `modal__confirm-button`
- `table__row-1`

## Query Patterns

### Primary: data-testid

```tsx
const button = canvas.getByTestId("component__action-button");
const input = canvas.getByTestId("search-input");
```

### Fallback: Role

```tsx
const button = canvas.getByRole("button", { name: "Save" });
const dialog = canvas.getByRole("dialog");
```

### Existence Checking

| Method            | Behavior                  | Use When              |
| ----------------- | ------------------------- | --------------------- |
| `getByTestId()`   | Throws if not found       | Element must exist    |
| `queryByTestId()` | Returns null if not found | Element may not exist |
| `findByTestId()`  | Async, includes waitFor   | Element appears later |

### Dynamic Content

```tsx
const getName = (content: string) => content.includes(dynamicValue);
await waitFor(() => canvas.getByText(getName));
```

## Ant Design Select Handling

```tsx
import { selectAntOption, openAntSelect } from "test/unit/utils/test-utils";

// Complete select workflow
await selectAntOption("my-select-testid", "Option Text", user);

// Open dropdown only
await openAntSelect("my-select-testid", user);
```

Never click Ant Select directly. Options render in a portal outside the component tree.

## AAA Pattern Within Steps

```tsx
await step("User clicks Run button", async () => {
  // Arrange
  const button = canvas.getByTestId("dashboard__run-button");

  // Act
  await user.click(button);

  // Assert
  await waitFor(() => {
    expect(canvas.getByTestId("dashboard__results")).toBeVisible();
  });
});
```

## Step Naming Convention

Use subject+verb present tense, human-readable:

- "User clicks Run button"
- "Verify chart displays data"
- "Form validates required fields"

NOT bare imperatives: "Click button", "Check visible"

## Common Pitfalls

| Pitfall                | Fix                                              |
| ---------------------- | ------------------------------------------------ |
| `querySelector` usage  | Replace with `canvas.getByTestId()`              |
| Unscoped queries       | Always `canvas = within(canvasElement)`          |
| Missing `waitFor`      | Async changes require `await waitFor(() => ...)` |
| String text selection  | Use function matchers for dynamic content        |
| No step descriptions   | Every `step()` must have a descriptive name      |
| Testing implementation | Test user-visible behavior, not internal state   |
| Duplicate test logic   | Each story validates unique behavior or state    |
