// SPDX-License-Identifier: MIT
// Copyright (C) 2026 Nicholas Vlamis

// OGXM 2.3: another producer's file, written by the reference codec
// (`tests/gen_ogxm23_fixtures.py`), with a priced forfeit, a stored match
// equity table that is not ours, and a clock rule. A 2.2 reader refuses it;
// this one reads it, converts its MWCs on the table it stores -- the anchors
// are the reference's own -- and writes the table back with the numbers.

import { readFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { readGvab } from '../src/reader.js';
import { write_gvab } from '../src/binary.js';
import { mwcAnchors, storedMet } from '../src/met.js';
import { frameKey } from '../src/basefill.js';

let passed = 0, failed = 0;
function assert(cond, msg) {
  if (cond) { passed++; console.log(`OK  ${msg}`); }
  else { failed++; console.error(`FAIL  ${msg}`); }
}

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'ogxm2');
const data = new Uint8Array(readFileSync(join(FIXTURES, 'v23-foreign.ogxm')));
const pinned = JSON.parse(readFileSync(join(FIXTURES, 'v23-foreign.anchors.json'), 'utf8'));
const header = (b) => {
  const dv = new DataView(b.buffer, b.byteOffset);
  return [dv.getUint16(6, true), dv.getUint16(10, true)];
};
const emg = (v, [lose, win]) => (v - lose) / (win - lose) * 2 - 1;
const r4 = (x) => Math.round(x * 1e4) / 1e4;

assert(header(data).join('/') === '3/3', 'the fixture is a 2.3 file a 2.2 reader refuses');

const doc = readGvab(data);
const info = doc.analysis_info;
assert(JSON.stringify(info.met) === JSON.stringify(pinned.table), 'the block keeps the table it stores');
assert(info.met_id === 'test-shifted', 'and its name');
assert(doc.clock_info.rule === 'opengammon' && JSON.stringify(doc.clock_info.grants) === '[1,0]',
  'the clock keeps its rule and grants');

/** The document's value for a pinned entry: the first alternative's equity, or
 *  the no-double value of a cube on its own ply or a dice ply's live cube. */
function docValue(a) {
  if (a.alternatives && a.alternatives.length) return a.alternatives[0].equity;
  if (a.no_double_equity !== undefined) return a.no_double_equity;
  if (a.cube_decision && a.cube_decision.no_double_equity !== undefined) {
    return a.cube_decision.no_double_equity;
  }
  return undefined;
}

{
  let frames = 0, values = 0, apart = 0, checked = 0;
  const bad = [];
  for (const e of pinned.plies) {
    const p = doc.games[e.gi].plies[e.pi];
    if (p.action_id !== e.action_id) { bad.push(`${e.gi},${e.pi} is action ${p.action_id}`); continue; }
    const a = p.analysis;
    if (!a) continue;                                   // the forfeit's RESIGN is not one we keep
    checked++;
    const [lose, win] = e.anchors;
    const want = [Number(((win + lose) / 2).toFixed(8)), Number(((win - lose) / 2).toFixed(8))];
    if (a.mwc_frame && Math.abs(a.mwc_frame[0] - want[0]) < 1e-8
        && Math.abs(a.mwc_frame[1] - want[1]) < 1e-8) frames++;
    else bad.push(`${e.gi},${e.pi} frame ${JSON.stringify(a.mwc_frame)} vs ${JSON.stringify(want)}`);
    if (e.mwc === undefined) continue;
    const got = docValue(a);
    if (got !== undefined && Math.abs(got - r4(emg(e.mwc, e.anchors))) <= 1.5e-4) values++;
    else bad.push(`${e.gi},${e.pi} value ${got} vs ${r4(emg(e.mwc, e.anchors))}`);
    const key = frameKey(p);
    const ours = mwcAnchors(key[0], key[1], key[2], key[3]);
    if (Math.abs(emg(e.mwc, e.anchors) - emg(e.mwc, [ours[1], ours[0]])) > 1e-3) apart++;
  }
  assert(bad.length === 0, `every analysed ply lines up with the reference's${bad.length ? `: ${bad.slice(0, 5).join('; ')}` : ''}`);
  assert(checked > 200 && frames === checked, `each ply's frame is the stored table's (${frames}/${checked})`);
  assert(values > 200, `each value is converted on it (${values})`);
  assert(apart > 50, `and on our table ${apart} of them would not have been`);
}

// Rewritten: verbatim while untouched, and through the stated frames when not.
{
  const again = readGvab(write_gvab(doc));
  assert(JSON.stringify(again.analysis_info.met) === JSON.stringify(pinned.table), 'an untouched rewrite keeps the table');

  const edited = structuredClone(doc);
  delete edited._ogxm2_passthrough;                     // nothing goes back verbatim
  const out = write_gvab(edited);
  assert(header(out)[0] === 3, 'our own encoding of it is a 2.3 file');
  const back = readGvab(out);
  assert(JSON.stringify(back.analysis_info.met) === JSON.stringify(pinned.table),
    'and states the table its MWCs are quoted on');
  let same = 0, differ = 0;
  doc.games.forEach((g, gi) => g.plies.forEach((p, pi) => {
    const a = p.analysis;
    const b = back.games[gi].plies[pi].analysis;
    if (!a || docValue(a) === undefined) return;
    if (b && docValue(b) === docValue(a)) same++;
    else differ++;
  }));
  assert(differ === 0 && same > 200, `which read back to the same equities (${same} same, ${differ} not)`);
  assert(back.clock_info.rule === 'opengammon' && JSON.stringify(back.clock_info.grants) === '[1,0]',
    'and the same clock');
}

// Our own blocks: the table we write is ours, so the document does not carry it.
{
  const ours = structuredClone(doc);
  delete ours._ogxm2_passthrough;
  delete ours.analysis_info.met;
  delete ours.analysis_info.met_id;
  ours.games.forEach((g) => g.plies.forEach((p) => { if (p.analysis) delete p.analysis.mwc_frame; }));
  const out = write_gvab(ours);
  const back = readGvab(out);
  assert(header(out).join('/') === '3/0', 'a block on our table is written at minor 3, readable by 2.2');
  assert(back.analysis_info.met === undefined, 'and reads back without a table in the document');
  assert(JSON.stringify(storedMet(7)) !== JSON.stringify(pinned.table), '(the fixture\'s table is not ours)');
}

// The table helpers themselves.
{
  const t = storedMet(3);
  assert(JSON.stringify(t) === JSON.stringify({ pre_crawford: [0.67736, 0.75076, 0.59947], post_crawford: [0.51197, 0.67736] }),
    'ours cut at 3 is P(1,2), P(1,3), P(2,3) and the leader\'s post-Crawford row');
  assert(storedMet(1) === null && storedMet(26) === null, 'nothing below 2 away, nor past our table');
  assert(storedMet(5, false).post_crawford === undefined, 'no post-Crawford row without the rule');
}

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
