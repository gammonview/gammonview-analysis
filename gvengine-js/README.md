# gvengine-js — bgsage in the browser

bgsage's C++ compiled to WebAssembly, with the networks and bearoff database of
the same bgsage release beside it. It gives **native bgsage's numbers**: the
parity test holds every cubeful equity to within 5e-6 of `BgBotAnalyzer`, which
is what `gvanalysis` runs on the Studio and in the desktop helper. A position
read in a browser and the same position read anywhere else agree.

A rollout's cubeless numbers (its probabilities and cubeless equity) are held to
5e-5 instead. Inside each trial bgsage treats plays within 1e-6 of the best
cubeful value as tied and breaks the tie on cubeless equity, and the ~1e-7 by
which WASM and native arithmetic differ can put a play on either side of that
line: a different play of equal cubeful value but different gammon chances.
Measured, that was one pick in one trial of 360, moving the mean by 7e-6; the
cubeful equity cannot move, and native x86 and arm64 differ from each other the
same way.

Not published yet (`"private": true`); gammonview is the first consumer.

## What it does

| Call | What it is | Same as |
|---|---|---|
| `checker(pos, {ply})` | every legal play searched at `ply`, cube-aware | BgBotAnalyzer `Nply` |
| `checker(pos, {ply, trimmed: true, played})` | every play at 1-ply; up to 16 within 0.20 at 2-ply; up to five of those within 0.08 at `ply`; `played` and the top two always at `ply` | each play's number is exact for its `eval_level` |
| `checker(pos, {ply, match: true, played})` | match analysis's staged screen, `played` in every stage, groups deepest first | `gvanalysis.checker_eval.checker_play` |
| `checker(pos, {level: "2T"})`, `"3T"` | the whole decision as the rollout levels read it: a few plays rolled out (`se`), the rest at 2-ply or 1-ply | BgBotAnalyzer `truncated2` / `truncated3` |
| `cube(pos, {ply})` | n-ply cube decision | BgBotAnalyzer `Nply` |
| `cube(pos, {level: "2T"})`, `"3T"` | 360 trials truncated at 7, 2-ply (2T) or 3-ply (3T) inside | BgBotAnalyzer `truncated2` / `truncated3` |
| `rollout(pos, boards, {level})` | up to eight named plays rolled out at 2T or 3T; a bearoff-database board also gets its exact `bearoff` probabilities | `post_move_analytics` at a rollout level — the server's `POST /position/moves` |
| `post_move(pos, after, {ply})` | one board after a play, at `ply` — for a played board a move list does not hold (an illegal play) | `post_move_analytics` |
| `post_move(pos, after, {level: "2T"})`, `"3T"` | the same board rolled out, with `se` | `post_move_analytics` at a rollout level |
| `rolls(pos)` | each of the 21 rolls with its best 1-ply play: luck's sweep, one roll to a thread | `checker()` at 1-ply, first row |

Every row but the post-move rollouts is in the parity suite.

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

Trimmed 3-ply, on 911 checker decisions from real matches with the played move
passed (typical / 99th percentile / worst): **88 ms / 0.46 s / 0.67 s** on one
thread, 55 ms / 0.30 s / 0.51 s on four, 52 ms / 0.28 s / 0.49 s on eight.
Against the full search over the same decisions it chose a different play 3
times, at a worst cost of 0.00026, and no graded play's error moved by 0.01.
A one-stage cut (1-ply straight to five) missed 12 times, by up to 0.048, and
misgraded 41 plays by more than 0.02.

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
