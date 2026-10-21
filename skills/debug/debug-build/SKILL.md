---
name: debug-build
description: "Debug TypeScript/webpack build failures: TS2307, TS2345, TS2339, TS2322, TS2769, tsc failed, webpack failed, module not found, circular dependency, missing types, bundle size, OOM. Triggers: build failed, tsc error, webpack error, vite error, esbuild error."
group: debugging
---

# Debug Build

## When to Use

- TypeScript compilation errors (TS2307, TS2345, TS2339, TS2322, TS2769, etc.)
- `tsc` or `yarn build` failing
- Module not found or cannot resolve imports
- Webpack compilation or bundling errors
- Missing type declarations or @types packages
- Circular dependency detected
- Bundle size exceeded or out-of-memory during build
- Loader or plugin errors in webpack

**Don't use for:**

- Jest test failures (use debug-jest)
- Runtime API errors (use debug-api)
- Dev server not starting (use dev-monitor)
- Writing new TypeScript code (general coding task)

## Failure Classification

| Category              | Key Signals                                                                                                                                  |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **Type Errors**       | TS2307 (module), TS2345 (arg mismatch), TS2339 (property), TS2322 (assignment), TS2769 (overload), TS2532 (undefined), TS7006 (implicit any) |
| **Module Resolution** | Cannot find module, circular dependency, ambient module missing, path mapping wrong                                                          |
| **Webpack**           | Module not found (loader), chunk size exceeded, OOM, plugin failure                                                                          |
| **Type Inference**    | Generic too complex, union too large, recursive depth limit, conditional type breaks                                                         |

> **Deep dive**: See `references/build-error-patterns.md` for complete patterns and root causes.

## Debugging Flow

**Run the build** — Execute `yarn build` or `tsc --noEmit` to capture full error output.

**Read the first error** — Always fix top-down. Later errors often cascade from the first. Extract the file path, line number, error code (TS2XXX), and expected vs actual types from the build output.

**Check recent changes** — Run `git diff` and `git log --oneline -10` on the failing file and its dependencies. Look for:

- Interface or type definition changes
- Added or removed dependencies
- Introduction of `any` that masked a real issue

**Trace the type chain** — Read the source at the indicated file and line. Inspect the inferred types by tracing declarations, generics, and imports upstream to find where the type diverges from what the call site expects.

**Debug module resolution** — For import errors, verify:

- Relative vs absolute path correctness
- Whether a file extension is needed
- Whether an index file exists at the target path
- Whether tsconfig `paths` includes the target directory

**Apply the fix** — Correct the import path, type annotation, or signature. Use the narrowest possible fix; do not weaken type safety.

**Re-run and verify** — Re-run the build after each fix. Confirm no cascade errors remain, then run full build plus tests (`yarn build && yarn test`) to confirm nothing else broke.

## Common Root Causes

Module resolution (TS2307), argument mismatches (TS2345), missing properties (TS2339), circular dependencies, and webpack loader issues. See `references/build-error-patterns.md` for the complete root cause catalog with detailed patterns for each error code.

## Constraints

- **DO NOT** use `any` type to suppress errors (use proper types or `unknown`)
- **DO NOT** use `@ts-ignore` or `@ts-expect-error` without a comment explaining why
- **DO NOT** fix errors out of order -- always fix the first error first
- **DO NOT** modify tsconfig to loosen strictness (e.g., disabling `strict`)
- **DO** use `import type` for type-only imports to avoid circular dependencies
- **DO** verify the full build passes after applying fixes
- **DO** check bundle size if webpack configuration was changed

## Companion Files

- **`references/build-error-patterns.md`** -- Complete error pattern classification and root cause catalog

## Output Format

Deliver a structured debugging report:

- **Error code**: The TS/webpack error code(s) encountered
- **Root cause**: One sentence explaining why the build failed
- **Fix applied**: What was changed and why
- **Cascade check**: Whether fixing this error resolved downstream errors
- **Build verification**: Confirmation that `tsc --noEmit` and `yarn build` pass
- **Side effects**: Any changes to bundle size, type strictness, or dependencies
