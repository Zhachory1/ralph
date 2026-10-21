# SLOs: [Component Name]

## Service Level Objectives

| SLO           | SLI (Measurement)                    | Target   | Window  |
| ------------- | ------------------------------------ | -------- | ------- |
| Availability  | `1 - (5xx responses / total)`        | 99.9%    | 30 days |
| Latency (p95) | `histogram_quantile(0.95, duration)` | < 300ms  | 30 days |
| Latency (p99) | `histogram_quantile(0.99, duration)` | < 1000ms | 30 days |
| Error Rate    | `errors / total requests`            | < 0.1%   | 30 days |

## Dashboard

- [Primary Dashboard](https://app.datadoghq.com/dashboard/...)
- [SLO Dashboard](https://app.datadoghq.com/slo/...)

## Monitors

| Monitor              | Threshold                 | Alert Channel |
| -------------------- | ------------------------- | ------------- |
| Availability < 99.9% | Error budget burn > 2%/hr | #team-alerts  |
| p95 Latency > 300ms  | 5min rolling average      | #team-alerts  |
| Error Rate > 0.1%    | 5min rolling average      | #team-alerts  |

## Error Budget Policy

- **Budget remaining > 50%**: Normal development velocity
- **Budget remaining 20-50%**: Prioritize reliability work
- **Budget remaining < 20%**: Freeze non-critical changes, focus on reliability
- **Budget exhausted**: Incident-level response until budget recovers
