// SPDX-License-Identifier: MIT
//
// The browser engine against native bgsage: every number in
// fixtures/parity.json is BgBotAnalyzer's (scripts/fixtures.py), and the WASM
// build must reproduce it. Not approximately in the sense of "close enough to
// play the same": the same arithmetic, to float precision. A drift here means
// the browser and the Studio disagree about a position, which is the one thing
// this package exists to prevent.
//
// Skips when dist/ has not been built (./build.sh), since building needs
// emscripten.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { create_engine } from "../src/engine.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const dist = path.join(here, "..", "dist");
const fixture = JSON.parse(fs.readFileSync(path.join(here, "fixtures", "parity.json")));
const built = fs.existsSync(path.join(dist, "manifest.json"));
const skip = built ? false : "dist/ not built: run ./build.sh";
const TOLERANCE = 5e-6;

async function engine(file) {
    const manifest = JSON.parse(fs.readFileSync(path.join(dist, "manifest.json")));
    const { default: factory } = await import(path.join(dist, file));
    return { manifest, engine: await create_engine({ factory, manifest, read: async (name) => fs.readFileSync(path.join(dist, name)) }) };
}

function same_moves(got, want, where) {
    assert.equal(got.moves.length, want.length, `${where}: number of plays`);
    const by_board = new Map(got.moves.map((m) => [m.board.join(), m]));
    for (const m of want) {
        const g = by_board.get(m.board.join());
        assert.ok(g, `${where}: play ${m.board} missing`);
        assert.ok(Math.abs(g.equity - m.equity) < TOLERANCE, `${where}: ${g.equity} vs ${m.equity}`);
    }
    assert.equal(got.moves[0].board.join(), want[0].board.join(), `${where}: best play`);
}

function same_cube(got, want, where) {
    for (const k of ["equity_nd", "equity_dt", "equity_dp"]) {
        assert.ok(Math.abs(got[k] - want[k]) < TOLERANCE, `${where}: ${k} ${got[k]} vs ${want[k]}`);
    }
    assert.equal(got.should_double, want.should_double, `${where}: should_double`);
    assert.equal(got.should_take, want.should_take, `${where}: should_take`);
}

test("built from the bgsage the fixtures came from", { skip }, () => {
    const manifest = JSON.parse(fs.readFileSync(path.join(dist, "manifest.json")));
    const pinned = fs.readFileSync(path.join(here, "..", "bgsage.version"), "utf8").trim();
    assert.equal(manifest.bgsage, pinned);
    assert.equal(fixture.bgsage, pinned, "fixtures are stale: run scripts/fixtures.py");
});

test("single-threaded build", { skip }, async (t) => {
    const { engine: e } = await engine("gvengine.mjs");
    await t.test("checker play at 2-ply", () => {
        fixture.positions.forEach((p, i) => same_moves(e.checker(p, { ply: 2 }), fixture.checker["2ply"][i], `position ${i}`));
    });
    await t.test("cube decisions at 2- and 3-ply", () => {
        for (const ply of [2, 3]) {
            fixture.positions.forEach((p, i) => same_cube(e.cube(p, { ply }), fixture.cube[`${ply}ply`][i], `${ply}ply position ${i}`));
        }
    });
    await t.test("trimmed 3-ply: every number is the full search's at its own depth", () => {
        fixture.positions.forEach((p, i) => {
            const got = e.checker(p, { ply: 3, trimmed: true });
            const full = { "2-ply": fixture.checker["2ply"][i], "3-ply": fixture.checker["3ply"][i] };
            for (const level of Object.keys(full)) full[level] = new Map(full[level].map((m) => [m.board.join(), m.equity]));
            assert.ok(got.moves.slice(0, 2).every((m) => m.eval_level === "3-ply"), `position ${i}: a shallow value in the top two`);
            for (const m of got.moves) {
                if (!full[m.eval_level]) continue;
                assert.ok(Math.abs(m.equity - full[m.eval_level].get(m.board.join())) < TOLERANCE, `position ${i} ${m.eval_level}`);
            }
        });
    });
    for (const [ply, name] of [[2, "2ply"], [3, "3ply"]]) {
        await t.test(`match analysis's screen at ${name}, played move forced`, () => {
            fixture.match[name].forEach(({ played, moves }, i) => {
                const got = e.checker(fixture.positions[i], { ply, match: true, played });
                assert.equal(got.moves.length, moves.length, `position ${i}: plays`);
                moves.forEach((m, k) => {
                    const g = got.moves[k];
                    assert.deepEqual(g.board, m.board, `position ${i} row ${k}: order`);
                    assert.equal(g.eval_level, m.eval_level, `position ${i} row ${k}: level`);
                    assert.ok(Math.abs(g.equity - m.equity) < TOLERANCE, `position ${i} row ${k}: ${g.equity} vs ${m.equity}`);
                    assert.ok(Math.abs(g.cubeless_equity - m.cubeless_equity) < TOLERANCE, `position ${i} row ${k}: cubeless`);
                });
            });
        });
    }
    for (const [ply, name] of [[2, "2ply"], [3, "3ply"]]) {
        await t.test(`a played board at ${name}, as post_move_analytics reads it`, () => {
            fixture.post_move[name].forEach(({ board, equity, cubeless_equity }, i) => {
                const got = e.post_move(fixture.positions[i], board, { ply });
                assert.ok(Math.abs(got.equity - equity) < TOLERANCE, `position ${i}: ${got.equity} vs ${equity}`);
                assert.ok(Math.abs(got.cubeless_equity - cubeless_equity) < TOLERANCE, `position ${i}: cubeless`);
            });
        });
    }
    await t.test("trimmed 3-ply: the played move is searched, however bad", () => {
        fixture.positions.forEach((p, i) => {
            const worst = fixture.checker["3ply"][i].at(-1);
            const got = e.checker(p, { ply: 3, trimmed: true, played: worst.board });
            const m = got.moves.find((x) => x.board.join() === worst.board.join());
            assert.equal(m.eval_level, "3-ply", `position ${i}`);
            assert.ok(Math.abs(m.equity - worst.equity) < TOLERANCE, `position ${i}`);
        });
        assert.throws(() => e.checker(fixture.positions[0], { ply: 3, trimmed: true, played: fixture.positions[0].board }),
            /not a legal play/);
    });
    e.destroy();
});

test("threaded build", { skip }, async (t) => {
    const { engine: e } = await engine("gvengine-mt.mjs");
    await t.test("checker play at 3-ply", () => {
        fixture.positions.forEach((p, i) => same_moves(e.checker(p, { ply: 3, threads: 4 }), fixture.checker["3ply"][i], `position ${i}`));
    });
    await t.test("cube decisions at 2T", () => {
        fixture.positions.forEach((p, i) => same_cube(e.cube(p, { level: "2T", threads: 4 }), fixture.cube.truncated2[i], `position ${i}`));
    });
    await t.test("cube decisions at 3T", () => {
        fixture.cube.truncated3.forEach((want, i) => same_cube(e.cube(fixture.positions[i], { level: "3T", threads: 8 }), want, `position ${i}`));
    });
    for (const [level, name] of [["2T", "truncated2"], ["3T", "truncated3"]]) {
        await t.test(`single plays rolled out at ${level}, as /position/moves does`, () => {
            fixture.rollout[name].forEach((want, i) => {
                const got = e.rollout(fixture.positions[i], want.map((m) => m.board), { level, threads: 8 });
                want.forEach((m, k) => {
                    assert.deepEqual(got.moves[k].board, m.board);
                    assert.ok(Math.abs(got.moves[k].equity - m.equity) < TOLERANCE, `position ${i} play ${k}: ${got.moves[k].equity} vs ${m.equity}`);
                    assert.ok(Math.abs(got.moves[k].cubeless_equity - m.cubeless_equity) < TOLERANCE, `position ${i} play ${k} cubeless`);
                });
            });
        });
    }
    for (const [level, name] of [["2T", "truncated2"], ["3T", "truncated3"]]) {
        await t.test(`whole checker decisions at ${level}, as BgBotAnalyzer reads them`, () => {
            fixture.checker[name].forEach((want, i) => {
                const got = e.checker(fixture.positions[i], { level, threads: 8 });
                assert.equal(got.moves.length, want.length, `position ${i}: number of plays`);
                const by_board = new Map(got.moves.map((m) => [m.board.join(), m]));
                for (const m of want) {
                    const g = by_board.get(m.board.join());
                    assert.equal(g.eval_level, m.eval_level, `position ${i}: ${m.board} level`);
                    assert.ok(Math.abs(g.equity - m.equity) < TOLERANCE, `position ${i} ${m.eval_level}: ${g.equity} vs ${m.equity}`);
                }
                assert.equal(got.moves[0].board.join(), want[0].board.join(), `position ${i}: best play`);
            });
        });
    }
    await t.test("a rollout refuses a board that is not a legal play", () => {
        assert.throws(() => e.rollout(fixture.positions[0], [fixture.positions[0].board]), /not a legal play/);
    });
    e.destroy();
});
