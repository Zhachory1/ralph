# Golden Signals Reference

## The Big 4

| Signal         | What to Measure          | Metric Types           | Examples                                            |
| -------------- | ------------------------ | ---------------------- | --------------------------------------------------- |
| **Latency**    | Time to service requests | Histogram, Timer       | p50/p95/p99 request duration, E2E latency           |
| **Traffic**    | Volume of requests       | Counter                | QPS, requests by status, by endpoint                |
| **Errors**     | Rate of failed requests  | Counter with type tags | 4xx, 5xx, timeouts, panics, exceptions              |
| **Saturation** | Resource utilization     | Gauge                  | Queue depth, memory %, CPU %, connection pool usage |

## Instrumentation Patterns

### Python (Datadog statsd)

```python
from datadog import statsd

# Latency
statsd.timing("service.request.duration", elapsed_ms, tags=["endpoint:api_v1"])

# Traffic
statsd.increment("service.requests.total", tags=["status:success", "endpoint:api_v1"])

# Errors
statsd.increment("service.errors.total", tags=["type:timeout", "endpoint:api_v1"])

# Saturation
statsd.gauge("service.queue.depth", queue.qsize(), tags=["queue:main"])
```

### Go (Datadog statsd)

```go
// Latency
statsd.Timing("service.request.duration", elapsed, []string{"endpoint:api_v1"}, 1)

// Traffic
statsd.Incr("service.requests.total", []string{"status:success"}, 1)

// Errors
statsd.Incr("service.errors.total", []string{"type:timeout"}, 1)

// Saturation
statsd.Gauge("service.queue.depth", float64(queueLen), []string{"queue:main"}, 1)
```

### TypeScript (Datadog)

```typescript
import StatsD from "hot-shots";
const statsd = new StatsD();

// Latency
statsd.timing("service.request.duration", elapsed, ["endpoint:api_v1"]);

// Traffic
statsd.increment("service.requests.total", ["status:success"]);

// Errors
statsd.increment("service.errors.total", ["type:timeout"]);

// Saturation
statsd.gauge("service.queue.depth", queueLen, ["queue:main"]);
```

## Metric Naming Convention

```
{service}.{subsystem}.{signal}.{unit}
```

Examples:

- `bidservice.auction.request.duration_ms`
- `bidservice.auction.requests.total`
- `bidservice.auction.errors.total`
- `bidservice.budget.queue.depth`

## Tags (Required)

Every metric must include these tags:

- `service:{name}` -- Service name
- `env:{environment}` -- production, staging, development
- `endpoint:{name}` -- API endpoint or operation
- `status:{result}` -- success, error, timeout

Optional but recommended:

- `feature_flag:{state}` -- on/off (segment by flag)
- `version:{tag}` -- Service version
- `region:{name}` -- AWS region

## SLO Mapping

| Signal     | SLI Definition                       | Typical SLO Target |
| ---------- | ------------------------------------ | ------------------ |
| Latency    | p95 request duration < threshold     | < 300ms (example)  |
| Traffic    | Requests served without error        | > 99.9%            |
| Errors     | Error count / total requests         | < 0.1%             |
| Saturation | Resource utilization below threshold | < 80%              |
