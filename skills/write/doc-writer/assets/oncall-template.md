# On-Call Runbook: [Component Name]

## Service Overview

- **What it does**: [One sentence]
- **Dashboard**: [Datadog link]
- **Logs**: [Datadog log query link]
- **Escalation**: [Slack channel or PagerDuty]

## Common Alerts

### [Alert Name]

**Symptom**: [What the alert says / what the user experiences]

**Check**:

```bash
# Step 1: Check service health
curl -s https://service/health | jq .

# Step 2: Check recent errors
# Datadog query: service:name status:error
```

**Fix**:

1. [Immediate action]
2. [Follow-up action]
3. [Verification step]

**Rollback**: [How to revert if the fix does not work]

---

### [Alert Name 2]

**Symptom**: [Description]

**Check**:

```bash
# Diagnostic commands
```

**Fix**:

1. [Steps]

**Rollback**: [Revert procedure]

## Failure Modes

| Failure Mode       | Impact     | Detection         | Recovery               |
| ------------------ | ---------- | ----------------- | ---------------------- |
| [DB connection]    | [Degraded] | [Monitor name]    | [Restart / failover]   |
| [External timeout] | [Partial]  | [Alert name]      | [Circuit breaker]      |
| [OOM]              | [Full]     | [Container alert] | [Scale / reduce batch] |

## Useful Commands

```bash
# Check pod status
kubectl get pods -l app=service-name

# Tail logs
kubectl logs -l app=service-name --tail=100 -f

# Check recent deploys
kubectl rollout history deployment/service-name
```
