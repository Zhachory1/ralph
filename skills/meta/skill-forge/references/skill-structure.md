# Skill Structure Reference

## Directory Layout

```
skill-name/
  SKILL.md              # Required: frontmatter + instructions
  scripts/              # Optional: executable code agents can run
  references/           # Optional: detailed docs loaded on demand
  assets/               # Optional: templates, schemas, data files
```

## SKILL.md Frontmatter (Required)

```yaml
---
name: kebab-case-name # 1-64 chars, lowercase letters, numbers, hyphens
description: >-
  Keyword-packed description (max 1024 chars). Include: what it does,
  when to use it, trigger phrases, file patterns, error codes.
---
```

### Name Rules

- Lowercase alphanumeric and hyphens only
- No consecutive hyphens (`--`)
- Cannot start or end with hyphen
- Must match parent directory name

### Description Best Practices

- Pack with trigger keywords for auto-invocation
- Include synonyms and variations
- Mention error codes, symptoms, file patterns
- Maximum 1024 characters

## Body Structure by Skill Type

### Debugging Skills

1. **When to Use** / **Not for**
2. **Context** (language, tools, environment)
3. **Failure Classification** (categorize error types)
4. **Investigation Strategy** (systematic triage steps)
5. **Common Root Causes** (patterns with fixes)
6. **Proof Requirements** (what proves the fix works)
7. **Constraints** (DO/DO NOT)
8. **Output Format**

### Generator Skills

1. **When to Use** / **Not for**
2. **Context**
3. **Reference Pattern Analysis** (read existing code first)
4. **Prerequisites** (what must exist before generating)
5. **Generation Workflow** (step-by-step)
6. **Output Specification** (exact format of generated artifact)
7. **Validation** (how to verify correctness)
8. **Constraints**
9. **Output Format**

### Workflow Skills

1. **When to Use** / **Not for**
2. **Context**
3. **Execution Sequence** (phased steps with checkpoints)
4. **Critical Requirements** (hard blockers, non-negotiables)
5. **State Management** (what to track across steps)
6. **Known Issues and Mitigations**
7. **Constraints**
8. **Output Format**

## Quality Checklist

- [ ] Description packed with trigger keywords (max 1024 chars)
- [ ] Name uses lowercase, numbers, hyphens only (1-64 chars)
- [ ] No numbered lists (bold headers instead)
- [ ] No filler phrases ("you should", "it is important", "make sure to")
- [ ] Written for expert audience
- [ ] No duplicated content across sections
- [ ] Self-improvement section present
- [ ] Content compressed 30%+ vs first draft
- [ ] Tested with a simulated invocation

## Progressive Disclosure

- **Metadata** (~100 tokens): name + description loaded at startup
- **Instructions** (< 5000 tokens): full SKILL.md body loaded on activation
- **Resources** (as needed): scripts/, references/, assets/ loaded on demand

Keep SKILL.md under 500 lines. Move detailed reference material to separate files.
