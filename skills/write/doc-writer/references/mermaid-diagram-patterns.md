# Mermaid Diagram Patterns for Architecture Docs

Reference companion to `doc-writer/SKILL.md`. Reusable Mermaid diagram
patterns for `/docs/ARCHITECTURE.md` updates. Always include diagrams when
changes affect system architecture — prose alone is insufficient.

## Dependency Diagram

Show what the service calls and what calls it:

````markdown
### Dependencies

```mermaid
graph TD
    AuctionEngine -->|calls| BiddingService
    BiddingService -->|calls| BudgetService
    BiddingService -->|calls| CampaignService
    BiddingService -->|reads| ConfigStore
```
````

## Component Diagram

Show internal components and their relationships:

````markdown
### Components

```mermaid
graph LR
    subgraph BiddingService
        BidCalculator --> FloorEnforcer
        BudgetChecker --> BidCalculator
    end
    Request([Request]) --> BudgetChecker
    FloorEnforcer --> Response([Response])
```

- **BidCalculator**: Core bid computation logic
- **BudgetChecker**: Validates campaign budget availability (NEW)
- **FloorEnforcer**: Ensures bids meet minimum thresholds
````

## Data Flow / Sequence Diagram

Show how a request flows through the system:

````markdown
### Data Flow

```mermaid
sequenceDiagram
    participant AuctionEngine
    participant BiddingService
    participant BudgetService
    participant DB

    AuctionEngine->>BiddingService: calculateBid(campaign_id)
    BiddingService->>BudgetService: checkBudget(campaign_id)
    BudgetService->>DB: SELECT remaining FROM budgets
    DB-->>BudgetService: budget
    BudgetService-->>BiddingService: budget_remaining
    BiddingService->>BiddingService: compute optimal bid
    BiddingService-->>AuctionEngine: bid_amount
```
````

## State Diagram

For features with lifecycle states:

````markdown
### Campaign Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Draft
    Draft --> Active: approve()
    Active --> Paused: pause()
    Paused --> Active: resume()
    Active --> Exhausted: budget depleted
    Exhausted --> [*]
```
````

## When to Use Which Diagram

| Change Type              | Recommended Diagrams                |
| ------------------------ | ----------------------------------- |
| New service dependency   | Dependency + Sequence               |
| Internal refactor        | Component                           |
| New API endpoint         | Sequence + Dependency               |
| State machine / workflow | State                               |
| Most changes (default)   | Dependency graph + Sequence diagram |

Multiple diagrams are encouraged. A dependency graph plus a sequence diagram
is a strong default for most architecture changes.
