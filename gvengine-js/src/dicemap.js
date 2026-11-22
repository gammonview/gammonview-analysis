// SPDX-License-Identifier: MIT
//
// The dice map: how each of the 36 rolls would go, as XG's Dice Distribution
// window shows it.
//
// Which rolls depends on the decision. A cube decision is about the roll to
// come, so it is the side on roll's own rolls from the board as it stands --
// or, asked for, from the board after a double and a take. A checker play is
// judged by what it leaves, so it is the opponent's replies to the play
// selected -- which is what makes clicking a second candidate show *why* one
// is better: the rolls that hit, or that fail to.
//
// Every number is the analysed player's equity, whoever rolls, so a cell means
// the same thing in both maps: good for you, or bad. A reply map's numbers are
// the opponent's negated. They are on the panel's scale too: an after-take map
// is in the cube *before* the double, as the panel's D/T is, so its average
// sits beside that number rather than half of it.
//
// Each roll is its best play at `ply`, by the trimmed search: every play
// screened at 1-ply, up to sixteen searched at 2-ply, up to five of those at
// 3-ply. The default, 2-ply, is XG's "1st roll 2-ply, 2nd roll 1-ply": a few
// tenths of a second for all 21 rolls on one thread, its weighted average
// within a few hundredths of the 3-ply read. 3-ply is the upgrade: some
// seconds on one thread, moving single rolls by up to about 0.05 and the
// average by about a hundredth. The opponent's cube is not consulted before a
// reply map: it is the rolls, as if they roll.
//
// Where the trimmed search costs something is the 3-ply cut to five plays,
// which can drop the play a full search finds best: once in 273 rolls on the
// parity fixtures, by 0.056. A full 3-ply search of every roll would take a
// minute in a browser, so the map takes that risk rather than the wait.
//
// `gvanalysis.dicemap` draws the same map natively, on the full search, and
// `test/dicemap.test.mjs` holds this one to it roll for roll.
//
// Pure: the engine (`engine.js`) is passed in, and nothing here knows where it
// runs. GammonView imports it from this package and runs it in its analysis
// worker.

import { parseOgid } from "@gammonview/gvformat/ogid.js";
import { mwcAnchors } from "@gammonview/gvformat/met.js";

/** The 21 rolls, and their weight out of 36. */
const ROLLS = [];
for (let d1 = 1; d1 <= 6; d1++) for (let d2 = d1; d2 <= 6; d2++) ROLLS.push([d1, d2, d1 === d2 ? 1 : 2]);

const round4 = (x) => Math.round(x * 1e4) / 1e4;

/** The engine's position for a parsed OGID, from the side `state` has on roll. */
function engine_position(state, { jacoby, beaver }) {
    return {
        board: state.board,
        cube_value: state.cubeValue,
        // A dead cube has no decision in it; bgsage reads deadness off the score.
        cube_owner: state.cubeOwner === "dead" ? "centered" : state.cubeOwner,
        away1: state.away1, away2: state.away2, is_crawford: Boolean(state.crawford),
        jacoby, beaver,
    };
}

function flip(board) {
    const out = new Array(26).fill(0);
    out[0] = board[25];
    out[25] = board[0];
    for (let i = 1; i < 25; i++) out[25 - i] = -board[i];
    return out;
}

const swap_owner = (owner) => ({ player: "opponent", opponent: "player" }[owner] ?? owner);

/** Whether the side on roll has no checker left: positives, and its bar (25). */
const borne_off = (board) => board[25] === 0 && board.every((n, i) => i === 25 || n <= 0);

/** `position` from the other side of the board: the side not on roll. */
const other_side = (position, board = position.board) => ({
    ...position, board: flip(board), cube_owner: swap_owner(position.cube_owner),
    away1: position.away2, away2: position.away1,
});

/**
 * The 21 rolls of whoever is on roll in `position`, each at the roller's own
 * equity after their best play at `ply`. A roll with no play leaves the board
 * as it is, so it is worth the other side's turn from there, negated: their
 * no-double equity at the same depth. A result reading the roller's last
 * checker off as a loss -- 1-ply's terminal misread, which luck guards against
 * too -- is taken as a single win. Each roll keeps the board its play leaves,
 * in the roller's frame, for `our_cube`.
 */
function sweep(engine, position, ply, threads) {
    let dance = null;
    return ROLLS.map(([a, b, weight]) => {
        const best = engine.checker({ ...position, dice: [a, b] }, { ply, trimmed: true, threads }).moves[0];
        let equity;
        if (!best) {
            dance ??= -engine.cube(other_side(position), { ply, threads }).equity_nd;
            equity = dance;
        } else {
            equity = best.equity < -0.9 && borne_off(best.board) ? 1 : best.equity;
        }
        return { dice: [a, b], weight, equity, board: best ? best.board : position.board };
    });
}

/**
 * The cube decision the reply leaves us: `board` is the replier's board after
 * their play, so it is turned back onto our side, with the cube and the score.
 * Read at the rolls' own depth. "double_take" and "double_pass" are a correct
 * double; "too_good" is a position that would be a pass but is better played
 * on. Null when we would not double.
 */
function our_cube(engine, replier, board, ply, threads) {
    return cube_mark(engine.cube(other_side(replier, board), { ply, threads }));
}

/** An engine cube decision as `our_cube` names it. */
function cube_mark(cube) {
    if (cube.should_double) return cube.should_take ? "double_take" : "double_pass";
    return cube.should_take ? null : "too_good";
}

/**
 * Whether `position_id` is a cube decision whose side on roll may double, and
 * so whether an after-take map means anything: not at a checker play, not with
 * the cube on the other side, and not in the Crawford game.
 *
 * @param {string} position_id
 */
export function can_double(position_id) {
    let state;
    try { state = parseOgid(position_id); } catch { return false; }
    const cube_decision = state.isCubeDecision || !(state.die1 && state.die2);
    return cube_decision && state.gameState !== "D"
        && !["opponent", "dead"].includes(state.cubeOwner) && !state.crawford;
}

/**
 * An equity read with the cube at `from`, restated with it at `to`, through
 * the match winning chances it stands for. Money's anchors are fixed, so there
 * it is a plain ratio; at a match score a cube level moves both anchors.
 */
function rescale(equity, away1, away2, from, to) {
    if (!away1 || !away2) return equity * from / to;
    const [win_from, loss_from] = mwcAnchors(away1, away2, from);
    const [win_to, loss_to] = mwcAnchors(away1, away2, to);
    const mwc = (win_from + loss_from) / 2 + equity * (win_from - loss_from) / 2;
    return (2 * mwc - (win_to + loss_to)) / (win_to - loss_to);
}

/**
 * The dice map for a decision.
 *
 * @param {object} engine  a `gvengine.js` engine
 * @param {string} position_id  the decision, as an OGID normalised for analysis
 * @param {object} [options]
 * @param {string|null} [options.after]  for a checker play, the position after
 *   the play selected (`position_after`), still with the mover on roll
 * @param {boolean} [options.doubled]  for a cube decision, the rolls after a
 *   double and a take: the cube turned and on the taker's side
 * @param {number} [options.ply]  2 or 3: how deep each roll's play is searched
 * @param {boolean} [options.jacoby]
 * @param {boolean} [options.beaver]
 * @param {number} [options.threads]
 * @returns {{roller: "mover"|"opponent", doubled: boolean, ply: number, average: number|null,
 *   won: boolean, rolls: {dice: number[], weight: number, equity: number,
 *   cube?: string|null}[]}|null}
 *   null for a decision with no map: a double already offered, a checker play
 *   with no play selected, or an after-take map where there is no double. A
 *   play that bears off the last checker has no replies to map, and says
 *   `won` with no rolls. A reply map where we could double after their
 *   roll gives each roll `cube`, the decision it leaves us (`our_cube`).
 */
export function dice_map(engine, position_id, {
    after = null, doubled = false, ply = 2, jacoby = true, beaver = true, threads = 1,
} = {}) {
    if (![2, 3].includes(ply)) throw new Error(`a dice map is read at 2-ply or 3-ply, not ${ply}`);
    const state = parseOgid(position_id);
    if (state.gameState === "D") return null;
    const checker = Boolean(state.die1 && state.die2) && !state.isCubeDecision;
    if (checker && !after) return null;
    const turned = !checker && doubled;
    if (turned && !can_double(position_id)) return null;

    const roller = checker ? parseOgid(after).flipped() : state;
    // The mover's checkers, seen from the replying side, are its negatives
    // and its bar (index 0): none left is a game already over.
    if (checker && roller.board[0] === 0 && roller.board.every((n) => n >= 0)) {
        return { roller: "opponent", doubled: false, ply, average: null, won: true, rolls: [] };
    }
    // Both money rules mean nothing at a match score, as everywhere else.
    const money = !state.away1 && !state.away2;
    const position = engine_position(roller, { jacoby: jacoby && money, beaver: beaver && money });
    if (turned) {
        position.cube_value = state.cubeValue * 2;
        position.cube_owner = "opponent";
    }
    const restate = (e) => (turned ? rescale(e, state.away1, state.away2, position.cube_value, state.cubeValue) : e);
    const sign = checker ? -1 : 1;
    // Our cube after their reply: ours to turn unless they own it, and not in
    // the Crawford game. The play moved no cube, so it is as the decision had it.
    const ours_to_turn = checker && ["centered", "player"].includes(state.cubeOwner) && !state.crawford;
    const rolls = sweep(engine, position, ply, threads).map(({ board, ...r }) => ({
        ...r,
        equity: round4(sign * restate(r.equity)),
        ...(ours_to_turn ? { cube: our_cube(engine, position, board, ply, threads) } : {}),
    }));
    return {
        roller: checker ? "opponent" : "mover",
        doubled: turned,
        ply,
        average: round4(rolls.reduce((t, r) => t + r.weight * r.equity, 0) / 36),
        won: false,
        rolls,
    };
}

/**
 * The market losers of a cube decision: the sequences -- our roll, then their
 * reply -- after which we would double and they would pass. Not doubling now
 * loses our market on exactly these, which is the count a player weighs
 * against the cost of doubling too early.
 *
 * Our roll is played at `ply`, as the map plays it, so the two agree on what
 * each roll does; their reply and our cube after it are read one ply
 * shallower. That is XG's "1st roll 2-ply, 2nd roll 1-ply" again, and what
 * keeps 441 sequences to well under a second at 2-ply. Read deeper the total
 * barely moves -- 43 to 45 of 1296 in a money position, 1128 to 1138 at a
 * match score -- but the split between a pass and too good does, since
 * those sequences sit on the pass line.
 *
 * Too good counts: they would pass, and we have lost our market as surely,
 * only to a position better played on. Their cube is not consulted after our
 * roll, as in any count of market losers.
 *
 * A position already past the market has none to lose, so the same sweep
 * counts its market gainers too: the sequences that bring the opponent back
 * in -- no double, or a double they would take -- or win them the game. They
 * are what waiting costs, at a pass and at too good alike; a too-good
 * position that falls to a pass has lost nothing, since it cashes next turn.
 * `decision` is the position's own cube decision at `ply`, named as a reply
 * map's marks are, which says which of the two counts is the one to read.
 *
 * @param {object} engine  a `gvengine.js` engine
 * @param {string} position_id  the cube decision, as `dice_map` takes it
 * @param {object} [options]  `ply`, `jacoby`, `beaver`, `threads`, as for `dice_map`
 * @returns {{ply: number, decision: string|null, pass: number, too_good: number, gainers: number,
 *   rolls: {dice: number[], weight: number, pass: number, too_good: number, gainers: number}[]}|null}
 *   The counts out of 1296 sequences; each roll's out of their 36 replies.
 *   Null where there is no market to lose: a checker play, the cube theirs,
 *   the Crawford game, a double already offered.
 */
export function market_losers(engine, position_id, { ply = 2, jacoby = true, beaver = true, threads = 1 } = {}) {
    if (![2, 3].includes(ply)) throw new Error(`market losers are read at 2-ply or 3-ply, not ${ply}`);
    if (!can_double(position_id)) return null;
    const state = parseOgid(position_id);
    const money = !state.away1 && !state.away2;
    const ours = engine_position(state, { jacoby: jacoby && money, beaver: beaver && money });
    const decision = cube_mark(engine.cube(ours, { ply, threads }));
    const rolls = sweep(engine, ours, ply, threads).map(({ dice, weight, board }) => {
        const out = { dice, weight, pass: 0, too_good: 0, gainers: 0 };
        // Our last checker off: the game is over, with no market in it.
        if (borne_off(board)) return out;
        const theirs = other_side(ours, board);
        for (const reply of sweep(engine, theirs, ply - 1, threads)) {
            // Their last checker off: a game lost -- no market, but what
            // cashing would have saved.
            if (borne_off(reply.board)) { out.gainers += reply.weight; continue; }
            const cube = our_cube(engine, theirs, reply.board, ply - 1, threads);
            if (cube === "double_pass") out.pass += reply.weight;
            else if (cube === "too_good") out.too_good += reply.weight;
            else out.gainers += reply.weight;
        }
        return out;
    });
    const total = (key) => rolls.reduce((t, r) => t + r.weight * r[key], 0);
    return { ply, decision, pass: total("pass"), too_good: total("too_good"), gainers: total("gainers"), rolls };
}

/**
 * The map as a 6x6 grid, `grid[i][j]` the roll of `i + 1` and `j + 1`: each
 * of the 15 non-doubles appears twice, as on any dice chart.
 *
 * @param {{rolls: {dice: number[]}[]}} map
 */
export function dice_grid(map) {
    const at = new Map(map.rolls.map((r) => [`${r.dice[0]}${r.dice[1]}`, r]));
    return [1, 2, 3, 4, 5, 6].map((a) => [1, 2, 3, 4, 5, 6].map((b) =>
        at.get(`${Math.min(a, b)}${Math.max(a, b)}`)));
}

/**
 * Each die's average: the mean of its column, so of the 11 rolls showing that
 * number with the double counted once -- how good "any roll with a 5" is. The
 * six together average to the map's own average, as XG's do.
 *
 * @param {object[][]} grid  `dice_grid`'s
 * @returns {number[]}
 */
export function die_averages(grid) {
    return [0, 1, 2, 3, 4, 5].map((j) => round4(grid.reduce((t, row) => t + row[j].equity, 0) / 6));
}
