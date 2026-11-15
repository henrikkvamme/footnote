# starter

The template for a new challenge app. It is built so that coding agents get fast, strict feedback.

- TanStack Start and React 19 on Vite+ (`vp`), with pnpm
- Effect 4 services behind TanStack Start server functions
- XState 6 with `@xstate/effect`, read in React through `@effect/atom-react`
- Oxlint with type-aware rules, the anti-slop rules, a no-comments rule and import fences
- `effect-tsrs` for type checks with Effect diagnostics as errors
- A project CLI, `pnpm j`, for the gate, verification and scaffolding

## Start a new challenge app

```sh
cd starter
pnpm install
pnpm j new project my-challenge
cd ../my-challenge
pnpm install
pnpm j check
pnpm dev
```

`AGENTS.md` has the rules for agents. The lint rules in `tools/oxlint/anti-slop` come from
[dmmulroy/anti-slop](https://github.com/dmmulroy/anti-slop) through
[joelhooks/rat-stack](https://github.com/joelhooks/rat-stack), as does `tools/oxlint/no-comments.ts`.
Both sources use the MIT license.
