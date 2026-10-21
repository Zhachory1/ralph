---
name: msw-storybook-validator
description: "Validate MSW handlers in Storybook by tracing component API calls to endpoints, ensuring all requests mocked. Triggers: MSW debugging, network errors, 404, handler mismatch, missing handler, storybook fetch failed, API call not mocked, request not intercepted, mock service worker."
group: storybook
---

# MSW Storybook Validator

Trace component API calls to actual service endpoints and verify all network requests are properly mocked, preventing "Cannot GET /notfound" errors and endpoint mismatches.

**Stack:** React, TypeScript, TanStack Query, MSW, Storybook.

**Not for:** writing play tests (use storybook-play-tests), general API debugging outside Storybook, backend endpoint implementation.

## The Process

### **Gather Story Context**

Read the story file and extract:

- Component import path
- MSW handlers in decorators/parameters
- Test data setup and mock factories
- Hook arguments (workspaceId, accountId, etc.)

### **Trace Component APIs**

Read the component source and identify all hooks that make network requests:

- **`useQuery`** hooks -- trace to their service method and endpoint URL
- **`useMutation`** hooks -- trace to POST/PUT/DELETE endpoints
- **Custom hooks** -- follow the chain: hook -> service method -> HTTP call -> URL

For each hook, determine:

- The HTTP method (GET, POST, PUT, DELETE)
- The full endpoint URL with path parameters
- Which path params are used (`:workspaceId`, `:accountId`, etc.)

### **Analyze MSW Coverage**

Parse the story's MSW handlers and compare against discovered API calls:

- Extract endpoints from `http.get()`, `http.post()`, etc.
- Map each handler to a component API call
- Identify mismatches:
  - **Wrong path**: `/api/accounts/:accountId/models` vs `/api/workspaces/:workspaceId/data/models`
  - **Wrong params**: `:accountId` vs `:workspaceId`
  - **Missing handlers**: Component calls with no corresponding mock
  - **Orphan handlers**: Mocks for endpoints the component does not call

## Common Mismatch Patterns

**Path parameter mismatches**: Component uses `:workspaceId`, handler uses
`:accountId`. Grep the service file to confirm the correct parameter.

**Missing nested routes**: Component calls `/data/models`, handler has `/models`.
Trace the full path from the service method.

**Query hooks without service**: `useQuery(['key'])` may inline the fetch call.
Check the component for a `queryFn` definition.

**Multiple services**: Component imports from several service files. Trace each
import separately.

**Dynamic endpoints**: Template literals like `` `/api/workspaces/${id}/connections` ``.
Extract the URL pattern with path parameters.

**DTO format mismatches**: MSW returns camelCase but the transformation layer
expects snake_case DTO format. Check the transformation layer for required fields.

## Constraints

- NEVER assume handler correctness without tracing the full call chain: component -> hook -> service -> endpoint
- NEVER skip path parameter validation
- NEVER ignore orphan handlers -- they indicate stale mocks
- ALWAYS verify HTTP methods match (GET vs POST)
- ALWAYS check DTO/response format expectations
- ALWAYS provide drop-in replacement handler code for every mismatch or missing handler

## Output Format

Produce a structured coverage report with this exact structure:

```
Component APIs:
- GET /api/workspaces/:workspaceId/data/models
- GET /api/workspaces/:workspaceId/connections

Story MSW handlers:
[pass] GET /api/workspaces/:workspaceId/data/models
[FAIL] GET /api/accounts/:accountId/connections (WRONG PARAM)

Coverage: 1/2 (50%)
Status: FAIL

Fixes:
http.get('/api/workspaces/:workspaceId/connections', () =>
  HttpResponse.json(mockConnections)
)
```

The report MUST contain all five sections:

1. **Component APIs** -- every API call with HTTP method and full URL
2. **Story MSW handlers** -- each handler with `[pass]` or `[FAIL]` and failure reason
3. **Coverage** -- fraction and percentage of requests with correct mocks
4. **Status** -- `PASS` (100% coverage, no mismatches) or `FAIL`
5. **Fixes** -- drop-in replacement handler code for every failed or missing handler; omit this section only when Status is PASS
