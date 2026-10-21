---
name: alert-monitor-suggester
description: "Recommend SLOs, Datadog monitors, alerts, metrics, dashboards for code changes. Triggers: add monitors, SLO, alerting, observability, golden signals, dashboard, metrics instrumentation, datadog monitor, latency, error rate, saturation alert, ONCALL.md, runbook, RED metrics, Big 4."
group: core-workflow
---

# Alert & Monitoring Suggester

Recommend SLOs, Datadog monitors, and alerts for code changes. Observability is a core deliverable — a feature is not production-ready until SLOs, golden signals, and custom metrics are defined, deployed, and validated.

## Analysis Process

1. **Read the changes** — Understand what code was added or modified
2. **Identify failure modes** — What can go wrong? Assume failure, defensive engineering
3. **Map to Golden Signals** — Latency, Traffic, Errors, Saturation
4. **Define SLOs** — What promises does this code need to keep?
5. **Create monitors** — Alert on SLO breaches and anomalies
6. **Add logging** — Enable 3 AM debugging when issues occur
7. **Update ONCALL.md** — Document alerts and runbooks

## The Big 4 Golden Signals

| Signal | What to Measure | Examples |
|--------|----------------|---------|
| **Latency** | Time to service requests | Timers on critical path, E2E latency, model prediction times |
| **Traffic** | Volume of requests | QPS, request successes, request failures |
| **Errors** | Rate of failed requests | Stack dumps, error counters |
| **Saturation** | Resource utilization | Pending requests, memory usage, CPU |

## Output Format

Structure all recommendations into these sections:

**Summary** — Key risks, service topology Mermaid diagram showing where metrics are instrumented

**Metrics** — Counters, histograms, gauges covering all 4 Golden Signals. See [references/golden-signals-reference.md](references/golden-signals-reference.md) for Python/Go patterns.

**SLOs** — Format for `/docs/SLO.md`:
- SLI: measure | Target: %/ms | Window: 30d rolling | Metric: `query` | Error budget

**Monitors** — Datadog YAML; every monitor MUST include runbook link + notification channels. Include alert escalation flow diagram. See [assets/datadog-monitor-template.yaml](assets/datadog-monitor-template.yaml) for copy-and-adapt templates.

**Logging** — Structured log recommendations with Datadog query patterns

**Dashboard** — Widgets by Golden Signals, business metrics, dependencies, debugging rows

**ONCALL.md Updates** — Alert documentation and runbook entries for on-call rotation

### Quality Criteria

- Covers all 4 Golden Signals
- Sufficient context for on-call to diagnose at 3 AM
- Metrics feed directly into SLO calculations
- Alerts actionable with runbook links — no noisy alerts
- Metrics segmented by feature flag state for safe rollouts

## Defensive Engineering

All interfaces must include: timeouts, fallbacks, circuit breakers, input validation.

E2E Ownership: DRI is responsible for incident resolution, post-mortem, and ONCALL.md updates when a feature causes an SLO violation.

## Constraints

- **DO NOT** create noisy alerts — alert fatigue erodes trust
- **DO NOT** monitor metrics without remediation steps in ONCALL.md
- **DO NOT** set arbitrary thresholds — use baseline data from the Measure phase
- **DO NOT** ship features without observability (Test → Monitor → Release)
- **DO** include runbook links in all alerts (link to ONCALL.md)
- **DO** tag all metrics with `service`, `environment`, `team`
- **DO** consider cardinality limits for metric tags
- **DO** segment metrics by feature flag state for safe rollouts
- **DO** treat every incident as a learning opportunity — update runbooks

## Companion Files

- [references/golden-signals-reference.md](references/golden-signals-reference.md) — Instrumentation patterns per language, naming conventions, tag requirements, SLO mapping
- [assets/datadog-monitor-template.yaml](assets/datadog-monitor-template.yaml) — YAML templates for latency, error rate, traffic, saturation monitors

## Scripts

- `python ../code-verifier/scripts/audit-observability.py <path>` — Extract metrics, check Golden Signals coverage
- `../code-verifier/scripts/check-docs.sh <path>` — Verify SLO.md and ONCALL.md exist

All scripts output JSON to stdout.
