# Docstring Patterns Reference

Reference companion to `doc-writer/SKILL.md`. Language-specific documentation
patterns for file headers, function docs, and inline comments.

## File Headers

Every changed or new file needs a docstring explaining what the file/module is
responsible for, how it fits into the larger system, and key dependencies.

### Python

```python
"""
AdSelectionService:
Orchestrates retrieval, filtering, and ranking of ad candidates.
Acts as facade between Auction Engine and ML Inference Service.

Key Dependencies:
- CandidateRetriever: Fetches initial candidate pool
- RankingModel: Scores and orders candidates
- BudgetService: Enforces advertiser budget constraints
"""
```

### Go

```go
// Package adselection orchestrates retrieval, filtering, and ranking of ad candidates.
//
// It acts as a facade between the Auction Engine and ML Inference Service,
// coordinating the flow from candidate retrieval through final ranking.
//
// Key components:
//   - CandidateRetriever: Fetches initial candidate pool
//   - RankingModel: Scores and orders candidates
//   - BudgetService: Enforces advertiser budget constraints
package adselection
```

### TypeScript

```typescript
/**
 * AdSelectionService — Orchestrates retrieval, filtering, and ranking
 * of ad candidates. Acts as facade between Auction Engine and ML
 * Inference Service.
 *
 * @module adselection
 */
```

## Function/Method Documentation

Document public functions with: purpose (one sentence), parameters and their
constraints, return values and possible errors, example usage (for complex
functions).

### Python

```python
def calculate_bid(budget: Budget, campaign_id: str, floor_price: int = 0) -> int:
    """
    Calculate the optimal bid amount for a campaign.

    Uses the campaign's remaining budget and historical performance
    to determine bid price. Returns 0 if budget is exhausted.

    Args:
        budget: Campaign budget with remaining funds (in micros)
        campaign_id: Unique identifier for the campaign
        floor_price: Minimum acceptable bid (default: 0)

    Returns:
        Bid amount in micros, or 0 if cannot bid

    Raises:
        CampaignNotFoundError: If campaign_id doesn't exist
    """
```

### Go

```go
// CalculateBid determines the optimal bid amount for a campaign.
//
// It uses the campaign's remaining budget and historical performance
// to calculate the bid price. Returns 0 if budget is exhausted.
//
// Parameters:
//   - budget: Campaign budget with remaining funds (in micros)
//   - campaignID: Unique identifier for the campaign
//   - floorPrice: Minimum acceptable bid
//
// Returns the bid amount in micros, or 0 if cannot bid.
// Returns CampaignNotFoundError if campaignID doesn't exist.
func CalculateBid(budget *Budget, campaignID string, floorPrice int) (int, error)
```

## Inline Comments

Add comments only for non-obvious logic. Comments explain "why", code
explains "how".

### When to comment

- Business logic anomalies
- Non-obvious algorithmic choices
- Magic numbers with business context
- Workarounds or technical debt

### Examples

```python
# Using 1.2x multiplier for Q4 seasonality drift (see ticket #402)
adjusted_bid = base_bid * 1.2

# Retry limit of 3 matches SLA agreement with upstream provider
MAX_RETRIES = 3

# TODO(JIRA-1234): Remove after feature flag rollout complete
if legacy_mode:
    return legacy_calculate_bid(budget)
```

## README Update Pattern

When changes affect a component's API or behavior:

````markdown
## Campaign Bidding

### New: Budget-Aware Bidding (v2.3.0)

The bidding system now considers remaining budget when calculating bids.
Campaigns with exhausted budgets return 0 instead of minimum bid.

### Usage

```python
from bidding import calculate_bid

bid = calculate_bid(budget, campaign_id="123")
```

### Configuration

| Env Var              | Default | Description           |
| -------------------- | ------- | --------------------- |
| `BID_FLOOR_MICROS`   | 1000    | Minimum bid amount    |
| `BUDGET_RESERVE_PCT` | 0.1     | Reserve 10% of budget |
````

## ONCALL.md Update Pattern

When adding new functionality that could fail, update the runbook:

````markdown
## Alert: BudgetCheckTimeout

**Symptom**: `budget_check_timeout` metric spikes, latency increases

**Check 1**: Look at [Budget Service Dashboard](link)

- Is Budget Service healthy?
- Are there network issues?

**Check 2**: Run this query in Datadog:

```
service:bidding @error_type:budget_timeout | timeseries
```

**Fix**:

- If Budget Service is down: Enable circuit breaker via feature flag
  `BUDGET_CHECK_BYPASS`
- If network issue: Check AWS health dashboard
- Rollback: Revert to previous version if needed
````
