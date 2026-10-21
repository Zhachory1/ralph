# Optimization Visualization Templates

Mermaid diagram templates for visualizing optimization changes in
code-optimizer reports. Use when optimizations involve data flow changes,
call chains, or architectural restructuring.

## Hot Path Diagram

Show the critical execution path and highlight where bottlenecks occur:

```mermaid
graph LR
    A[Request] --> B[Parse]
    B --> C["Fetch Budget ⚠️ N+1"]
    C --> D[Calculate Bid]
    D --> E[Response]
    style C fill:#ff6b6b,color:#fff
```

Use `⚠️` annotations to mark bottleneck nodes. Style bottleneck nodes with
`fill:#ff6b6b,color:#fff` (red background).

## Before/After Flow

For structural optimizations, show the change side-by-side:

```mermaid
graph LR
    subgraph "Before: Sequential"
        S1[Fetch A] --> S2[Fetch B] --> S3[Fetch C]
    end
    subgraph "After: Parallel"
        P1[Fetch A] & P2[Fetch B] & P3[Fetch C] --> Merge[Merge]
    end
```

## When to Include Diagrams

- **Include**: Data flow changes, call chain restructuring, caching layer
  additions, concurrency pattern changes
- **Skip**: Simple variable/algorithm swaps, single-line fixes, naming changes
