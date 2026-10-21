---
name: worktree
description: "Manage git worktrees as sibling dirs for parallel branch dev. Create/list/switch/remove at <parent>/<branch-name>. Triggers: git worktree, parallel branches, worktree create, worktree list, worktree remove, sibling worktree, switch worktree, isolate branch, multiple branches, hot-fix."
---

# Git Worktree Management

Manage git worktrees as sibling directories at the same level as the current repository.

## Worktree Structure

**Important**: Always work relative to Claude's current working directory.

- Current repository: wherever `git rev-parse --show-toplevel` points from the current working directory
- Parent directory: `dirname "$(git rev-parse --show-toplevel)"`
- Worktrees are created at: `<parent-directory>/<branch-name>`

Example structure:

```
/path/to/parent/
├── my-repo/           (current working directory - could be main or any branch)
├── TICKET-123/        (new worktree)
└── TICKET-456/        (another worktree)
```

This allows the skill to work in any git repository, on any machine, and branch off from any current branch.

## Core Responsibilities

### 1. Creating Worktrees from Jira Tickets

When a user provides a Jira ticket (in any format), you should:

1. **Extract the Jira ticket ID** from various formats:
   - Just the ID: "IDENTITY-871"
   - URL format: "https://<your-tracker>/browse/<TICKET-ID>"
   - With description: "IDENTITY-871: Fix the bug"

2. **Optionally fetch the Jira issue summary** (if the user wants a descriptive branch name):
   - Use the Atlassian MCP tools to get issue details
   - Sanitize the summary for use in branch names (lowercase, hyphens, remove special chars)
   - Create branch name: `<ticket-id>-<sanitized-summary>`

3. **Determine the base branch**:
   - By default, create from current branch
   - If user wants to start from a specific branch: `git worktree add -b <branch-name> <worktree-path> <base-branch>`
   - Ask user if they want to start from main/master or current branch if unclear

4. **Create the worktree**:
   - Get the repository root: `git rev-parse --show-toplevel`
   - Determine the parent directory: `dirname "$(git rev-parse --show-toplevel)"`
   - Create worktree path: `<parent>/<branch-name>`
   - Run: `git worktree add -b <branch-name> <worktree-path> <base-branch>` (for new branch from specific base)
   - Or run: `git worktree add -b <branch-name> <worktree-path>` (for new branch from current)
   - Or run: `git worktree add <worktree-path> <branch-name>` (for existing branch)

5. **Confirmation**:
   - Display the created worktree location (relative path is fine)
   - Show how to navigate to it: `cd <worktree-path>`
   - Note: The new worktree will be created as a sibling to the current repository

### 2. Listing Worktrees

When asked to list worktrees:

- Run: `git worktree list`
- Format output in a readable way with branch names and paths

### 3. Removing Worktrees

When asked to remove a worktree:

1. Verify the worktree exists
2. Check for uncommitted changes (warn user)
3. Run: `git worktree remove <path>`
4. Optionally delete the branch: `git branch -D <branch-name>`

### 4. Cleaning Up Stale Worktrees

When asked to clean up:

- Run: `git worktree prune`
- Remove any stale directory references

## Important Notes

- Always determine paths dynamically - never hardcode repository paths
- The new branch will be created from the current branch (whatever branch the user is currently on)
- If user wants to branch from a specific branch (e.g., main), ask them or they can specify
- Check if a worktree or branch already exists before creating
- Validate that we're in a git repository before proceeding
- Keep branch names clean and consistent (lowercase, hyphens, no special characters)
- After creating a worktree, show the user the relative path to navigate to it
- This workflow is portable - it works on any machine and any git repository

## Commands Reference

```bash
# List worktrees
git worktree list

# Add worktree with new branch
git worktree add -b <branch> <path>

# Add worktree with existing branch
git worktree add <path> <branch>

# Remove worktree
git worktree remove <path>

# Prune stale worktrees
git worktree prune

# Get repo root
git rev-parse --show-toplevel

# Get parent directory
dirname "$(git rev-parse --show-toplevel)"
```

## Error Handling

- If not in a git repo: inform user and ask them to navigate to the repo
- If branch exists: ask if they want to use existing branch or choose a different name
- If worktree path exists: inform user and ask for alternative name
- If Jira fetch fails: proceed with just the ticket ID

## Atlassian Integration

When fetching Jira issue details:

1. Get cloudId first: use `getAccessibleAtlassianResources`
2. Get issue: use `getJiraIssue` with the ticket ID
3. Extract summary from the response
4. Sanitize for branch name: lowercase, replace spaces with hyphens, remove special characters

Always ask the user if they want to fetch the Jira summary before making API calls, as they may want a simple branch name or may have custom naming preferences.
