# Build Error Patterns Reference

Reference companion to `debug-build/SKILL.md`. Quick-reference for classifying TypeScript compilation and webpack build failures and identifying root causes.

## Failure Classification

### Type Errors

- **TS2307**: Cannot find module -- import path wrong or missing declaration
- **TS2345**: Argument type mismatch -- function signature changed
- **TS2339**: Property does not exist -- interface outdated or typo
- **TS2322**: Type not assignable -- incompatible types
- **TS2769**: No overload matches -- wrong argument count or types
- **TS2532**: Object possibly undefined -- needs null check
- **TS7006**: Implicit any -- parameter needs type annotation

### Module Resolution Errors

- **Cannot find module** -- path wrong, file moved, extension missing
- **Circular dependency** -- A imports B imports A
- **Ambient module missing** -- @types package not installed
- **Path mapping wrong** -- tsconfig paths not resolving

### Webpack Errors

- **Module not found** -- loader missing for file type (CSS, SVG, etc.)
- **Chunk size exceeded** -- bundle too large, needs code splitting
- **Memory heap out** -- build process OOM
- **Plugin failure** -- webpack plugin crashed

### Type Inference Issues

- **Generic type too complex** -- TypeScript gives up inferring
- **Union too large** -- TypeScript performance degrades
- **Recursive type** -- TypeScript hits depth limit
- **Conditional type breaks** -- type logic is incorrect

## Common Root Causes

### TS2307 Cannot find module

- Import path is relative when it should be absolute (or vice versa)
- File extension required but missing (.ts/.tsx)
- File was moved but import path was not updated
- @types package missing (install with `yarn add -D @types/package`)
- tsconfig `paths` does not include the target directory

### TS2345 Argument type mismatch

- Function signature changed in an upstream file
- Optional parameter became required
- Type was widened or narrowed
- Generic constraint violated

### TS2339 Property does not exist

- Interface is outdated (DTO changed on backend)
- Typo in property name
- Property is conditionally present (use optional chaining `?.`)
- Type guard or narrowing failed

### Circular dependency

- A imports B, B imports A -- refactor shared types to a third file
- Barrel export (index.ts) imports from files that import from it
- Use type-only imports to break cycles: `import type { X } from './y'`

### Webpack module not found

- Loader missing for file type (CSS, SVG, images, etc.)
- Webpack `resolve.extensions` does not include the file type
- Alias not configured in webpack config
- Node module not installed
