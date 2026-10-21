---
name: dev-monitor
description: "Detect/diagnose dev env issues: webpack not starting, dev server stuck, storybook broken, port conflict, EADDRINUSE, zombie process, service crashed, health check failed, connection refused. Triggers: dev server broken, port conflict, service not starting, kill port."
group: debugging
---

# Dev Monitor

Detect and diagnose dev environment issues: read state and logs first, identify root cause, propose the minimal fix.

**Dev environment:** Multiple concurrent services — webpack dev server, Storybook, and backend services each bind specific ports.

**Not for:** Jest/Playwright failures (use debug-jest/debug-playwright), TypeScript errors (use debug-build), API debugging (use debug-api), production issues (escalate to on-call).

## Diagnostic Capabilities

**Health Diagnostics**

- Check if ports are both bound AND responding to requests
- Identify zombie processes (port bound, service unresponsive)
- Verify service health endpoints return expected responses
- Cross-reference process table with expected services

**Port Conflict Detection**

- Detect EADDRINUSE errors and identify which process holds the port
- Identify conflicts between multiple worktrees or dev environments
- Map processes to their bound ports

**Log Analysis**

- Read the last 20 lines of service logs first; expand to 100 lines if the root cause is not visible
- Extract ERROR, WARN, and EADDRINUSE patterns
- Correlate log errors with service health status

**Performance Insights**

- Identify services consistently slow to start
- Detect resource contention (high CPU, memory pressure)
- Suggest targeted optimizations (clear caches, rebuild, etc.)

## Diagnosis Patterns

**Zombie Process**

- **Symptom**: Port bound but service not responding to requests
- **Diagnosis**: Process crashed but did not release the port
- **Fix**: Kill the zombie process by PID, then restart the service

**Port Conflict**

- **Symptom**: EADDRINUSE error when starting a service
- **Diagnosis**: Another process or worktree is using the same port
- **Fix**: Identify the conflicting process, stop it or use a different port

**Service Crash Loop**

- **Symptom**: Service repeatedly fails shortly after starting
- **Diagnosis**: Configuration error, missing dependency, or corrupt state
- **Fix**: Check config files (webpack.config.js, etc.), verify node_modules, check package.json

**Missing Dependencies**

- **Symptom**: Module not found errors on service startup
- **Diagnosis**: node_modules out of date or corrupted
- **Fix**: Run `yarn install` or clear node_modules and reinstall

**Stale Build Artifacts**

- **Symptom**: Service starts but serves outdated or broken content
- **Diagnosis**: Build cache is stale or corrupted
- **Fix**: Clear build output directories and rebuild

## Investigation Flow

**Gather State**
Check running processes, port bindings, and recent logs.

**Diagnose**
Identify the root cause: zombie, port conflict, crash loop, missing dependency, or stale artifact.

**Propose Fix**
Recommend the minimal, surgical command to resolve the issue.

**Verify**
After the fix is applied, re-check that the service is healthy.

**Prevent**
If the issue is recurring, suggest a preventive measure.

## Constraints

- **DO NOT** kill processes in other worktrees or environments without explicit approval
- **DO NOT** run `rm -rf node_modules` as a first resort--diagnose first
- **DO NOT** modify service configuration without understanding the current state
- **DO NOT** assume a full environment restart is needed--prefer surgical fixes
- **DO** read logs and process state before proposing any fix
- **DO** prefer killing specific PIDs over broad `killall` commands
- **DO** verify the service is healthy after applying a fix
- **DO** report findings even if no immediate fix is available

## Output Format

Structured diagnostic report:

- **Issue Summary**: One sentence describing the problem
- **Root Cause**: Classification (zombie process, port conflict, crash loop, missing dep, stale artifact)
- **Evidence**: Process state, port bindings, or log excerpts confirming the diagnosis
- **Fix Command**: The specific command(s) to resolve the issue
- **Verification**: Command or check to confirm the service is healthy after the fix
- **Prevention**: Suggestion to avoid recurrence (if applicable)
