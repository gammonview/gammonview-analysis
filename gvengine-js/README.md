# gvengine-js — bgsage in the browser

bgsage's C++ compiled to WebAssembly, with the networks and bearoff database of
the same bgsage release beside it. It gives **native bgsage's numbers**: the
parity test holds every equity to within 5e-6 of `BgBotAnalyzer`, which is what
`gvanalysis` runs on the Studio and in the desktop helper. A position read in a
browser and the same position read anywhere else agree.

Not published yet (`"private": true`); gammonview is the first consumer.

## What it does

| Call | What it is | Same as |
|---|---|---|
| `checker(pos, {ply})` | every legal play searched at `ply`, cube-aware | BgBotAnalyzer `Nply` |
| `checker(pos, {ply, trimmed: true})` | every play at 1-ply, then at most five within 0.08 at `ply`, and no 1-ply value left in the top two | the deep plays' numbers are exact; the rest are 1-ply |
| `cube(pos, {ply})` | n-ply cube decision | BgBotAnalyzer `Nply` |
| `cube(pos, {level: "2T"})` | 360 trials truncated at 7, 2-ply inside | BgBotAnalyzer `truncated2` |

`ply` is 1–4. Each call takes `threads` (default 1) and `budget_ms` (default ten
minutes). The answer is the engine's JSON, in BgBotAnalyzer's analytics shape,
not OGXM.

A position is bgsage's board — 26 ints from the player on roll's side, point 1
to 24, positive for the player's checkers, `board[25]` the player's bar and
`board[0]` the opponent's — plus `dice`, `cube_value`, `cube_owner`
(`"centered"`, `"player"`, `"opponent"`), `away1`/`away2` (0 for money),
`is_crawford`, `jacoby`, `beaver`.

```js
import { create_engine } from "@gammonview/engine";
import factory from "./dist/gvengine.mjs";       // or gvengine-mt.mjs

const manifest = await (await fetch("dist/manifest.json")).json();
const engine = await create_engine({
    factory, manifest,
    read: async (name) => new Uint8Array(await (await fetch(`dist/${name}`)).arrayBuffer()),
});
const { moves } = engine.checker({ board, dice: [3, 1] }, { ply: 3 });
```

Calls are synchronous and can take seconds, so in a page this runs in a Worker.

## Two builds

- **`gvengine.mjs`** runs anywhere. Leave `threads` at 1: it has no threads to
  start.
- **`gvengine-mt.mjs`** runs threads on WebAssembly shared memory, which a
  browser allows only on a cross-origin-isolated page (COOP `same-origin` +
  COEP `require-corp` or `credentialless`). Its pool is
  `navigator.hardwareConcurrency` threads.

## What it costs

Measured on the Studio (M-series) in Node, which is V8; a browser's own engine
may differ. Typical and worst over a seeded sample, checker plays:

| | 1 thread | 4 threads | 8 threads |
|---|---|---|---|
| 2-ply | 75 ms / 0.95 s | 21 ms / 0.27 s | — |
| 3-ply | 0.71 s / 16 s | 0.20 s / 4.3 s | 0.12 s / 2.2 s |
| 3-ply trimmed | 0.13 s / 1.1 s | 0.06 s / 0.8 s | 0.06 s / 0.8 s |

Cube decisions: 3-ply 54 ms on one thread, 2T 1.0 s / 2.7 s on eight. 4-ply
checker play is not interactive at any thread count (16 threads: 1.9 s / 23 s).
WASM runs n-ply search about 2.2× slower than native bgsage on one core, and
rollouts about 1.25×.

The download is the engine (~0.5 MB) plus 13 MB of networks and a 4.9 MB bearoff
database — 8.6 MB gzipped together.

## Building

```bash
./build.sh        # needs emcc (brew install emscripten), git and uv
npm test          # skips when dist/ is absent
```

`bgsage.version` is the one input. `build.sh` clones that tag of bgsage for the
C++ and takes the networks from that wheel on PyPI, so the engine and its
networks cannot come from different releases. Moving it: edit the file, rebuild,
`npm run fixtures`, `npm test`. The test refuses fixtures from another version.

The build compiles a patched copy of the sources, never the checkout. The one
patch routes `neural_net.cpp` to its NEON path: wasm32 is neither x86 nor ARM,
so it would otherwise fall to scalar code, and emscripten's NEON translation
reproduces native numbers exactly where its AVX2 translation drifts by up to
0.003.

## Licence

`src/entry.cpp` is a modified copy of bgsage's `cpp/src/mobile.cpp` and is
**MPL-2.0**, as are bgsage's sources, networks and bearoff database that a
build compiles and copies into `dist/` (`dist/LICENSE-bgsage.txt`). Everything
else here is MIT, like the rest of the repository. Anyone shipping `dist/` must
say where the source is: bgsage's tag on GitHub, plus this directory for the
entry point and the build patch. See `THIRD-PARTY-NOTICES.md`.
