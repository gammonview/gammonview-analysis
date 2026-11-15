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
    await t.test("trimmed 3-ply: the deep plays carry the full search's numbers", () => {
        fixture.positions.forEach((p, i) => {
            const got = e.checker(p, { ply: 3, trimmed: true });
            const full = new Map(fixture.checker["3ply"][i].map((m) => [m.board.join(), m.equity]));
            const deep = got.moves.filter((m) => m.eval_level === "3-ply");
            assert.ok(deep.length >= Math.min(2, got.moves.length), `position ${i}: top two searched`);
            assert.ok(got.moves.slice(0, 2).every((m) => m.eval_level === "3-ply"), `position ${i}: a 1-ply value in the top two`);
            for (const m of deep) assert.ok(Math.abs(m.equity - full.get(m.board.join())) < TOLERANCE, `position ${i}`);
        });
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
    e.destroy();
});
