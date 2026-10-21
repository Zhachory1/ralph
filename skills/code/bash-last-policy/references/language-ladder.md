# Language Ladder — Patterns

Detailed patterns for the two languages that replace Bash. Reach for Go first,
Python for automation/data glue. Both ship with tests.

## Go (default)

For services, CLIs, system tools, and anything that shells out.

### Project shape

```
tool/
├── go.mod
├── main.go          # thin: parse args, call pure funcs, encode JSON
├── main_test.go     # table-driven tests on the pure logic
└── internal/        # domain types + logic when it grows beyond one file
```

### `os/exec` — the right way

```go
func runJSON[T any](ctx context.Context, name string, args ...string) (T, error) {
	var zero T
	cmd := exec.CommandContext(ctx, name, args...) // explicit arg vector, no shell
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		return zero, fmt.Errorf("%s: %w: %s", name, err, strings.TrimSpace(stderr.String()))
	}
	var out T
	if err := json.Unmarshal(stdout.Bytes(), &out); err != nil {
		return zero, fmt.Errorf("decode %s output: %w", name, err)
	}
	return out, nil
}
```

Rules:

- Never build a command from a formatted string; pass discrete args. This removes
  shell-quoting and injection classes of bug entirely.
- Always use `CommandContext` so timeouts and cancellation propagate.
- Capture and wrap `stderr` into the error — silent failures are the Bash disease.
- Prefer tools' JSON modes (`-o json`, `--json`) and `json.Unmarshal` into structs.

### Streaming / interactive / long-running

The pattern above is for request/response commands (run, capture, parse). For
long-running, streaming, or interactive processes, stdlib `os/exec` has sharp
edges: `CommandContext` kills only the direct child (not the process group),
and reading `StdoutPipe` while the process runs needs careful goroutine and
`Wait` ordering to avoid deadlocks.

Options, in order:

- Stdlib with care — set `Cmd.Cancel` and `Cmd.WaitDelay` (Go 1.20+) and, on
  Unix, start the child in its own process group (`SysProcAttr{Setpgid: true}`)
  so you can signal the whole group on cancel.
- A focused wrapper such as [`go-cmd/cmd`](https://github.com/go-cmd/cmd) when
  you need streaming output channels plus proper process-group termination
  without hand-rolling it.

Reach for the wrapper only when the interactive/streaming need is real; the
request/response helper above covers the common case.

### Testing

Table-driven, one `t.Run` per case, asserting on the pure function — not on a
subprocess. Integration tests that actually shell out are separate and gated
(e.g. `testing.Short()`), so unit tests stay fast and hermetic.

## Python (automation / data)

For automation, data wrangling, org/data pipelines, and glue.

### Project shape and packaging

A single-file, dependency-free tool needs no packaging ceremony:

```
tool.py              # typed models + pure functions + thin __main__
test_tool.py         # unittest, stdlib only
```

Add packaging only when it earns its keep (third-party deps, reuse, publishing):

```
tool/
├── pyproject.toml   # uv-managed deps — only when you actually have deps
├── src/tool.py      # typed models + pure functions + thin __main__
└── tests/test_tool.py
```

### `subprocess` — the right way

```python
def run_json(name: str, *args: str, timeout: int = 30) -> object:
    proc = subprocess.run(
        [name, *args],                     # list form, never shell=True
        capture_output=True, text=True,
        check=True, timeout=timeout,       # raise on non-zero, bound the wait
    )
    return json.loads(proc.stdout)
```

Rules:

- List-form args, never `shell=True` with an interpolated string.
- `check=True` so a failure raises instead of silently continuing.
- `timeout=` so a hung child cannot wedge the run.
- Model data with `@dataclass` (stdlib) first; reach for Pydantic when you need
  real validation at the boundary.

### Dependencies: stdlib first

Lean on the standard library and internal libraries before pulling in a
third-party dependency. Every dep is a supply-chain, maintenance, and
onboarding cost. `json`, `dataclasses`, `argparse`, `subprocess`, `pathlib`,
and `unittest` cover most tool needs with zero deps.

### Testing (stdlib-first)

Prefer stdlib `unittest`, asserting on pure functions. It is zero-dep and enough
for a one-off tool. Use `pytest` when the project already uses it or when
fixtures/parametrization genuinely pay off — not by default. Keep functions free
of I/O so tests need no network or subprocess; mock the process boundary only in
integration tests.

## Where each fits

- **Go** — the default for CLIs, system tools, and services (typed, single binary,
  fast tests). Matches the paved road for platform tooling.
- **Python** — automation, data pipelines, and org/reporting glue where
  iteration speed and the data ecosystem win. Stdlib for simple tools; `uv` +
  `pyproject.toml` once you have third-party dependencies.
- **Bash** — only inside the escape hatch: ad-hoc, interactive, throwaway.
