---
name: skill-forge
description: "End-to-end skill creation. Generate Claude Code skills with keyword-packed descriptions, pattern-matched templates (debugging/generator/workflow), compression, mandatory testing, quality validation. Triggers: create skill, new skill, generate skill, validate skill, scaffold SKILL.md."
group: orchestration
---

# Skill Forge

Build high-quality AI coding agent skills through a complete pipeline: gather requirements, analyze patterns, structure content, compress, validate, and test. Every skill produced is concise, keyword-rich, and tested before delivery.

**Not for:** creating full agent definitions (use agent-forge), one-off task execution, general documentation writing.

## Skill Creation Pipeline

### Gather Context

Ask 2-5 strategic questions before building:

**Problem and Solution**: "What problem does this skill solve and what outcome
does it produce?" Focus on core value, not implementation.

**Trigger Strategy**: "When should this skill activate?" Determine whether it
needs liberal keyword matching, targeted patterns, or manual invocation only.

**Success Evidence**: "What proves this skill succeeded?" Identify observable
outcomes and measurable completion criteria.

**Dependencies**: "What tools, external resources, or other skills does this
need?" Map coordination requirements.

**Skill Type**: "Is this a debugging, generator, or workflow skill?" This
determines which structural template to follow.

### Analyze Existing Patterns

Read similar skills in the codebase to identify:

- Structural conventions (section ordering, heading style)
- Description patterns (keyword density, trigger phrases)
- Content density expectations

### Structure the Skill

Follow the appropriate template for the skill type:

**Purpose** (2-3 sentences): Problem solved, when to invoke, why it exists.

**Core Principles** (3-7 bold statements): Fundamental truths, decision guides,
quality standards.

**Execution** (bold headers, not numbered lists): Workflow steps, decision
points, tool usage, state transitions.

**Common Patterns**: What works, what fails, solutions to recurring problems.

**Success Criteria**: Completion checklist, evidence requirements, quality gates.

**Self-Improvement**: Section where the skill appends discoveries and pattern
refinements during use.

### Write the Description

The `description` field drives discoverability. Pack it with:

- What the skill does and when to use it
- Synonyms and variations of trigger phrases
- Error codes, symptoms, and file patterns
- Maximum 1024 characters (Agent Skills Specification limit)

### Compress Content

- Remove filler phrases: "you should", "it is important", "make sure to"
- Convert numbered lists to bold headers
- Target 30% or greater word reduction vs. first draft without meaning loss
- Keep examples only when the pattern is non-obvious
- Do NOT compress the description--keep it keyword-rich

### Validate Quality

Check these gates before delivery:

- Description packed with trigger keywords (max 1024 chars)
- Name uses lowercase, numbers, and hyphens only (1-64 chars)
- No numbered lists (bold headers instead)
- No filler phrases
- Written for expert audience
- No duplicated content across sections
- Self-improvement section present

### Test the Skill

Approach depends on skill type:

**Debugging skills**: Create a simulated error scenario, invoke the skill,
verify it triages correctly and produces evidence.

**Generator skills**: Request a minimal artifact, invoke the skill, verify the
output matches expected patterns and sections.

**Workflow skills**: Execute the first step, invoke the skill, verify it
initiates the correct process and tracks progress.

Test failure means rewrite and retest.

## Skill Type Templates

**Debugging**: Failure classification, investigation strategy, common root
causes, proof requirements.

**Generator**: Reference pattern analysis, prerequisites, generation workflow,
output specification, validation.

**Workflow**: Execution sequence, critical requirements, state management,
known issues and mitigations.

## Companion Files

- [references/skill-structure.md](references/skill-structure.md) --
  Complete skill directory layout, frontmatter rules, body structure per skill
  type (debugging, generator, workflow), quality checklist, and progressive
  disclosure guidelines

## Atomicity Constraint

Every skill must be self-contained within its own directory. Skills must not
reference `setup/standards/` paths or assume installer state.

- If a skill requires your organisation's engineering standards (coding, testing,
  architecture, observability, terminology), add: _"Consult the `standards` skill for
  engineering conventions."_
- If a skill requires domain-specific knowledge (e.g., test patterns, diagram
  templates), embed it in the skill's own `references/` subdirectory.
- Never link to `~/.<ide>/standards/`, `setup/standards/`, or any path outside
  the skill's own directory.

## Constraints

- **DO NOT** deliver a skill without testing it first
- **DO NOT** use numbered lists in skill content (bold headers only)
- **DO NOT** include filler phrases or fluff
- **DO NOT** duplicate content across sections
- **DO NOT** reference external standards paths — use the `standards` skill
- **DO** pack the description with trigger keywords
- **DO** compress content by 30% or more vs. first draft
- **DO** match the appropriate template for the skill type
- **DO** include success criteria and a self-improvement section
- **DO** test every skill before marking it complete

## Output Format

Deliver:

```
Skill forged: {name}
Location: {path}
Description: {char_count} chars, {keyword_count} triggers
Compression: {original_words} -> {final_words} ({percentage}%)
Pattern: {template_type}
Test: {scenario} -> PASS

Try: {example_invocation}
```

Plus the complete skill file ready for placement in the skills directory.
