---
name: handoff
description: "Generate session context transfer doc preserving working state, decisions, ranked file refs, next steps. Markdown: goal, instructions, file context, git state. Triggers: handoff, session transfer, save context, preserve session, hand off work, write handoff, end of session summary."
group: workflow
---

# Handoff

Input: a goal or task description. Output: a markdown handoff document containing the prompt, instructions, ranked file references, and git context needed to resume work in a new session.

**Not for:** simple file bookmarking, sharing a snippet, documenting completed work (use doc-writer).

## The Process

### Phase 1: Parse Goal

Extract the goal from user input. This becomes the primary prompt for the
receiving session.

### Phase 2: Scan Session for Files

Review the current conversation history for all file mentions:

- Files that were read
- Files that were edited
- Files found in search results
- Files mentioned in conversation

### Phase 3: Rank Files by Relevance

Score each file on three weighted dimensions. Final score =
`(recency * 0.4) + (frequency * 0.3) + (goal_alignment * 0.3)`.

**Recency (40%):** Files mentioned in recent conversation turns rank higher.

- Last 5 turns: 1.0
- Last 10 turns: 0.7
- Earlier: 0.4

**Frequency (30%):** Files mentioned multiple times rank higher.

- 3+ mentions: 1.0
- 2 mentions: 0.7
- 1 mention: 0.4

**Goal Alignment (30%):** File path/name matches goal keywords.

- Strong match: 1.0
- Partial match: 0.5
- No match: 0.1

### Phase 4: Select Top Files

Choose the top 5-10 files based on ranking. Prioritize:

- Files directly related to the goal
- Files that were actively edited
- Entry points and key interfaces

Exclude:

- Generated files, node_modules, build artifacts
- Files only tangentially mentioned
- Files over 1000 lines (note size if essential)

### Phase 5: Extract Instructions

Summarize relevant context as 3-10 bullet points focusing on:

- What was being worked on
- Key decisions made and why
- Constraints or requirements
- Blockers or issues discovered
- Architecture or design context

Emphasize "why" context (decisions, constraints, blockers), not just "what."

### Phase 6: Capture Git Context

```bash
git branch --show-current          # Current branch
git status --porcelain | wc -l     # Uncommitted file count
git log --oneline -3               # Recent commits
```

If no git repository is present, omit this section.

### Phase 7: Generate Handoff Document

Create `handoff-<task-name>-<YYYYMMDD>.md` in the current working directory.

Generate the date with: `date +%Y%m%d`

Use kebab-case for the task name (max 30 characters). If a file with the same
name exists, append a sequence number: `-2`, `-3`, etc.

## Handoff Document Format

```markdown
# Handoff: <goal>

<!-- Handoff Format: v1 -->
<!-- Created: <ISO timestamp> -->
<!-- Project: <absolute path to working directory> -->

## Prompt

<goal restated as clear instruction for the receiving session>

## Instructions

- Key context point 1
- Key context point 2
- Important constraint or decision
- ...

## Relevant Files

<list of top-ranked file paths, one per line>

## Git Context

Branch: <current branch>
Uncommitted: <count> files
Recent commits:

- <commit subject 1>
- <commit subject 2>
- <commit subject 3>

## Working Directory

<absolute path to current working directory>
```

## Post-Generation

After writing the handoff document:

- **Confirmation**: Display the file path
- **Summary**: Report file count, instruction count, and git state captured
- **Next steps**: Instruct the user to open a new AI coding session and provide
  the handoff file as initial context

## Companion Files

- [assets/handoff-template.md](assets/handoff-template.md) --
  Copy-and-fill handoff document template with all required sections

## Constraints

- **DO** capture "why" context (decisions, constraints, blockers), not just "what"
- **DO** rank files by relevance before including them
- **DO** keep the file list focused (5-10 files, hard cap at 10)
- **DO** include git context (branch, uncommitted changes, recent commits)
- **DO** use kebab-case task names with date suffix for filenames
- **DO NOT** include generated files, build artifacts, or node_modules
- **DO NOT** write vague instructions--be specific about decisions and blockers
- **DO NOT** skip the file ranking step

## Output Format

A markdown file named `handoff-<task-name>-<YYYYMMDD>.md` written to the current
working directory, structured per the Handoff Document Format template above:
Prompt, Instructions (3-10 bullets), Relevant Files (5-10 ranked paths), Git
Context, and Working Directory.

Followed by a confirmation message with the file path and a summary of what was
captured.
