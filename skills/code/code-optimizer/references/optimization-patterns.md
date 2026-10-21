# Optimization Patterns Reference

Reference companion to `code-optimizer/SKILL.md`. Language-specific code
examples for each optimization category (Go, Python, TypeScript).

## Algorithmic Complexity

### Python: Set-based deduplication (O(n^2) → O(n))

```python
# Before: O(n^2) - checking membership in list
def find_duplicates(items):
    duplicates = []
    for item in items:
        if items.count(item) > 1 and item not in duplicates:
            duplicates.append(item)
    return duplicates

# After: O(n) - using set for membership
def find_duplicates(items):
    seen = set()
    duplicates = set()
    for item in items:
        if item in seen:
            duplicates.add(item)
        seen.add(item)
    return list(duplicates)
```

### Key Patterns

- Replace `list.count()` / `in list` with `set` membership — O(1) vs O(n)
- Replace nested loops with hash maps for join-style operations
- Sort + two-pointer instead of brute-force pair finding
- Use `collections.Counter` for frequency counting instead of manual loops

## Memory Optimization

### Python: Generator-based streaming

```python
# Before: Creates full list in memory
def process_large_file(path):
    lines = open(path).readlines()  # Loads entire file
    return [process(line) for line in lines]

# After: Generator-based streaming
def process_large_file(path):
    with open(path) as f:
        for line in f:
            yield process(line)
```

### Go: Pre-allocated slices

```go
// Before: Allocates new slice on each append
func collectIDs(items []Item) []string {
    var ids []string
    for _, item := range items {
        ids = append(ids, item.ID)
    }
    return ids
}

// After: Pre-allocate with known capacity
func collectIDs(items []Item) []string {
    ids := make([]string, 0, len(items))
    for _, item := range items {
        ids = append(ids, item.ID)
    }
    return ids
}
```

### Key Patterns

- Use generators/iterators for large datasets (avoid materializing full lists)
- Pre-allocate slices/arrays when size is known
- Close all resources (files, connections, cursors) — use `with`/`defer`
- Pass large structs by pointer in Go to avoid copies
- Bound caches and maps with eviction policies

## I/O and Network Optimization

### Python: Batch queries (N+1 → 2 queries)

```python
# Before: N+1 queries
def get_campaigns_with_budgets(campaign_ids):
    campaigns = []
    for cid in campaign_ids:
        campaign = db.get_campaign(cid)  # N queries
        campaign.budget = db.get_budget(cid)  # N more queries
        campaigns.append(campaign)
    return campaigns

# After: Batched queries
def get_campaigns_with_budgets(campaign_ids):
    campaigns = db.get_campaigns_batch(campaign_ids)  # 1 query
    budgets = db.get_budgets_batch(campaign_ids)  # 1 query
    budget_map = {b.campaign_id: b for b in budgets}
    for campaign in campaigns:
        campaign.budget = budget_map.get(campaign.id)
    return campaigns
```

### Key Patterns

- Batch database queries (N+1 → 1-2 queries)
- Use connection pooling for databases and HTTP clients
- Stream large payloads instead of loading full body into memory
- Add timeouts on all external calls
- Fan-out/fan-in for independent I/O operations

## Caching Opportunities

### Python: TTL-aware caching

```python
# Before: Recomputes on every call
def get_user_score(user_id):
    user = fetch_user(user_id)  # DB call
    history = fetch_history(user_id)  # DB call
    return compute_score(user, history)  # CPU intensive

# After: With TTL-aware caching layer (e.g., cachetools or Redis)
from cachetools import TTLCache, cached

score_cache = TTLCache(maxsize=10000, ttl=300)

@cached(score_cache)
def get_user_score(user_id):
    user = fetch_user(user_id)
    history = fetch_history(user_id)
    return compute_score(user, history)
```

### Key Patterns

- Cache repeated expensive computations with TTL
- Cache stable data fetched multiple times per request
- Always define cache invalidation strategy and eviction policy
- Bound cache size to prevent unbounded memory growth
- Consider cache warming for latency-sensitive paths

## Concurrency Improvements

### Python: asyncio gather

```python
# Before: Sequential API calls
def fetch_all_data(ids):
    results = []
    for id in ids:
        results.append(fetch_from_api(id))
    return results

# After: Concurrent with asyncio
import asyncio

async def fetch_all_data(ids):
    tasks = [fetch_from_api_async(id) for id in ids]
    return await asyncio.gather(*tasks)
```

### Go: Goroutines with WaitGroup

```go
// Before: Sequential
func fetchAllData(ids []string) []Data {
    results := make([]Data, len(ids))
    for i, id := range ids {
        results[i] = fetchFromAPI(id)
    }
    return results
}

// After: Concurrent with goroutines
func fetchAllData(ids []string) []Data {
    results := make([]Data, len(ids))
    var wg sync.WaitGroup
    for i, id := range ids {
        wg.Add(1)
        go func(i int, id string) {
            defer wg.Done()
            results[i] = fetchFromAPI(id)
        }(i, id)
    }
    wg.Wait()
    return results
}
```

### Key Patterns

- Parallelize independent operations (fan-out/fan-in)
- Use async I/O for non-blocking calls
- Bound goroutines/threads with semaphores or worker pools
- Avoid lock contention on hot paths
- Use channels (Go) or queues (Python) for safe communication

## Simplicity Improvements

### Python: Remove premature abstraction

```python
# Before: Over-engineered (premature abstraction)
class BidCalculatorFactory:
    def create_calculator(self, strategy_type):
        if strategy_type == "default":
            return DefaultBidCalculator()
        elif strategy_type == "premium":
            return PremiumBidCalculator()
        else:
            return DefaultBidCalculator()

# After: Simple function (if only one strategy is used)
def calculate_bid(budget, campaign_id):
    if budget.remaining <= 0:
        return 0
    return min(budget.remaining, MAX_BID)
```

### Key Patterns

- Rule of Three: abstract only after 3+ occurrences
- Simplify conditionals with guard clauses and early returns
- Remove dead code — use Git history, don't comment out
- Prefer composition over inheritance
- Delete unused imports, variables, and functions

## Defensive Coding

Patterns for defensive engineering in optimized code:

- **Input Validation**: Never trust inputs from outside the component boundary
- **Error Handling**: Catch specific errors, not generic. Log and re-throw
  with context.
- **Never Swallow Errors**: Silent failures are debugging nightmares
- **Timeouts & Fallbacks**: All external calls need protection
- **Circuit Breakers**: For repeated failures to external services
