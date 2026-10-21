---
name: kickstart
description: "Initialize coding session from handoff doc: restore file context, verify env, check git state, present action plan. Triggers: kickstart, resume session, load handoff, restore context, continue from handoff, pick up handoff, session bootstrap, restart from handoff doc."
group: workflow
---

# Kickstart

Restore working context from a handoff document: load the file, verify environment state (directory, branch), load context files, and present goal and next steps.

**Not for:** fresh work with no prior context, reading single files, general codebase onboarding (use getting-started). Companion to the `handoff` skill — handoff creates, kickstart consumes.

## The Process

### Find Handoff File

**If a path is provided:** Use that file directly.

**If no path is provided:** Find the most recent handoff file in the current
working directory:

```bash
ls -t handoff-*.md 2>/dev/null | head -1
```

If no handoff file is found, report an error and suggest checking the filename
or listing available handoff files.

### Parse Handoff Document

Read the handoff file and extract these sections:

- **Prompt**: The goal/task for this session
- **Instructions**: Context bullet points
- **Relevant Files**: File paths to load
- **Git Context**: Branch, uncommitted files, recent commits
- **Working Directory**: Expected working directory

If required sections (Prompt, Relevant Files) are missing, warn but proceed
with available information.

### Verify Environment

Run these checks in order:

**Working Directory Check:**

- Compare current working directory with the handoff's Working Directory
- If different, warn and suggest the `cd` command to correct it

**Git Branch Check:**

- Run `git branch --show-current`
- Compare with the handoff's branch
- If different, warn and suggest the `git checkout` command

**Uncommitted Changes Check:**

- Run `git status --porcelain | wc -l`
- If count differs by more than 5 from the handoff, note it (informational only)

All checks are warnings only -- the user decides whether to act on mismatches.

### Load Context Files

For each file listed in the Relevant Files section:

- Read the file to load it into context
- If a file exceeds 500 lines, read the first 200 lines and note truncation
- If a file does not exist, log a warning and continue with remaining files

Report a loading summary: "Loaded X/Y files (Z not found: ...)"

### Present Instructions

Display the Instructions section as context points: what was being worked on,
key decisions, constraints, and blockers.

### Confirm Ready

Output the initialization summary (see Output Format below). Include any
warnings (directory mismatch, branch mismatch, missing files) before the
"Ready to begin" line.

## Output Format

```
Session initialized from <handoff-filename>

Goal: <extracted goal>

Context loaded:
- <N> instruction points
- <X>/<Y> files loaded

Environment:
- Working directory: <match/mismatch>
- Git branch: <branch name>

[Any warnings about mismatches or missing files]

Ready to begin. What is the first step?
```

## Error Handling

**Handoff file not found:**

```
Handoff file not found: <filename>

Try:
- Check the filename spelling
- List available files: ls handoff-*.md
- Provide no path to auto-detect the most recent handoff
```

**Files no longer exist:**

```
Warning: Some files from handoff no longer exist:
- <filepath> (not found)

Loaded X of Y files successfully.
```

## Constraints

- **DO** verify environment (working directory, git branch) before loading files
- **DO** warn on mismatches but proceed -- let the user decide
- **DO** load all listed files into context for maximum continuity
- **DO** truncate files over 500 lines to first 200 lines
- **DO NOT** fail silently on missing files -- always report what could not load
- **DO NOT** automatically switch branches or directories -- only suggest commands
- **DO NOT** skip environment verification
