---
name: bash-last-policy
description: Prefer Go or Python over committed Bash for tools, CLIs, pipelines, and tested automation. Bash remains suitable for short, throwaway terminal commands.
group: core-workflow
---

# Bash Last

Use shell for ad-hoc interactive commands. Use a real language when logic is committed, shared, repeated, parsed, branched, or tested.

## Ladder

1. Existing repo helper or native build-tool feature
2. Go for CLIs, system tools, services, and process orchestration
3. Python for automation, data work, and glue
4. Bash only for short command invocation with no real logic

Do not replace a harmless one-line CI command with a program. Move to Go/Python when shell starts handling arguments, control flow, aggregation, retries, or structured data.

## Process Safety

- Request JSON/XML output; do not scrape display columns.
- Pass subprocess arguments as arrays, never interpolated shell strings.
- Check exit status, capture stderr, and apply timeouts/cancellation.
- Use stdlib first; add dependencies only when they earn their cost.
- Test domain logic, not shell syntax.

For safe `os/exec` and `subprocess` patterns, read [language-ladder.md](references/language-ladder.md). Consult `standards` for repo-wide testing and paved-road rules.
