// SPDX-License-Identifier: MIT
// Copyright (C) 2026 Nicholas Vlamis

// A double that was made, and a take, always count toward PR (XG: "a double
// that results in a double, and a take, are never obvious"). The triviality
// rules decide only whether a no-double or a pass counts. One sample match
// holds a correct double in an optional-double position, which used to go
// uncounted.
//
// Mirrors tests/test_cube_action_counting.py.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { convertXg, trivialCube } from '../src/xg2gva.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SAMPLES_DIR = path.join(__dirname, '..', '..', 'samples', 'xg');

let passed = 0;
let failed = 0;

function assert(cond, msg) {
  if (cond) { passed++; console.log(`OK  ${msg}`); }
  else { failed++; console.error(`FAIL  ${msg}`); }
}

let doubles = 0;
let takes = 0;
let optional = 0;
const uncounted = [];
for (const name of fs.readdirSync(SAMPLES_DIR).filter((n) => n.endsWith('.xg')).sort()) {
  const doc = await convertXg(fs.readFileSync(path.join(SAMPLES_DIR, name)));
  for (const game of doc.games) {
    for (const ply of game.plies) {
      const a = ply.analysis ?? {};
      if (a.double_take_equity == null) continue;
      if (ply.action_id === 21) {
        doubles++;
        if (trivialCube(a.no_double_equity, a.double_take_equity, a.double_pass_equity)
            && a.equity_loss < 0.001) optional++;
      } else if (ply.action_id === 22) {
        takes++;
      } else {
        continue;
      }
      if (a.decision !== true) uncounted.push(`${name} game ${game.game_number}`);
    }
  }
}
assert(doubles > 0 && takes > 0, `samples hold doubles (${doubles}) and takes (${takes})`);
assert(optional > 0, `including a correct double the triviality rule calls obvious (${optional})`);
assert(uncounted.length === 0, `every double and take counts (${uncounted.join(', ') || 'none uncounted'})`);

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
