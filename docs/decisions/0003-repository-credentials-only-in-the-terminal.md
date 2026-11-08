# 0003: Repository credentials reach agents only through the terminal tool

- **Status:** accepted
- **Date:** 2026-11-07
- **Issue:** [#24](https://github.com/Zhachory1/ralph/issues/24)

## Decision

Agents run `git` and `gh` in the `terminal` tool, which forwards the repository credential into the sandbox (`terminal.docker_forward_env`). `execute_code` is not given `GH_TOKEN`, and Ralph does not patch Hermes to change that.

## Context

`execute_code` strips any environment variable whose name contains `TOKEN`, `KEY` or `SECRET`, and its escape hatch (`terminal.env_passthrough`) refuses a fixed list that includes `GH_TOKEN`. Hermes added that list for GHSA-rhgp-j443-p4rf, where a skill registered itself to receive a model key in the `execute_code` child. Whether `GH_TOKEN` belongs on that list is Hermes' call.

Since #76 every profile's terminal runs in the Docker sandbox and receives `GH_TOKEN` by name. Profiles that need GitHub already have `terminal`, so nothing requires the token inside `execute_code`.

## Consequences

- The shared principles tell every profile to use the terminal for `git` and `gh`. The builder, author and researcher SOULs already say so.
- `execute_code` stays credential-free. A script that needs GitHub calls `gh` through the terminal tool.
- If Hermes narrows its blocklist upstream, this decision can be revisited; nothing in Ralph depends on the scrub.
