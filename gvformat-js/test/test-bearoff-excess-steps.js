// SPDX-License-Identifier: MIT
// Copyright (C) 2026 Nicholas Vlamis

// One checker borne off with both dice and pips to spare keeps its steps:
// 8/off with a 4-5 is 8/4 then 4/off with the 5. It used to become one 8-pip
// step, too long for `pips`, and the move was dropped -- shown as "no move".
//
// Mirrors tests/test_bearoff_excess_steps.py.

import { _computeMoveSteps, _flipBoard, _notationToSteps, fitAlternativeSteps } from '../src/export.js';
import { parseOgid } from '../src/ogid.js';

let checks = 0, failures = 0;
function check(cond, label) {
  checks++;
  if (!cond) { failures++; console.log(`FAIL  ${label}`); } else console.log(`OK    ${label}`);
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const st = parseOgid('hllmmnnnooooooo:22334455566kkkp:W2N:45:B:R:1:0:5:55');
const board = st.onRoll === 'W' ? [...st.board] : _flipBoard([...st.board]);
check(board[8] === 1 && board[4] === 2 && board[3] === 2, "the position is White's, from White's side");

const want = [{ from: 17, pips: 4 }, { from: 21, pips: 5 }];
const steps = _notationToSteps('8/off', true, 4, 5, board);
check(same(steps, want), '8/off with a 4-5 is 8/4 then 4/off with the 5');
check(same(fitAlternativeSteps(steps, '8/off', true, 4, 5), want), 'and fits an alternative');
check(same(_notationToSteps('8/off', true, 5, 4, board), want), 'whichever order the dice are reported in');
check(same(_notationToSteps('8/off', true, 4, 5), want), 'and with no board, smaller die first');

const after = [...board];
after[8] -= 1;
check(same(_computeMoveSteps(board, after, true, 4, 5), want), 'the played move splits the same way');

const held = [...board];
held[5] = -2;
check(same(_notationToSteps('7/off', true, 2, 6, held), [{ from: 18, pips: 6 }, { from: 24, pips: 2 }]),
  "a made point at the smaller die's stop sends the larger die first");
check(same(_notationToSteps('9/off', true, 4, 5, board), [{ from: 16, pips: 5 }, { from: 21, pips: 4 }]),
  'an exact two-die bear-off is split as before');

console.log(`\n${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
