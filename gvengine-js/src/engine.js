// SPDX-License-Identifier: MIT
//
// bgsage in a browser: a thin wrapper over the emscripten build of
// `entry.cpp`. It owns the engine's memory and its request layout, and hands
// back the engine's own JSON -- BgBotAnalyzer's analytics shape, not OGXM.
// Turning that into a site's analysis shape is the caller's business, as it is
// `gvanalysis.position`'s on the Python side.
//
// Calls are synchronous and can take seconds, so in a browser this belongs in
// a Worker. Calls on one engine must not overlap; a Worker running them one at
// a time gives that for free.

const OWNERS = { centered: 0, player: 1, opponent: 2 };
const OP = { checker: 0, cube: 1, checker_trimmed: 2, cube_2t: 3, cube_3t: 4, checker_2t: 5, checker_3t: 6 };
const ROLLOUT_LEVELS = { "2T": 2, "3T": 3 };
const REQUEST_INTS = 65;
const DIR = "/gvengine";

/**
 * Create an engine.
 *
 * @param {object} options
 * @param {Function} options.factory  the default export of `gvengine.mjs`, or of
 *   `gvengine-mt.mjs` where SharedArrayBuffer is available
 * @param {object} options.manifest  `manifest.json`, parsed
 * @param {(name: string) => Promise<Uint8Array>} options.read  a file named in
 *   the manifest (`models/...`, the bearoff database), as bytes
 * @param {object} [options.module]  passed to the factory (`locateFile`, ...)
 */
export async function create_engine({ factory, manifest, read, module = {} }) {
    const M = await factory(module);
    M.FS.mkdir(DIR);
    M.FS.mkdir(`${DIR}/models`);
    const names = [...new Set([...manifest.models, manifest.bearoff])];
    const files = await Promise.all(names.map((name) => read(name)));
    names.forEach((name, i) => M.FS.writeFile(`${DIR}/${name}`, files[i]));

    const owned = [];
    const string = (s) => { const p = M.stringToNewUTF8(s); owned.push(p); return p; };
    const paths = M._malloc(4 * manifest.models.length);
    const hidden = M._malloc(4 * manifest.hidden_sizes.length);
    owned.push(paths, hidden);
    manifest.models.forEach((name, i) => {
        M.HEAP32[(paths >> 2) + i] = string(`${DIR}/${name}`);
        M.HEAP32[(hidden >> 2) + i] = manifest.hidden_sizes[i];
    });
    const handle = M._gvengine_create(string(manifest.strategy_type), paths, hidden,
        manifest.models.length, string(`${DIR}/${manifest.bearoff}`));
    owned.forEach((p) => M._free(p));
    if (!handle) throw new Error(M.UTF8ToString(M._gvengine_last_error()));
    const request = M._malloc(4 * REQUEST_INTS);

    function write_request(position, { ply = 2, threads = 1, budget_ms = 600_000, played } = {}) {
        const at = request >> 2;
        if (position.board?.length !== 26) throw new Error("board must have 26 points");
        position.board.forEach((n, i) => { M.HEAP32[at + i] = n; });
        const [die1, die2] = position.dice ?? [1, 1];
        const owner = OWNERS[position.cube_owner ?? "centered"];
        if (owner === undefined) throw new Error(`unknown cube owner ${position.cube_owner}`);
        [die1, die2, ply, position.cube_value ?? 1, owner, position.away1 ?? 0, position.away2 ?? 0,
            position.is_crawford ? 1 : 0, position.jacoby === false ? 0 : 1, position.beaver === false ? 0 : 1,
            budget_ms, threads, played ? 1 : 0].forEach((n, i) => { M.HEAP32[at + 26 + i] = n; });
        if (played && played.length !== 26) throw new Error("played must have 26 points");
        (played ?? []).forEach((n, i) => { M.HEAP32[at + 39 + i] = n; });
    }

    function read_reply(out) {
        if (!out) throw new Error(M.UTF8ToString(M._gvengine_last_error()));
        try {
            return JSON.parse(M.UTF8ToString(out));
        } finally {
            M._gvengine_free(out);
        }
    }

    function analyze(op, position, options) {
        write_request(position, options);
        return read_reply(M._gvengine_analyze(handle, op, request));
    }

    return {
        /**
         * Every legal play of `position.dice`, best first. Without `trimmed`
         * every play is searched at `ply`, which is what BgBotAnalyzer does.
         * `trimmed` screens them all at 1-ply, searches up to 16 at 2-ply and
         * up to five of those at `ply`; each move's `eval_level` says which
         * depth its number is from. `played`, a board after one of the legal
         * plays, is always searched at `ply` -- pass it when grading a play.
         * `{ level: "2T" }` or "3T" reads the decision as BgBotAnalyzer's
         * rollout levels do: a few plays rolled out (`eval_level` "Rollout",
         * with `se`), the rest at 2-ply or 1-ply.
         */
        checker: (position, { trimmed = false, level, ...options } = {}) =>
            analyze(level === "2T" ? OP.checker_2t : level === "3T" ? OP.checker_3t
                : trimmed ? OP.checker_trimmed : OP.checker, position, options),
        /** The cube decision at `ply`, or rolled out with `{ level: "2T" }` or "3T". */
        cube: (position, { level, ...options } = {}) =>
            level === "2T" ? analyze(OP.cube_2t, position, options)
                : level === "3T" ? analyze(OP.cube_3t, position, options)
                    : analyze(OP.cube, position, options),
        /**
         * Named plays of `position.dice`, each rolled out at `level` ("2T" or
         * "3T"): `boards` are the positions after them, at most eight. What the
         * server's `POST /position/moves` answers, move for move.
         */
        rollout(position, boards, { level = "2T", ...options } = {}) {
            if (!ROLLOUT_LEVELS[level]) throw new Error(`unknown rollout level ${level}`);
            if (!boards?.length || boards.length > 8) throw new Error("between 1 and 8 moves");
            write_request(position, options);
            const ptr = M._malloc(4 * 26 * boards.length);
            try {
                boards.forEach((b, i) => {
                    if (b.length !== 26) throw new Error("a board has 26 points");
                    b.forEach((n, j) => { M.HEAP32[(ptr >> 2) + 26 * i + j] = n; });
                });
                return read_reply(M._gvengine_rollout_moves(handle, ROLLOUT_LEVELS[level], request, ptr, boards.length));
            } finally {
                M._free(ptr);
            }
        },
        destroy() {
            M._free(request);
            M._gvengine_destroy(handle);
        },
    };
}
