---
name: debug-api
description: "Debug HTTP API/network failures: CORS, preflight, 401, 403, expired tokens, 404, 500, fetch failed, connection refused, timeout, TanStack Query refetch, stale data, DTO mismatch, MSW divergence. Triggers: API error, network failure, CORS, axios error, request failed."
group: debugging
---

# Debug API

Systematically trace HTTP/API failures through the network layer, service layer, and client-side query cache. Approach: inspect actual network traffic first, then work outward.

**API Architecture:** TanStack Query in `custom-hooks/queries/`, query options in `options/`, services in `services/`. MSW for API mocks. Components never call services directly.

**Not for:** Jest failures (debug-jest), Playwright failures (debug-playwright), build errors (debug-build), dev environment issues (dev-monitor).

## Failure Classification

| Category                 | Key Signals                                                             |
| ------------------------ | ----------------------------------------------------------------------- |
| **Request Never Sent**   | `enabled: false`, query key mismatch, hook not invoked, network offline |
| **Request Fails**        | CORS 403/404, Auth 401/403, 404 URL wrong, 500 backend error, timeout   |
| **Response Wrong**       | DTO mismatch, null/undefined fields, type error, empty array            |
| **TanStack Query Stuck** | Infinite refetch, stale data, cache not invalidating, loading forever   |

> **Deep dive**: See `references/api-error-patterns.md` for complete patterns and root causes.

## Investigation Strategy

**Inspect Network Traffic**

Examine the actual request and response in browser DevTools or equivalent:

- Request URL (exact match, check trailing slashes)
- Request headers (Authorization, Content-Type)
- Request payload (JSON format, required fields)
- Response status code (200/400/401/403/404/500)
- Response body (DTO shape, error messages)
- Timing (slow response or timeout?)

**Check TanStack Query State**

Inspect TanStack Query behavior via React DevTools TanStack Query plugin or by logging query state directly in the component:

- `data`, `isLoading`, `isError`, `error`, `fetchStatus`, `status`
- Verify the query key is stable across renders (log the key in a `useEffect`)

**Verify Service Layer**

Check the service method responsible for the request:

- Endpoint URL constructed correctly?
- Headers (especially auth token) attached properly?
- DTO transformation correct?
- Error handling present and correct?

**Compare MSW vs Real API**

If MSW mocks work but the real API fails:

- Compare handler URL with the real endpoint
- Compare handler response shape with the real DTO
- Check whether auth is bypassed in the mock
- Look for hardcoded test data in the mock handler

**Validate the Fix**

After applying a fix:

- Reproduce the original failure scenario to confirm resolution
- Test under network throttling to catch timing edge cases
- Confirm the fix works across 3+ page refreshes

## Common Root Causes

CORS configuration, auth token handling, TanStack Query lifecycle, and DTO contract drift. See `references/api-error-patterns.md` for the complete root cause catalog with detailed patterns for each category.

## Constraints

- **DO NOT** bypass CORS by disabling browser security
- **DO NOT** hardcode auth tokens or API URLs
- **DO NOT** ignore DTO mismatches by casting to `any`
- **DO NOT** suppress network errors without logging them
- **DO** inspect actual network traffic before making assumptions
- **DO** verify both MSW mock and real API responses match
- **DO** check query key stability when debugging TanStack Query
- **DO** test fixes under throttled network conditions
- **DO** confirm the fix works across 3+ page refreshes

## Companion Files

- **`references/api-error-patterns.md`** -- Complete error pattern classification and root cause catalog

## Output Format

Structured debugging report:

- **Failure type**: Classification (request not sent, request fails, response wrong, query stuck)
- **Root cause**: One sentence explaining the failure
- **Network evidence**: Status code, headers, response body observations
- **Fix applied**: What was changed and why
- **Verification**: Confirmation the fix works consistently (3+ refreshes)
- **Contract notes**: Any DTO or API contract changes that need backend communication
