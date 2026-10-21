# Before / After Conversions

A single task — "aggregate service CPU by region" — shown as a fragile Bash
pipeline, then as typed, tested Go and Python that emit JSON.

## Bad: bash pipeline aggregating data to csv

```bash
#!/usr/bin/env bash
# aggregate.sh — fragile, untestable, positional text
curl -s "https://api.example.com/services" \
  | jq -r '.[] | [.name, .region, .cpu] | @tsv' \
  | awk -F'\t' '{ sum[$2] += $3 } END { for (r in sum) print r","sum[r] }' \
  | sort > cpu_by_region.csv
```

No types, no error handling (a failed `curl` still writes an empty file), no
tests, csv output, and it breaks on any name containing a tab or comma.

## Good: Go with `os/exec`, JSON, and a table-driven test

```go
type Service struct {
	Name   string  `json:"name"`
	Region string  `json:"region"`
	CPU    float64 `json:"cpu"`
}

// Pure, testable core logic.
func aggregateByRegion(services []Service) map[string]float64 {
	totals := map[string]float64{}
	for _, s := range services {
		totals[s.Region] += s.CPU
	}
	return totals
}
```

```go
func TestAggregateByRegion(t *testing.T) {
	cases := []struct {
		name string
		in   []Service
		want map[string]float64
	}{
		{"empty", nil, map[string]float64{}},
		{"sums per region", []Service{
			{"a", "us-west-2", 1.5},
			{"b", "us-west-2", 2.0},
			{"c", "eu-west-1", 4.0},
		}, map[string]float64{"us-west-2": 3.5, "eu-west-1": 4.0}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := aggregateByRegion(tc.in)
			if len(got) != len(tc.want) {
				t.Fatalf("len: got %d, want %d", len(got), len(tc.want))
			}
			for region, want := range tc.want {
				if got[region] != want {
					t.Errorf("%s: got %v, want %v", region, got[region], want)
				}
			}
		})
	}
}
```

## Good: Python with a typed model and a stdlib test

Stdlib only — no `pytest` dependency needed for a small tool.

```python
from dataclasses import dataclass
from collections import defaultdict


@dataclass
class Service:
    name: str
    region: str
    cpu: float


def aggregate_by_region(services: list[Service]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for s in services:
        totals[s.region] += s.cpu
    return dict(totals)
```

```python
import unittest


class TestAggregate(unittest.TestCase):
    def test_sums_per_region(self):
        services = [
            Service("a", "us-west-2", 1.5),
            Service("b", "us-west-2", 2.0),
            Service("c", "eu-west-1", 4.0),
        ]
        self.assertEqual(
            aggregate_by_region(services),
            {"us-west-2": 3.5, "eu-west-1": 4.0},
        )
```

The pure aggregation function is tested directly, in milliseconds, with no
network and no subprocess — exactly what the Bash version cannot do.
