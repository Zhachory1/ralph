# Bash Anti-Patterns and Their Replacements

Each pattern below is something Bash makes easy and dangerous. The replacement
moves the logic into a typed, tested function.

## 1. Parsing columns with awk/cut

**Bash:**

```bash
kubectl get pods | awk '{print $1, $3}'   # breaks on wide output, headers, spaces
```

**Replacement:** request JSON and parse into typed values.

```bash
kubectl get pods -o json   # then json.Unmarshal / json.loads into structs
```

Positional/whitespace parsing breaks on names with spaces, changed column order,
locale differences, and header rows. Structured data does not.

## 2. Building commands from interpolated strings

**Bash:**

```bash
eval "curl -H \"Authorization: Bearer $TOKEN\" $URL/$ID"   # injection + quoting hell
```

**Replacement:** explicit arg vectors (`exec.CommandContext(ctx, "curl", "-H", ...)`
or `subprocess.run(["curl", "-H", ...])`). No string concatenation, no `eval`.

## 3. Silent pipe failures

**Bash:**

```bash
curl -s "$URL" | jq '.items' > out.json   # curl fails, jq gets empty, out.json is "null"
```

`set -euo pipefail` helps but is opt-in, easy to forget, and `pipefail` still
loses intermediate context. **Replacement:** check the exit code of each call and
wrap the error with context. A failed fetch becomes a returned error, not a
corrupt output file.

## 4. csv/tab-delimited output as an interface

**Bash:**

```bash
echo "$name,$region,$cpu" >> report.csv   # commas/tabs in a field corrupt the row
```

**Replacement:** emit JSON. Downstream consumers parse a schema instead of
guessing delimiters, and fields containing separators are safe.

## 5. BATS as the test strategy

BATS runs the whole script and inspects stdout. It cannot:

- test a single function in isolation,
- cover edge cases cheaply (each case is a full process run),
- catch type errors, or
- run without the external commands the script invokes.

**Replacement:** put logic in pure functions and test them directly — Go
table-driven tests or `pytest`. The process boundary is tested separately and
sparingly.

## 6. Control flow and state in shell

**Bash:** nested `if`/`case`, `IFS` juggling, global variables, subshell scoping
surprises, arithmetic quirks (`[[ ]]` vs `[ ]` vs `(( ))`).

**Replacement:** real control flow, typed locals, functions, and modules. The
logic becomes readable and reviewable, and the intent is expressed in types.

## Quick reference

| Bash reflex                       | Typed replacement                         |
| --------------------------------- | ----------------------------------------- |
| `\| awk '{print $N}'`             | JSON output + struct/dataclass            |
| `eval` / string-built commands    | explicit arg vector                       |
| `\| jq ... > file` with no checks | checked exit codes + wrapped errors       |
| `>> file.csv`                     | `json.NewEncoder` / `json.dumps`          |
| BATS asserting on stdout          | table-driven tests / pytest on pure funcs |
| `set -euo pipefail` as safety net | error values / exceptions by construction |
