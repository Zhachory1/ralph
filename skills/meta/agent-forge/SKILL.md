---
name: agent-forge
description: "End-to-end agent creation. Single-purpose AI coding agents: single responsibility, minimal tools, explicit context, structured output, mandatory testing. Triggers: create agent, new agent, generate agent, sub-agent, specialized agent, agent design, agent prompt, scaffold agent."
group: orchestration
---

# Agent Forge

Create focused, single-purpose AI coding agents through a complete pipeline: define role, scope boundaries, specify tools, write detailed prompts, test, and deploy. Every agent has one clear responsibility and minimal permissions.

**Not for:** creating skills (use skill-forge), one-off task execution, general prompt writing without agent structure.

## Agent Creation Pipeline

### Gather Context

Ask 3-6 strategic questions before designing. If the user has already provided
sufficient detail, state your assumptions and proceed.

**Single Responsibility**: "What is this agent's ONE specific responsibility?"

**Scope Boundaries**: "What should this agent NOT do?"

**Required Context**: "What information must be provided in every invocation?"
Agents start with a clean slate each time.

**Output Format**: "What should this agent output and in what structure?"

**Tool Access**: "What tools are absolutely necessary?" Start from the most
restrictive set and justify additions.

**Task Complexity**: "Does this require multi-step orchestration or single-pass
execution?" Multi-step agents need explicit phase transitions and intermediate
validation; single-pass agents need strict input/output contracts.

### Design the Agent

**Role Definition**: One sentence -- "You are X. Your singular purpose: Y." Not
"general assistant" but "code security auditor" or "test generator for React
components."

**Scope Boundaries**: What it DOES (specific actions) and DOESN'T (out of scope).
Example: "You analyze security vulnerabilities. You do NOT fix them."

**Tool Access** (minimal principle):

- Read-only analysis: Read, Search, Find
- Code generation: Read, Write, Find
- Git workflows: Read, Write, Terminal, Find, Search
- Full access: All tools (rare, justify thoroughly)

### Write the Prompt

Structure every agent prompt with these sections:

- **Your Role**: Clear one-sentence definition
- **Your Mission**: Specific task with clear boundaries
- **Context Requirements**: What must be provided each invocation (no memory between runs)
- **Scope**: IN SCOPE / OUT OF SCOPE
- **Your Process**: Bold headers -- Gather Context, Analyze, Execute, Validate, Report
- **Constraints**: What NOT to do, tool limitations, quality requirements
- **Output Format**: Structured output specification with concrete example
- **Success Criteria**: Measurable conditions that prove completion

### Compress and Validate

- Remove filler: "you should", "it is important", "please"
- Use bold headers instead of numbered lists; target 30%+ word reduction
- Keep examples only when non-obvious; maintain explicit instructions
- Validate against checklist:
  - Single responsibility (one purpose, stated in first sentence)
  - Minimal tools (fewest required, each justified)
  - Structured output (format defined with example)
  - Measurable success criteria (pass/fail, not subjective)
  - Context requirements specified (no assumed prior knowledge)

### Test the Agent

Testing is mandatory before delivery. Approach depends on agent type:

**Analysis agents**: Provide sample input, verify output format and completeness.

**Generator agents**: Request minimal artifact, verify correct output and patterns.

**Workflow agents**: Execute first step, verify process initiation and tracking.

Test failure means rewrite and test again. No exceptions.

## Agent Type Patterns

**Analysis Agents** (read-only tools):
Code reviewers, security auditors, performance analyzers, pattern detectors.

**Generator Agents** (read + write tools):
Test creators, code scaffolders, documentation generators, configuration builders.

**Workflow Agents** (broad tool access):
Multi-step orchestrators, CI/CD coordinators, deployment managers, migration executors.

## Agent Template

> **Template file**: See `assets/agent-template.md` for the full starter template with placeholders.

Structure every agent prompt with these sections: Your Role, Your Mission, Context Requirements, Scope (IN/OUT), Your Process (bold headers), Constraints, Output Format, Success Criteria.

## Companion Files

- **`assets/agent-template.md`** — Starter template for new agent definitions

## Atomicity Constraint

Every agent must be self-contained. Agents must not reference `setup/standards/`
paths or assume installer state.

- If an agent requires Rokt engineering standards, add: _"Consult the `standards`
  skill for Rokt engineering conventions."_
- If an agent requires domain-specific knowledge, embed it in the agent's own
  `assets/` subdirectory.
- Never link to `~/.<ide>/standards/`, `setup/standards/`, or any path outside
  the agent's own directory.

## Constraints

- **DO NOT** create agents with vague or multi-purpose responsibilities
- **DO NOT** grant more tool access than strictly necessary
- **DO NOT** deliver an agent without testing it first
- **DO NOT** use numbered lists in agent prompts (bold headers only)
- **DO NOT** omit context requirements -- agents have no memory between runs
- **DO NOT** reference external standards paths — use the `standards` skill
- **DO** define a single, narrow responsibility for every agent
- **DO** start with the most restrictive tool set and justify additions
- **DO** include explicit context requirements in every agent prompt
- **DO** specify structured output format
- **DO** test every agent before marking it complete

## Output Format

Deliver:

```
Agent forged: {name}
Type: {role-type}
Responsibility: {single-purpose}
Tools: {tool-list}
Context requirements: {required-context-list}
Output format: {structured-format}
Test: {scenario} -> PASS

Example invocation:
[Task description with ALL required context provided inline]
```

Plus the complete agent definition file ready for deployment.
