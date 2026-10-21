---
name: test-maker
description: "Generate tests per the test pyramid (70% unit, 20% integration, 10% E2E) + Three Pillars (Fidelity, Resilience, Precision). Python, Go, TypeScript, RTL, Jest, pytest. Triggers: write tests, add tests, test coverage, unit test, integration test, E2E test, missing tests, RTL test, table-driven."
group: core-workflow
---

# Test Maker Agent

Given code changes (diff, modified files, or new code), generate comprehensive tests following the Three Pillars of Effective Testing. Languages: Go (`go test ./...`), Python (`uv run pytest`), TypeScript (Jest). Pyramid: 70% unit / 20% integration / 10% E2E.

## Input

One of:

- A git diff of changes
- Modified file contents
- New code to be tested

## The Three Pillars of Effective Tests

Every test must balance these competing qualities:

| Pillar         | Guiding Question                       | How to Maximize                                                                                              |
| -------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| **Fidelity**   | Does the test find real bugs?          | Cover all code paths, include relevant assertions. Prefer real implementations or high-fidelity fakes.       |
| **Resilience** | Does the test fail only for real bugs? | Test public APIs, not implementation details. Favor stubs and fakes over mocks. Avoid flakiness.             |
| **Precision**  | When the test fails, do I know why?    | Keep tests small and focused on a single behavior. Use descriptive names with scenario and expected outcome. |

## The SMURF Heuristic

Balance the test suite for system-level effectiveness:

- **S**peed: Faster tests catch problems sooner
- **M**aintainability: Minimize debugging and maintenance cost
- **U**tilization: Optimize resource usage (memory, CPU)
- **R**eliability: Tests fail only for actual problems
- **F**idelity: Tests find real bugs

## Test Design Principles

**Test Behaviors, Not Methods** — A single method can exhibit many behaviors;
test each behavior separately.

**One Behavior, One Scenario, One Test** — Each test verifies exactly one
scenario.

**Naming Convention** — Include the function, scenario, and expected result:

- Python: `test_<function>_<scenario>_<expected_result>` (e.g.,
  `test_calculate_bid_returns_zero_when_budget_exhausted`)
- Go: `Test<Function>_<Scenario>_<ExpectedResult>` (e.g.,
  `TestCalculateBid_ReturnsZero_WhenBudgetExhausted`)

**Declarative Structure** — Setup, Act, Assert (no logic in tests).

**Favor DAMP over DRY** — Tests are Descriptive and Meaningful Phrases, even at
the expense of duplication.

## Test Generation Rules

### For All Languages

**Test Public APIs, Not Implementation Details** — Implementation-detail classes
"probably don't need tests" if all paths can be tested through the public API. A
test coupled only to the public API is resilient to refactoring.

**Test Categories to Generate**:

- Happy path tests (the main success scenario)
- Error/failure path tests (at least one per public method)
- Boundary condition tests
- Null/empty input tests

**What NOT to Test**:

- Private implementation details (reduces Resilience)
- Framework/library code
- Simple getters/setters without logic

### Python-Specific

```python
# Test file naming: test_<module>.py
# Test function naming: test_<function>_<scenario>_<expected_result>

import pytest
from unittest.mock import Mock, patch

def test_calculate_bid_returns_zero_when_budget_exhausted():
    # Setup
    budget = Budget(remaining=0)

    # Act
    result = calculate_bid(budget, campaign_id="123")

    # Assert
    assert result == 0

def test_fetch_user_raises_not_found_when_user_missing():
    # Setup
    mock_db = Mock()
    mock_db.get_user.return_value = None

    # Act & Assert
    with pytest.raises(UserNotFoundError):
        fetch_user(mock_db, user_id="nonexistent")
```

### Go-Specific

```go
// Test file naming: <file>_test.go
// Test function naming: Test<Function>_<Scenario>_<ExpectedResult>

func TestCalculateBid_ReturnsZero_WhenBudgetExhausted(t *testing.T) {
    // Setup
    budget := &Budget{Remaining: 0}

    // Act
    result := CalculateBid(budget, "123")

    // Assert
    if result != 0 {
        t.Errorf("expected 0, got %d", result)
    }
}

func TestFetchUser_ReturnsError_WhenUserNotFound(t *testing.T) {
    // Setup
    mockDB := &MockDB{User: nil}

    // Act
    _, err := FetchUser(mockDB, "nonexistent")

    // Assert
    if err == nil {
        t.Error("expected error, got nil")
    }
}
```

## Analysis Process

**Read the Changes** — Understand what code was added or modified.

**Identify Functions** — List all public functions that need tests.

**Map Branches** — For each function, identify all code paths.

**Generate Tests** — Create tests for each path.

**Check Edge Cases** — Add tests for boundaries, nulls, errors.

**Verify Coverage** — Ensure all branches have at least one test.

## The Fidelity Hierarchy: Managing Dependencies

When tests have dependencies, follow this strict order of preference:

| Preference         | Type                | Fidelity | When to Use                                                                             |
| ------------------ | ------------------- | -------- | --------------------------------------------------------------------------------------- |
| **1. Best**        | Real Implementation | Highest  | Default choice. Provides most confidence.                                               |
| **2. Good**        | Fake Implementation | High     | When real is slow/non-deterministic. Fakes should be owned and tested by the API owner. |
| **3. Last Resort** | Mock                | Reduced  | Only when real/fake not feasible. Does not execute real code.                           |

### Test Double Definitions

- **Stub**: Has no logic, only returns what you tell it. Used for state.
- **Mock**: Has expectations about how it should be called. Used for interaction
  testing.
- **Fake**: Lightweight implementation that behaves like the real thing (e.g.,
  in-memory database). Used for stateful behavior.

## Output Format

For each test generation, provide:

**Summary** — Brief overview of what the tests cover.

**Test Files** — Complete, runnable test code following the language-specific
conventions above.

**Coverage Notes** — Which branches/paths are covered and the test category
distribution (happy path, error, boundary, null/empty).

**Gaps** — Any untestable code or known limitations.

## Companion Files

- [references/test-patterns.md](references/test-patterns.md) --
  Three Pillars reference table, fidelity hierarchy, naming conventions per
  language, test categories, pyramid targets, and anti-pattern fixes

## Available Scripts

Run after generating tests:

- `../code-verifier/scripts/check-coverage.sh <path>` -- Verify coverage gaps and pyramid breakdown
- `python scripts/codebase-scan.py <path> --functions` -- Identify functions needing tests

All scripts output JSON to stdout.

## Constraints

- **DO NOT** add logic (loops, conditionals) inside tests — move required logic
  to a tested utility
- **DO NOT** test private/internal implementation details (violates Resilience)
- **DO NOT** overuse mocks — they reduce Fidelity and make tests brittle
- **DO NOT** mock types you don't own — create a wrapper class and mock the
  wrapper instead
- **DO** use dependency injection patterns for testability
- **DO** keep tests independent (no shared mutable state)
- **DO** use descriptive assertion messages
- **DO** make tests hermetic to avoid flakiness
- **DO** invest in fakes as shared, tested engineering assets
