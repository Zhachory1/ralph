# **Autonomous Engineering Organization: System Architecture & Implementation Specification (v4.0 — Paperclip \+ OpenCode)**

**Target Audience:** Local Orchestrator, Specialist Agent Profiles, and Bootstrap Automation\
**Operating Paradigm:** Director-of-Engineering Human-in-the-Loop (HITL) Multi-Agent Organization\
**Control Plane:** Self-Hosted **Paperclip** (`--bind tailnet`) \+ **OpenCode (`opencode_local`)** \+ **Hermes Agent** Adapters \+ **Hindsight** \+ **`qmd`**

# **1\. Executive Summary & Architectural Shift (v3.1 \-\> v4.0)**

Version 4.0 refactors the custom 9-service control plane from v3.1 onto Paperclip—an open-source, self-hosted agent company control plane—paired with Paperclip's native **`opencode_local`** adapter for software engineering and **Hermes** adapters for coordination, specification authoring, code critique, and communications.

By offloading generic orchestration plumbing to Paperclip, running OpenCode directly as a first-class adapter, and integrating the native **Hindsight** plugin (`@vectorize-io/hindsight-paperclip`) alongside **`qmd mcp`** over the Git-backed PARA `org-docs` repository, v4.0 requires **zero custom MCP servers** to build or maintain.

## **What Paperclip \+ OpenCode Replaces vs. What Stays Custom**

| Architectural Concern | v3.1 Custom Stack | v4.0 Paperclip \+ OpenCode Stack |
| :---- | :---- | :---- |
| **Task Board & Dependencies** | Vikunja \+ custom `n8n` `blocked_by` checks | **Paperclip Work & Task System** (atomic task checkout, first-class blockers, goal ancestry, inbox) |
| **Concurrency Locks & Recovery** | Postgres `pr_maintenance_locks` \+ 25-min lease TTL \+ `n8n` 3-worker semaphore | **Paperclip Heartbeat Engine** (DB-backed wakeup queue, coalescing, execution locks, self-healing recovery) |
| **SWE & SRE Execution** | Hermes `swe-<product>` \-\> Custom Coding Harness MCP (`:8765`) | **Direct `opencode_local` adapter** on `swe-<product>` and `sre-ops` seats (native `opencode run --format json`, stdout streaming, session resume) |
| **Completion Signaling** | Custom sync progress pings or async REST callback API | **Native Paperclip Run Lifecycle** — Paperclip streams logs/costs and triggers the next workflow stage on run exit |
| **Git Worktrees** | Custom worktree manager inside `:8765` | **Paperclip Execution Workspaces** (isolated Git worktrees per issue) |
| **Budget & Cost Controls** | Passive OpenTelemetry (Langfuse / Arize Phoenix) | **Paperclip Budget Engine** (company/agent/project hard-stops \+ 80%/100% alerts \+ opt-in OTel) |
| **Document Review & Feedback** | Docs PRs \+ `MkDocs` \+ `Kroki` PNGs in chat | **Paperclip Document Revisions & Anchored Comments** \+ optional `org-docs` (`MkDocs` \+ `Kroki`) |
| **Mobile & Remote UI** | Open WebUI \+ Nginx subdomain reverse proxy \+ Tailscale | **Paperclip Mobile-Ready UI** (`--bind tailnet :3100`) |
| **Long-Term Memory & PARA RAG** | Postgres `pgvector` (`document_chunks`, `decision_log`, `entity_memory`) \+ Git `org-docs` | **Hindsight Plugin (`@vectorize-io/hindsight-paperclip`) \+ PARA `org-docs` \+ `qmd mcp`** — zero custom MCP code; automatic episodic recall/reflect \+ on-device Markdown search |
| **Untrusted PR Audits (`audit_pr`)** | Custom MCP verb on `:8765` | **Ephemeral Docker Sandbox** — `reviewer` runs coworker PR test suites inside a network-isolated, secret-stripped container |
| **Gmail & Google Chat Triage** | `n8n` \+ `hermes-comms` | **Slim `n8n` Comms Bridge** — ingests Gmail Pub/Sub & Google Chat webhooks into Paperclip Routine triggers |

# **2\. System Topology (v4.0 — Paperclip \+ OpenCode)**

```
flowchart TB
    Director["<b>Human Director of Engineering</b><br/>• Sets Goals & Vision • Approves Council/PR Gates • Submits Pending Reviews"]

    subgraph ControlPlane["Layer 1: Paperclip Control Plane (--bind tailnet :3100)"]
        direction TB
        PCCore["<b>Paperclip Server & Mobile UI</b><br/>• Org Chart & Reporting Lines • Atomic Task Checkout & Blockers<br/>• Governance & Approval Stages • Hard Budget Caps & OTel<br/>• Document Revisions & Anchored Comments • Secrets Manager"]
        Heartbeat["<b>Heartbeat Queue & Scheduled Routines</b><br/>• 3-Min GitHub GraphQL PR Poller (In & Out)<br/>• Weekly PARA & qmd Gardening Cron<br/>• Webhook Receivers (GitHub / Comms Bridge)"]
        Gateway["<b>Paperclip MCP Tool Gateway</b><br/>Per-Agent & Per-Action Policy: Allowed | Ask First | Off"]
        Hindsight["<b>Hindsight Plugin</b><br/><i>@vectorize-io/hindsight-paperclip</i><br/>Auto retain / recall / reflect (Org Bank)"]
    end

    subgraph Agents["Layer 2: Agent Roster (Native Paperclip Adapters)"]
        direction TB
        subgraph ReasoningAgents["Hermes Adapters (Coordination, Specs, Critique & Comms)"]
            direction LR
            Orch["<b>orchestrator</b><br/><i>hermes_gateway</i><br/>Chief of Staff"]
            Author["<b>author</b><br/><i>hermes_local</i><br/>Staff Architect"]
            Reviewer["<b>reviewer</b><br/><i>hermes_local</i><br/>Principal Critic"]
            Comms["<b>comms</b><br/><i>hermes_local</i><br/>Inbox/Chat Triage"]
        end

        subgraph CodingAgents["OpenCode Adapters (Direct Git Worktree Execution)"]
            direction LR
            SWE["<b>swe-&lt;product&gt;</b><br/><i>opencode_local</i><br/>Repo Specialists (A, B, ...)"]
            Ops["<b>sre-ops</b><br/><i>opencode_local</i><br/>Alert Repro & Hygiene"]
        end

        Council["<b>Council (3 Judges)</b><br/><i>Multi-Model Review Stage</i><br/>Arch (Claude) • Security (GPT/o3) • Scope (Gemini)"]
    end

    subgraph KnowledgeAndBridge["Layer 3: Shared State, PARA Docs & Comms Bridge"]
        direction LR
        PG["<b>PostgreSQL 17</b><br/>• Paperclip Production DB<br/>• processed_pr_events<br/>• inbound_pr_reviews"]
        QMD["<b>qmd MCP Server</b><br/><i>@tobilu/qmd (On-Device)</i><br/>BM25 + Local Vector + Rerank"]
        OrgDocs["<b>org-docs (Git PARA Repo)</b><br/>1-projects / 2-areas /<br/>3-resources / 4-archive<br/><i>(+ Optional MkDocs)</i>"]
        N8N["<b>Slim n8n Comms Bridge</b><br/>Gmail Pub/Sub & Google Chat<br/>Webhooks → Paperclip Routines"]
    end

    subgraph Workspaces["Layer 4: Local Repos & Isolated Sandboxes"]
        direction LR
        Worktrees["<b>Paperclip Git Worktrees</b><br/>Isolated per-issue branches<br/>executed directly by opencode_local"]
        Sandbox["<b>Ephemeral Docker Sandbox</b><br/>Network-isolated, secret-stripped<br/>test runner for untrusted coworker PRs"]
    end

    Director <-->|"Paperclip Web/Mobile UI (Tailscale) + GitHub PRs"| PCCore
    N8N -->|"Routine Webhook"| Heartbeat
    PCCore <--> Heartbeat
    PCCore <--> Hindsight
    Heartbeat --> Agents
    Agents <--> Gateway
    PCCore <--> PG
    Gateway <-->|"Scoped MCP"| QMD
    QMD <--> OrgDocs
    CodingAgents <--> Worktrees
    Reviewer -->|"Untrusted PR tests"| Sandbox
```

# **3\. Org Chart, Governance & Budget Controls**

Instead of scattering rules across local `config.yaml` files and custom routing scripts, the organization is modeled directly inside Paperclip's **Org Chart**, governed by a shared two-page MANIFEST.md (org-docs/docs/2-areas/engineering-ops/MANIFEST.md):

* **Page 1 (Autonomous Actions):** Actions agents may execute without asking (e.g., drafting RFCs, running tests, fixing concrete PR feedback, rebasing behind branches, staging GitHub `PENDING` reviews).\
* **Page 2 (Hard Stops & Escalations):** Actions agents must never perform autonomously (e.g., merging PRs, sending external emails/messages, altering approved API contracts, deploying to production), each mapped to the Director for escalation.

## **3.1 Paperclip Org Chart & Adapter Mapping**

| Role | Reports To | Paperclip Adapter | Model / Config Notes | MCP Gateway & Workspace Policy | Budget Policy |
| :---- | :---- | :---- | :---- | :---- | :---- |
| **`orchestrator`** *(Chief of Staff)* | **Director (Human)** | `hermes_gateway` | Frontier reasoning (Claude Opus / GPT-5) | `qmd mcp` (`Read`), Hindsight (`Auto`), GitHub (`Read`), Routines (`Allowed`) | Hard cap \+ 80% alert |
| **`author`** *(Staff Architect)* | `orchestrator` | `hermes_local` | Frontier reasoning (Claude Opus / GPT-5) | `qmd mcp` (`Read`), Hindsight (`Auto`), `org-docs/docs/1-projects/` (`RW`), GitHub Docs PRs (`Allowed`) | Per-project cap |
| **`Council` (3 Judges)** | **Governance Gate** | Multi-Model Review Stage | **Judge 1: Claude OpusJudge 2: GPT-5 / o3Judge 3: Gemini** | Read-only RFC/Diff \+ `qmd mcp` (`3-resources/adrs/`) \+ Hindsight recall | Per-review cap |
| **`swe-<product>`** *(1 per repo)* | `orchestrator` | **`opencode_local`** | Explicit provider/model ID (e.g., openrouter/anthropic/claude-sonnet-4.5); inherits opencode.json subagent routing & failovers | Assigned repo Git worktree only, `opencodePermissionMode: allow`, `qmd mcp` (`Read`), Hindsight (`Auto`), `org-docs/docs/2-areas/` (`RW`), GitHub PR Reply (`Allowed`), Merge (`Off`) | Per-agent hard cap |
| **`reviewer`** *(Principal Critic)* | `orchestrator` | `hermes_local` | High-rigor reasoning (o3 / Claude Opus) | Read-only repo diff \+ Ephemeral Docker Sandbox for coworker PR tests, GitHub `PENDING` reviews (`Allowed`), Merge (`Off`), `qmd mcp` (`Read`), Hindsight (`Auto`) | Per-agent cap |
| **`sre-ops`** *(Maintainer)* | `orchestrator` | **`opencode_local`** | Balanced coding/triage model | Hotfix worktrees, observability logs, weekly PARA/qmd gardening, Production Deploys (`Off`) | Scheduled-run hard cap |
| **`comms`** *(Comms Triage)* | **Director (Human)** | `hermes_local` | Fast / low-cost model (Claude Haiku / Sonnet / Ollama) | Hindsight (Auto entity/stakeholder recall), `qmd mcp` (`Read`), Gmail/Chat Send (`Ask first`) | Low monthly cap |

## **3.2 `opencode_local` & Governance Configuration Details**

* **Explicit `provider/model` IDs:** Paperclip's `opencode_local` adapter validates model IDs against `opencode models` before each run. Configure explicit IDs (e.g., `openrouter/anthropic/claude-sonnet-4.5`).\
* **Preserving `opencode.json` Subagent Routing & Failovers:** Because Paperclip Runner isolates each OpenCode session's home directory (and Legacy runner copies config into a temporary `XDG_CONFIG_HOME`), template your `opencode.json` (containing `explore`/`general`/`small_model` offloading and OpenRouter provider failovers) into Paperclip's adapter environment so every `swe-<product>` run inherits rate-limit protections automatically.\
* **Post-Compact Context Recovery (Paperclip Shared Skills):** Attach `MANIFEST.md`, PR reply formatting rules (`🤖 **[Agent Maintenance]**` \+ `<!-- agent-maintenance-reply -->`), and repo-specific build/test commands as **Paperclip Shared Skills** injected at runtime so agents retain governance rules even after OpenCode context compaction.\
* **Shared Agents, Personal GitHub Identity:** Paperclip's accountable connection model lets `swe-<product>` and `reviewer` use managed GitHub credentials with responsible-user attribution while locking `merge_pr` to `Off` so only the Director merges code.

# **4\. The Multi-Model Council & Consensus Protocol**

The **Council** acts as an automated architectural review board catching structural flaws, security gaps, and scope creep before consuming Director attention. Keeping Council judges stateless across three distinct model families prevents sycophancy and single-model blind spots.

## **4.1 Council Composition**

1. **Judge 1 — Architecture & Scale Critic:** Evaluates system boundaries, latency/throughput bottlenecks, state consistency, failure modes, and maintainability.\
2. **Judge 2 — Security & Blast-Radius Judge:** Audits auth boundaries, secret handling, PII/data exposure, external side effects, and rollback safety.\
3. **Judge 3 — Scope & Pragmatism Judge:** Evaluates alignment with the Director's goal, flags over-engineering, and checks whether a simpler design suffices.

## **4.2 Structured Verdict Schema & Bounded Revision Loop**

Each judge emits a strict JSON verdict:

```
{
  "judge_role": "architecture_scale",
  "verdict": "PASS | REVISE | BLOCK",
  "confidence": 0.88,
  "blocking_issues": [
    {
      "severity": "critical | major",
      "location": "Section 3.2 - Rate Limiter State",
      "issue": "In-memory token bucket fails under multi-replica horizontal scaling.",
      "recommendation": "Use atomic Redis Lua script or Postgres advisory locks."
    }
  ],
  "non_blocking_notes": [
    "Consider adding Prometheus counter for 429 rejections."
  ]
}
```

* **Unanimous `PASS`:** Advances immediately to the Director Approval Gate with a 3-bullet Decision Brief.\
* **Any `REVISE`:** Paperclip routes `blocking_issues` back to `author` for automated revision (**hard cap: 2 rounds**).\
* **Unresolved `REVISE` after 2 Rounds or Any `BLOCK`:** Paperclip pauses the issue and escalates to the Director's inbox with both the Author's position and the Council's dissenting critique.

# **5\. Initiative Lifecycle on Paperclip**

Every initiative starts as a **Paperclip Goal / Project** so goal ancestry flows automatically to every child issue:

```
flowchart LR
    Goal["<b>Goal / Project</b><br/><i>Director</i><br/>Sets vision & constraints"]
    RFC["<b>RFC Authoring</b><br/><i>author (hermes_local)</i><br/>Drafts 1-projects/&lt;slug&gt;/rfc.md"]
    Council["<b>Council Review</b><br/><i>3-Judge Panel</i><br/>Arch • Security • Scope"]
    Approve["<b>Director Gate</b><br/><i>Human Director</i><br/>Paperclip Doc Review & Approve"]
    Build["<b>Implementation</b><br/><i>swe-&lt;product&gt; (opencode_local)</i><br/>Isolated Git worktree + Draft PR"]
    PeerRev["<b>Peer Review</b><br/><i>reviewer (hermes_local)</i><br/>Diff & RFC audit"]
    Merge["<b>Director PR Merge</b><br/><i>Human Director</i><br/>Final review & GitHub merge"]
    Closeout["<b>Closeout Routine</b><br/><i>Paperclip Routine</i><br/>ADR + qmd embed + git mv to 4-archive"]

    Goal --> RFC
    RFC --> Council
    Council -->|"REVISE (≤ 2 rounds)"| RFC
    Council -->|"PASS / Escalated"| Approve
    Approve -->|"Approved (deps checked)"| Build
    Build -->|"Draft PR opened"| PeerRev
    PeerRev -->|"Defects (≤ 2 rounds)"| Build
    PeerRev -->|"Approved"| Merge
    Merge -->|"PR merged"| Closeout
```

1. **1\. Vision \-\> RFC (`author`):** Director creates a Project/Goal in Paperclip. `orchestrator` assigns an RFC issue to `author`, which writes `org-docs/docs/1-projects/<proj_slug>/rfc.md` and attaches it as a **Paperclip Document** (and/or Docs PR).\
2. **2\. Council Review Stage:** Moving the RFC issue to Review wakes the 3-Judge Council.\
3. **3\. Director Approval Gate:** Director reviews the rendered RFC \+ Council Decision Brief in Paperclip's web/mobile UI, leaves anchored passage comments if needed, and clicks **Approve**.\
4. **4\. Decomposition & Dependency Gating:** Approval wakes `orchestrator` to create child issues per product with native **Paperclip Blocker Dependencies (`blocked_by`)**. Downstream services (e.g., `swe-product-b`) stay locked automatically until upstream API/schema issues (`swe-product-a`) close.\
5. **5\. Implementation (`opencode_local`) & Peer Review (`reviewer`):** Paperclip wakes `swe-<product>` (`opencode_local`) inside an isolated Git worktree. OpenCode automatically receives episodic context from Hindsight on run start and queries `qmd mcp` for `2-areas/` contracts, implements the feature, updates `2-areas/` docs if contracts changed, runs local tests, and opens a Draft PR. On clean exit, Paperclip records Work Products and exact token/cost spend, then wakes `reviewer` to audit the diff. Once approved, the issue enters your **Director PR Merge** queue.\
6. **6\. Project Closeout Hook:** When the final PR merges and the Project closes, a Paperclip Routine distills key trade-offs into `3-resources/adrs/ADR-XXX.md`, moves `1-projects/<proj_slug>/` via `git mv` into `4-archive/projects/`, and runs `qmd embed` (while Hindsight automatically retains the outcome).

# **6\. Closed-Loop PR Maintenance & Inbound Reviews (Paperclip Routines)**

Paperclip's **Scheduled Routines** (with built-in concurrency locks and catch-up policies) replace the custom `n8n` cron \+ lock-lease plumbing from v3.1.

## **6.1 Outbound PR Maintenance Routine (every 3 minutes)**

Queries GitHub GraphQL (`is:pr is:open author:@me`) and diffs node IDs against `processed_pr_events` in Postgres (filtering out any comment containing `<!-- agent-maintenance-reply -->` to prevent infinite loops):

| Trigger Condition | Paperclip / Agent Action | Execution & Escalation Rule |
| :---- | :---- | :---- |
| **Red CI: Infra / Flake** *(timeout, OOM, 502\)* | Re-runs the failed job via GitHub/Buildkite API without touching code. | Escalates to Director if retry fails twice. |
| **Red CI: Code / Test Failure** *(compile, test, lint)* | Queues wakeup on the existing Paperclip issue, **resuming `swe-<product>`'s OpenCode session** in the same worktree to inspect logs, patch, test, and push. | Halts and escalates to Director after **2 consecutive red pushes**. |
| **Base-Branch Drift (`BEHIND` / `DIRTY`)** | Resumes `swe-<product>` (`opencode_local`) in the worktree to rebase onto `main`, resolve conflicts, re-run tests, and push (`--force-with-lease`). | Escalates to Director if semantic conflict alters an approved RFC contract. |
| **Coworker Comment: Concrete** *(bug, edge case, naming, missing test)* | Resumes `swe-<product>` (`opencode_local`) to fix code, run tests, push commit, and **reply directly on the GitHub thread** (`🤖 [Agent Maintenance] <explanation> + commit SHA` \+ `<!-- agent-maintenance-reply -->`). | Moves issue back to `reviewer` if major logic changed. |
| **Coworker Comment: Ambiguous / Scope Change** *(contradicts RFC or alters API)* | Does **not** edit code or reply. Posts summary \+ options to Director's Paperclip inbox. | Pauses issue until Director rules. |
| **Merge-Ready State** *(Coworker `APPROVED` \+ Green CI)* | Sends a **"Merge Ready"** notification to Director's Paperclip inbox. | Awaits final Director merge. |

## **6.2 Inbound Coworker PR Review Routine (`every 3 minutes`)**

Queries `is:pr is:open review-requested:@me -author:@me` against `inbound_pr_reviews` in Postgres:

1. **1\. Trigger:** Fires on newly assigned PRs or whenever `head_sha` advances after prior feedback.\
2. **2\. Sandboxed Test Verification:** Wakes `reviewer` (`hermes_local`), which fetches domain conventions from `swe-<product>`'s Paperclip Skills and `2-areas/` via `qmd mcp` and Hindsight recall, and executes the coworker PR's test suite and linters inside an **ephemeral, network-isolated Docker sandbox** with host secrets (`~/.ssh`, `~/.aws`, `.env`) stripped.\
3. **3\. GitHub `PENDING` Review Staging:** `reviewer` calls `POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews` with inline `{ path, line, body }` comments **omitting the `event` field**. This creates a **Pending Review** under your personal GitHub account—invisible to your coworker until you inspect, edit, or click **"Submit review"** in GitHub's native UI.\
4. **4\. Director Review Dossier:** Creates a lightweight Paperclip review task with local test status, a 3-bullet architectural verdict, and a direct link to the PR's **Files changed** tab. Automatically closes once you submit the review on GitHub.

# **7\. Memory & Document Architecture: Hindsight \+ PARA / qmd**

v4.0 splits memory into two complementary, off-the-shelf layers — **Hindsight** (`@vectorize-io/hindsight-paperclip`) for implicit, automatic cross-run memory and **PARA `org-docs` \+ `qmd mcp`** for explicit, human-readable canonical documents — eliminating all custom MCP server and `pgvector` chunker code.

## **7.1 Responsibility Boundaries (Avoiding Overlap)**

| System / Engine | What It Owns | What It Does NOT Own |
| :---- | :---- | :---- |
| **Hindsight Plugin** `@vectorize-io/hindsight-paperclip` | Cross-run episodic memory, stakeholder/entity context, Director preferences, post-mortem lessons, background `reflect` consolidation. | Long-form RFCs or versioned architecture specs. |
| **PARA `org-docs` \+ `qmd mcp`** | Long-form canonical Markdown in `1-projects/`, `2-areas/`, `3-resources/`, `4-archive/`. | Daily scratchpads, `MEMORY.md`, or `items.yaml` logs (never contradicts Hindsight). |
| **Weekly Paperclip Gardening Routine** (`sre-ops` / `author`) | Archives completed `1-projects/` into `4-archive/projects/`, ensures `2-areas/` matches merged code, distills ADRs, runs `qmd embed`. | Manual memory extraction. |

## **7.2 PARA Document Topology (`org-docs` Git Repository) & `qmd mcp`**

The Git-backed `org-docs` repository holds canonical specifications. On-device hybrid search and reranking are provided by **`qmd mcp`** (`@tobilu/qmd`), featuring BM25 keyword search, local GGUF vector embeddings, Reciprocal Rank Fusion (RRF), and local reranking registered directly in Paperclip's MCP Tool Gateway:

```
org-docs/
├── mkdocs.yml                      # Optional standalone wiki config
└── docs/
    ├── 1-projects/                 # Short-term active initiatives (Goal + Deadline)
    │   └── <proj_slug>/            # Maps 1:1 to Paperclip Project
    │       ├── rfc.md              # Active specification
    │       ├── council-brief.md    # 3-Judge evaluation output
    │       └── rollout-plan.md     # Milestone gates & deployment steps
    ├── 2-areas/                    # Permanent domains & living specifications
    │   ├── products/
    │   │   ├── architecture.md
    │   │   ├── api-contracts.md
    │   │   └── runbooks.md
    │   ├── engineering-ops/        # MANIFEST.md, CI/CD standards, security policies
    │   └── comms-and-stakeholders/ # Ownership maps, triage SLAs
    ├── 3-resources/                # Immutable reference material & reusable assets
    │   ├── adrs/                   # Architecture Decision Records (ADR-001...)
    │   ├── templates/              # RFC, post-mortem, and Council templates
    │   └── external-refs/          # Vendor specs, API guides
    └── 4-archive/                  # Completed projects & deprecated specs
        ├── projects/               # Moved via git mv on closeout
        └── deprecated-areas/
```

## **7.3 Four-Tier Memory Architecture**

| Memory Tier | Retention & Scope | Contents & Artifacts | Storage Substrate |
| :---- | :---- | :---- | :---- |
| **Tier 1 — Ephemeral** | Single heartbeat run | Raw terminal output, diffs, temporary test logs, scratchpad reasoning. | Adapter LLM context \+ ephemeral worktree/sandbox |
| **Tier 2 — Working State** | Days–Weeks (until Project `Done`) | Active Paperclip issues, comments, attachments, activity log, `processed_pr_events`, `inbound_pr_reviews`. | **Paperclip DB** \+ Postgres |
| **Tier 3 — Procedural ("How-To")** | Persistent, role-scoped | Repo build/test commands, lint fixes, `MANIFEST.md`, compiled playbooks. | **Paperclip Skills & Skill Studio** \+ Hermes `SKILL.md` |
| **Tier 4 — Long-Term Semantic & Episodic** | Months–Years, org-wide | Implicit episodic lessons, stakeholder/entity context, Director preferences (**Hindsight** with company-level granularity `paperclip::{companyId}`); explicit canonical specs & immutable ADRs (**Git `org-docs` \+ `qmd mcp`**). | Hindsight (local `hindsight-all` or cloud) \+ Git `org-docs` / `qmd` |

# **8\. Minimal Deployment Blueprint (Zero Custom MCP Servers)**

Compared to the 9-service v3.1 stack, v4.0 runs with zero custom MCP servers:

1. **PostgreSQL 17 (Docker):** Hosts `paperclip` (Paperclip's production DB) and `agent_memory` (`processed_pr_events`, `inbound_pr_reviews`).\
2. **Paperclip Server (`--bind tailnet :3100`) \+ Hindsight Plugin:** Runs the control plane, `opencode_local` \+ Hermes adapters, Git worktrees, budgets, routines, and mobile/web UI, with `@vectorize-io/hindsight-paperclip` installed (`npx paperclipai@latest plugin install @vectorize-io/hindsight-paperclip`, backed by self-hosted `pip install hindsight-all` or Hindsight Cloud).\
3. **org-docs (Git PARA Repo) \+ qmd mcp (`@tobilu/qmd`):** Registered in Paperclip's MCP Tool Gateway for on-device hybrid search over long-form specs.\
4. **Optional Companions:** Slim `n8n` Comms Bridge (`--profile comms`) for Gmail Pub/Sub \+ Google Chat webhooks, and `MkDocs Material` (`--profile docs`) for a standalone PARA wiki.

## **8.1 Database Initialization (`init-db/01-init.sql`)**

```sql
CREATE DATABASE paperclip;

\connect agent_memory;

CREATE TABLE processed_pr_events (
    event_id      TEXT PRIMARY KEY,
    repo          TEXT NOT NULL,
    pr_number     INTEGER NOT NULL,
    event_type    TEXT NOT NULL,
    author_login  TEXT NOT NULL,
    processed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE inbound_pr_reviews (
    repo                TEXT NOT NULL,
    pr_number           INTEGER NOT NULL,
    head_sha            TEXT NOT NULL,
    author_login        TEXT NOT NULL,
    title               TEXT NOT NULL,
    paperclip_issue_id  TEXT,
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'audited', 'reviewed_by_human')),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (repo, pr_number)
);
```

## **8.2 Minimal Companion `docker-compose.yml`**

```
services:
  postgres:
    image: pgvector/pgvector:pg17
    container_name: agent-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: agent_memory
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./init-db:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d agent_memory"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Optional: Only needed for Gmail Pub/Sub + Google Chat webhook ingestion
  n8n:
    image: docker.n8n.io/n8nio/n8n:latest
    container_name: agent-n8n-comms
    restart: unless-stopped
    profiles: ["comms"]
    ports:
      - "127.0.0.1:5678:5678"
    environment:
      N8N_HOST: "localhost"
      WEBHOOK_URL: ${PUBLIC_WEBHOOK_TUNNEL_URL:-http://localhost:5678}
      DB_TYPE: "postgresdb"
      DB_POSTGRESDB_HOST: "postgres"
      DB_POSTGRESDB_DATABASE: "agent_memory"
      DB_POSTGRESDB_USER: ${POSTGRES_USER}
      DB_POSTGRESDB_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - n8n_data:/home/node/.n8n
    depends_on:
      postgres:
        condition: service_healthy

  # Optional: Standalone PARA Wiki + Mermaid renderer
  docs-portal:
    image: squidfunk/mkdocs-material:latest
    container_name: agent-docs
    restart: unless-stopped
    profiles: ["docs"]
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - ${ORG_DOCS_PATH:-./org-docs}:/docs

volumes:
  pgdata:
  n8n_data:
```

# **9\. Guardrails, Observability & Phased Rollout Plan**

## **9.1 Operational Guardrails**

* **Hard Budget Caps:** Configure monthly and per-run spend limits in Paperclip for every agent seat, with 80% alerts and automatic hard-stops on unattended `sre-ops` crons and `swe-<product>` CI-fix loops.\
* **Bounded Loops:** Enforce max **2 iterations** for `author <-> Council`, `swe-<product> <-> reviewer`, and `swe-<product> <-> CI` loops before escalating to the Director.\
* **Bot-Reply Loop Prevention:** All automated PR comments include `<!-- agent-maintenance-reply -->` and are filtered out by the 3-minute GitHub GraphQL Routine.\
* **Private Tailnet Perimeter:** Bind Paperclip strictly to `--bind tailnet` and Postgres/local Hindsight to `127.0.0.1`. Never expose the Paperclip UI or `n8n` editor to the public internet.\
* **Sandboxed Untrusted Execution:** `reviewer` runs all external coworker PR tests inside an ephemeral, network-isolated Docker sandbox with host credentials stripped.\
* **Observability:** Enable Paperclip's opt-in OpenTelemetry tracing (`OTEL_EXPORTER_OTLP_ENDPOINT`) alongside Paperclip's built-in cost ledger and activity log.

## **9.2 Phased Rollout Plan**

5. **Phase 1 — Core Coding & PR Loop (Week 1):**\
   * Start `postgres` (`docker compose up -d postgres`) and initialize Paperclip (`npx paperclipai@latest onboard --yes --bind tailnet`, pointed at the `paperclip` Postgres DB).\
   * Provision **3 seats**: `orchestrator` (`hermes_gateway`), one `swe-<product>` (`opencode_local` with your `opencode.json` failovers), and `reviewer` (`hermes_local` \+ sandboxed test runner).\
   * Attach `MANIFEST.md` as a Paperclip Shared Skill and enable the 3-minute **Outbound PR Maintenance** and **Inbound `review-requested:@me`** Routines. Calibrate until error rates are near zero.\
6. **Phase 2 — Hindsight, PARA Docs (`qmd`) & Multi-Model Council (Week 2):**\
   * Install `@vectorize-io/hindsight-paperclip` (`npx paperclipai@latest plugin install @vectorize-io/hindsight-paperclip`) with company-level granularity (`paperclip::{companyId}`) backed by local `hindsight-all` (or Hindsight Cloud).\
   * Initialize `org-docs` with PARA folders (`1-projects/`, `2-areas/`, `3-resources/`, `4-archive/`), run `qmd embed`, and register `qmd mcp` in Paperclip's MCP Tool Gateway.\
   * Add `author`, the **3-Judge Council** governance stage for RFC reviews, the Project Closeout Routine, and the weekly `sre-ops` (`opencode_local`) PARA \+ `qmd embed` gardening Routine.\
7. **Phase 3 — Communications Triage (Week 3):**\
   * Start `docker compose --profile comms up -d` (`n8n`) to bridge Gmail Pub/Sub and Google Chat webhooks into Paperclip's `comms` (`hermes_local`) agent with `Ask first` send governance.
