# Why Bash/BATS Is a Weak Foundation

Bash and, by extension, BATS are a suboptimal foundation for repeatable scripts.
Piping is powerful for exploration, but the lack of testing, readability,
composition, and abstraction leads to an inherent weakness in practice.

| Weakness           | What Bash does                                                                                        | What a typed language gives                                                                    |
| ------------------ | ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| **Testing**        | BATS bolts assertions onto a shell that has no unit boundaries; you test whole scripts, not functions | Unit tests at the function level (Go `testing`, Python `unittest`/`pytest`), fast and isolated |
| **Readability**    | Dense pipe chains, `$?`, `IFS`, quoting rules, cryptic flags                                          | Named functions, types, docstrings, structured control flow                                    |
| **Composition**    | Text-on-stdin/stdout is the only interface; everything is a string                                    | Functions, packages, interfaces, dependency injection                                          |
| **Abstraction**    | No real types, modules, or contracts; global state everywhere                                         | Structs/classes, modules, typed contracts, encapsulation                                       |
| **Error handling** | `set -euo pipefail` is opt-in and leaky; failures in pipes are silent                                 | Explicit error values / exceptions, wrapped and typed                                          |
| **Data**           | Positional text you re-parse with awk/cut; breaks on spaces, locales, edge cases                      | Parsed structured data (JSON/XML) with schemas                                                 |

## Piping

Piping is powerful for exploration and fine for ad-hoc terminal work. It is
fragile as a _foundation_: no types, silent mid-pipe failures (`a | b` where `a`
fails but `b` still "succeeds"), and no place to hang a unit test.

## BATS vs a real test

A BATS test can only run the whole script and grep its stdout. It cannot:

- test a single function in isolation,
- cover edge cases cheaply (each case is a full process run),
- catch a type error, or
- run without the external commands the script invokes.

The Go/Python equivalents test the pure logic in milliseconds, independent of
the network and the process boundary. That is the difference between "it ran
once" and "it is correct."
