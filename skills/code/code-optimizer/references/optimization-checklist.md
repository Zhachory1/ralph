# Optimization Checklist

Systematic review checklist for Phase 3 (Optimize). Work through each
category and document findings.

## Algorithmic Complexity

- [ ] No O(n^2) where O(n) or O(n log n) is possible
- [ ] No repeated calculations inside loops
- [ ] No unnecessary data structure conversions
- [ ] Appropriate data structures for access patterns (map for lookup, array for iteration)
- [ ] No linear search where hash lookup is possible

## Memory Usage

- [ ] No large unnecessary copies (pass by reference/pointer for large structs)
- [ ] All resources closed (files, connections, cursors, channels)
- [ ] No unbounded growth (slices, maps, caches without eviction)
- [ ] Efficient data structures (avoid over-allocation)
- [ ] Pre-allocated slices/arrays when size is known

## I/O Patterns

- [ ] No sequential I/O that could be parallel (fan-out/fan-in)
- [ ] No N+1 queries (batch or join instead)
- [ ] Connection pooling for databases and HTTP clients
- [ ] Appropriate timeouts on all external calls
- [ ] Streaming for large payloads (avoid loading full body into memory)

## Caching Opportunities

- [ ] Repeated expensive computations identified
- [ ] Stable data fetched once and cached (not re-fetched per request)
- [ ] Cache invalidation strategy defined
- [ ] Cache size bounded with eviction policy

## Concurrency

- [ ] Independent operations parallelized where beneficial
- [ ] Async I/O used where available
- [ ] No unnecessary serialization of parallel-safe work
- [ ] Goroutines/threads properly bounded (semaphores, worker pools)
- [ ] No lock contention on hot paths

## Simplicity (Rule of Three)

- [ ] No premature abstractions (abstracted only after 3+ uses)
- [ ] Conditionals simplified (guard clauses, early returns)
- [ ] Dead code removed
- [ ] Redundant error checks consolidated
- [ ] No over-engineering for hypothetical future requirements

## Hot Path Focus (80/20)

- [ ] Critical path identified (request handling, auction logic)
- [ ] Optimizations focused on measurable bottlenecks
- [ ] Non-critical paths left clear over clever
- [ ] Performance targets defined (p95 < 300ms for ad serving)

## Measurement Plan

For each optimization applied, record:

| Location | Issue Found | Solution Applied | Expected Impact | How to Verify |
| -------- | ----------- | ---------------- | --------------- | ------------- |
|          |             |                  |                 |               |
