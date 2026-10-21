# API Error Patterns Reference

Reference companion to `debug-api/SKILL.md`. Quick-reference for classifying API and network failures and identifying root causes.

## Failure Classification

### Request Never Sent

- Query `enabled: false` -- conditional fetch is blocked
- Query key mismatch -- wrong cache entry being read
- Service method not called -- hook not invoked
- Network offline -- browser blocking all requests

### Request Fails

- **CORS preflight 403/404** -- server configuration issue, not a client bug
- **Auth 401/403** -- token expired, missing, or wrong format
- **404** -- URL wrong (trailing slash, missing params, wrong endpoint)
- **500** -- backend error (check server logs in Datadog)
- **Timeout** -- slow network or server hang

### Response Wrong

- **DTO mismatch** -- backend changed the contract
- **Null/undefined** -- optional fields missing from response
- **Type error** -- response shape does not match expected interface
- **Empty array** -- valid response but no data (not an error)

### TanStack Query Stuck

- **Infinite refetch** -- query key changes on every render (unstable reference)
- **Stale data** -- `staleTime: 0` causes constant refetching
- **Cache not invalidating** -- need manual `queryClient.invalidateQueries()`
- **Loading forever** -- `enabled` condition never becomes true

## Common Root Causes

### CORS failures

- Server not sending `Access-Control-Allow-Origin` header
- Preflight OPTIONS request not handled by server
- Credentials required but `credentials: 'include'` not set
- Custom headers trigger preflight without server support

### Auth failures

- Token expired (check token storage in localStorage or cookies)
- Bearer format wrong (`Authorization: Bearer ${token}`)
- Token not included in request headers at all
- Backend requires a different auth scheme than what the client sends

### TanStack Query issues

- Query key not stable -- recreated each render, causes refetch loop
- `enabled` logic wrong -- never fetches or fetches before data is ready
- `staleTime` too low -- constant refetching on every focus/mount
- `retry: false` set -- fails once and never retries
- Cache key collision -- different queries return wrong cached data

### DTO mismatches

- Backend added a required field -- frontend crashes on missing data
- Frontend expects array -- backend returns null for empty case
- Enum values changed -- client-side validation fails
- Date format inconsistent -- parsing produces invalid dates
