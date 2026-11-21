// SPDX-License-Identifier: MIT
//
// The browser dice map against gvanalysis's: every map in fixtures/dicemap.json
// is `dice_distribution`'s (scripts/dicemap_fixtures.py), on native bgsage's
// full search, and `dice_map` must draw it from the WASM build's trimmed one.
//
// At 2-ply the two agree: the trimmed search's one cut keeps sixteen plays,
// and a roll is held to a thousandth, every cube mark exactly. At 3-ply its
// second cut keeps five, and can drop the play the full search finds best:
// on these fixtures once in 273 rolls, 4-5 in the 9-point cube position, by
// 0.056. That is the price of a 3-ply map in seconds rather than a minute,
// so it is allowed -- but only as what it is: in the roller's disfavour
// (a cut can lose the best play, never invent a better one), on one roll of
// a map at most, and with the map's average still within half a hundredth.
//
// Skips when dist/ has not been built (./build.sh), since building needs
// emscripten.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { create_engine } from "../src/engine.js";
import { dice_map, dice_grid, die_averages } from "../src/dicemap.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const dist = path.join(here, "..", "dist");
const fixture = JSON.parse(fs.readFileSync(path.join(here, "fixtures", "dicemap.json")));
const built = fs.existsSync(path.join(dist, "manifest.json"));
const skip = built ? false : "dist/ not built: run ./build.sh";
const TIE = 1e-3;

async function engine() {
    const manifest = JSON.parse(fs.readFileSync(path.join(dist, "manifest.json")));
    const { default: factory } = await import(path.join(dist, "gvengine.mjs"));
    return create_engine({ factory, manifest, read: async (name) => new Uint8Array(fs.readFileSync(path.join(dist, name))) });
}

test("the fixtures are this build's bgsage", { skip }, () => {
    const pinned = fs.readFileSync(path.join(here, "..", "bgsage.version"), "utf8").trim();
    assert.equal(fixture.bgsage, pinned);
});

test("dice_map draws gvanalysis's maps", { skip }, async (t) => {
    const e = await engine();
    try {
        for (const want of fixture.maps) {
            const label = `${want.ogid} ${want.ply}-ply${want.doubled ? " after a take" : ""}`;
            await t.test(label, () => {
                const got = dice_map(e, want.ogid, { after: want.after, doubled: want.doubled, ply: want.ply });
                assert.equal(got.roller, want.roller);
                assert.equal(got.won, want.won);
                if (want.won) return;
                // The roller's own sign: a reply map's equities are the mover's.
                const sign = want.roller === "mover" ? 1 : -1;
                let missed = 0;
                got.rolls.forEach((roll, k) => {
                    const w = want.rolls[k];
                    assert.deepEqual(roll.dice, w.dice);
                    const gap = sign * (roll.equity - w.equity);
                    if (Math.abs(gap) < TIE) {
                        assert.equal(roll.cube ?? null, w.cube ?? null, `${roll.dice}: cube`);
                        return;
                    }
                    missed += 1;
                    assert.equal(want.ply, 3, `${roll.dice}: ${roll.equity} vs ${w.equity} at 2-ply`);
                    assert.ok(gap < 0, `${roll.dice}: the trimmed search found a better play than the full one`);
                });
                assert.ok(missed <= 1, `${missed} rolls missed their best play`);
                const near = missed ? 5e-3 : TIE;
                assert.ok(Math.abs(got.average - want.average) < near, `average ${got.average} vs ${want.average}`);
                die_averages(dice_grid(got)).forEach((x, j) =>
                    assert.ok(Math.abs(x - want.die_averages[j]) < (missed ? 0.02 : TIE), `die ${j + 1}`));
            });
        }
    } finally {
        e.destroy();
    }
});
