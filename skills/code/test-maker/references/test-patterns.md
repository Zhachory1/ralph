# Test Patterns Reference

## The Three Pillars

| Pillar         | Question                               | Maximize By                                                 |
| -------------- | -------------------------------------- | ----------------------------------------------------------- |
| **Fidelity**   | Does the test find real bugs?          | Cover all paths, real implementations, relevant assertions  |
| **Resilience** | Does the test fail only for real bugs? | Test public APIs, not internals. Fakes over mocks.          |
| **Precision**  | When it fails, do I know why?          | Small, focused, descriptive names, single behavior per test |

## Fidelity Hierarchy

| Preference  | Type                | Fidelity | When to Use                          |
| ----------- | ------------------- | -------- | ------------------------------------ |
| **1. Best** | Real Implementation | Highest  | Default choice. Most confidence.     |
| **2. Good** | Fake Implementation | High     | When real is slow/non-deterministic. |
| **3. Last** | Mock                | Reduced  | Only when real/fake not feasible.    |

## Test Double Definitions

- **Stub**: No logic, returns what you tell it. Used for state testing.
- **Mock**: Has expectations about how it is called. Used for interaction testing.
- **Fake**: Lightweight implementation of the real thing (e.g., in-memory DB).

## Naming Conventions

### Python

```python
def test_<function>_<scenario>_<expected_result>():
    # Example:
    def test_calculate_bid_returns_zero_when_budget_exhausted():
```

### Go

```go
func Test<Function>_<Scenario>_<ExpectedResult>(t *testing.T) {
    // Example:
    func TestCalculateBid_ReturnsZero_WhenBudgetExhausted(t *testing.T) {
```

### TypeScript

```typescript
describe("<Component>", () => {
  it("should <expected behavior> when <scenario>", () => {});
});
```

## Test Categories

Every public function should have tests in these categories:

1. **Happy path** -- Main success scenario
2. **Error/failure path** -- At least one per public method
3. **Boundary conditions** -- Min/max values, empty collections, zero
4. **Null/empty inputs** -- nil, None, undefined, empty string, empty list

## Structure: Setup -> Act -> Assert

```python
def test_fetch_user_raises_not_found_when_user_missing():
    # Setup
    mock_db = Mock()
    mock_db.get_user.return_value = None

    # Act & Assert
    with pytest.raises(UserNotFoundError):
        fetch_user(mock_db, user_id="nonexistent")
```

## Pyramid Targets

| Type            | Target | Scope                                 |
| --------------- | ------ | ------------------------------------- |
| **Unit**        | 70%    | Single function/class, fast, isolated |
| **Integration** | 20%    | Service boundaries, DB, API contracts |
| **E2E**         | 10%    | Full user flows, real infrastructure  |

## Anti-Patterns

| Anti-Pattern                | Fix                                            |
| --------------------------- | ---------------------------------------------- |
| Logic in tests (loops, ifs) | Move to tested utility, keep tests declarative |
| Mocking types you don't own | Create wrapper, mock the wrapper               |
| Shared mutable state        | Each test sets up its own state                |
| Testing private internals   | Test through public API only                   |
| Non-deterministic tests     | Control randomness, mock time, use fakes       |
| Testing framework code      | Trust the framework, test your logic           |
