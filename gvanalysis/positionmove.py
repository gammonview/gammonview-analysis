# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""Re-evaluate *individual moves* of a checker play at a deeper level.

Two callers use this: GammonView's analysis server (`POST /position/moves`) and
the desktop helper, which answers the same request for the site. Both hand it a
parsed position and get the same dict back; `analyze_moves` is the form for a
caller holding only a position id.

`analyze_position` answers a whole decision: every legal move, ranked. That is
the right unit for a first look, but it is the wrong unit for a second one.
bgsage screens candidates at 1-ply and only spends the requested level on the
survivors -- moves within 0.08 equity of the 1-ply best, at most five of them
(`_MultiPlyAnalyzer.FILTER_MAX_MOVES` / `FILTER_THRESHOLD`). So a 3ply answer
is really a handful of 3ply rows over a tail of 1-ply ones, and the tail is
exactly where a doubtful move tends to sit. Re-running the whole decision at
2T to settle one move costs the rollout of five moves to learn about one.

This evaluates named moves and nothing else, which is what makes "analyse at
3p, upgrade the interesting rows to 2T/3T" a cheap interaction rather than a
new full run each time.

**The number is the same number.** A move's equity *is* an evaluation of its
post-move board: `_CubefulAnalyzer` calls
`rollout_strategy.cubeful_evaluate_board(post_move_board, ...)` per survivor,
and `BgBotAnalyzer.post_move_analytics` (which is what this calls) routes to
that same function for rollout levels. The rollout's dice are stratified from
`config_.seed` and cached on the strategy, not advanced per call, so every
candidate -- rolled out in a batch or one at a time, now or ten minutes from
now -- sees the identical 360-trial sequence. Common random numbers, which is
also why two upgraded rows are comparable to each other.

Two things do *not* round-trip exactly, both harmless and both about probs
rather than equity:

  - n-ply levels reach cubeful equity through `cubeful_probs_nply` plus a
    post-hoc Janowski conversion here, where a full checker play uses
    `cubeful_equity_nply`. Different arithmetic, and measurably so: 3ply rows
    re-evaluated this way came back 0.001-0.003 off their full-run values,
    which is small but not nothing next to a 0.008 blunder threshold. Rollout
    levels have no such gap -- a 2T/3T upgrade reproduced a forced full run to
    four decimals, on a fresh engine, including for a move the 1-ply filter
    had dropped. That is the reason to make 2T/3T the levels a row is upgraded
    *to*, and to treat an n-ply upgrade as a convenience rather than a
    substitute for re-running the decision.
  - a bearoff position's displayed probabilities come from the rollout here,
    where a full run overrides them with exact bearoff-database lookups. The
    cubeful equity is the rollout's either way.

Only shared helpers are used to build the reply -- `compute_move_notation` to
name a candidate, `_probs_to_eval`/`_normalize_eval_level` to shape it -- so an
upgraded row cannot drift from the row it replaces.
"""

from __future__ import annotations

import time

from gvanalysis.position import EVAL_LEVELS, PositionState, _probs, parse_position_id
from gvformat.export import _normalize_eval_level, _probs_to_eval
from gvformat.legality import board_problems


class MoveEvalError(ValueError):
    """A request that names something this position cannot offer."""


def _post_move_accepts_beaver() -> bool:
    """Whether this bgsage's `post_move_analytics` takes `beaver`. The engine
    underneath it does; the Python wrapper did not expose it as of bgsage 2.x.

    It makes no difference to the number either way. A beaver only answers a
    double that was wrong to make, and the engine's own cube play never makes
    one, so a move's equity under correct play is the same with beavers
    allowed or not (checked across checker plays and cube decisions alike).
    It is passed where it can be only so the request says what it means."""
    import inspect
    from bgsage import BgBotAnalyzer
    return "beaver" in inspect.signature(BgBotAnalyzer.post_move_analytics).parameters


BEAVER_UPGRADES = _post_move_accepts_beaver()


def candidate_boards(state: PositionState) -> dict[str, list[int]]:
    """Every legal play for `state`, keyed by the notation it will be shown as.

    Keyed by notation because notation is what a client can hand back: the
    OGXM `move` steps a panel renders are themselves derived from it
    (`_build_alternatives` -> `_notation_to_steps`), so it is the one label
    that survives the round trip without a second encoder to keep in step.
    """
    from bgsage import possible_moves
    from bgsage.text_export import compute_move_notation

    boards: dict[str, list[int]] = {}
    for cand in possible_moves(state.board, state.die1, state.die2):
        board = list(cand)
        notation = compute_move_notation(
            state.board, board, state.die1, state.die2)
        # Distinct candidates give distinct notations; first-wins is only a
        # guard against a duplicate the move generator happened to emit twice.
        boards.setdefault(_key(notation), board)
    return boards


def _key(notation: str) -> str:
    """Collapse a notation to what identity should ignore -- spacing only."""
    return " ".join(str(notation).split())


def evaluate_moves(
    state: PositionState,
    notations: list[str],
    *,
    analyzer,
    level: str,
    jacoby: bool = True,
    beaver: bool = True,
) -> dict:
    """Evaluate the named moves of `state` at `analyzer`'s level.

    `jacoby` and `beaver` are the money-game rules, as for `/position`: an
    upgraded row has to be evaluated under the same rules as the list it is
    dropped into. `beaver` reaches bgsage only where it can (`BEAVER_UPGRADES`),
    which costs nothing: it cannot move a move's equity.

    Returns an OGXM-flavoured dict whose `moves` entries carry the same
    `equity` / `eval` / `eval_level` an `alternatives` row does, so a client
    can drop one into a row it already rendered. `diff` is deliberately absent:
    it is relative to the best move, and which move is best is a property of
    the list a caller is assembling, not of any single evaluation.

    Raises `MoveEvalError` for a position with no checker play, or a notation
    that is not one of its legal moves.
    """
    if not (state.die1 and state.die2) or state.force_cube:
        raise MoveEvalError("this position poses a cube decision, not a checker play")

    boards = candidate_boards(state)
    wanted: list[tuple[str, list[int]]] = []
    for notation in notations:
        board = boards.get(_key(notation))
        if board is None:
            raise MoveEvalError(f"{notation!r} is not a legal move for this position")
        wanted.append((notation, board))

    started = time.perf_counter()
    moves = [
        _evaluated(notation, board, state, analyzer, jacoby, beaver)
        for notation, board in wanted
    ]
    return {
        "position_id": None,  # filled in by the caller, which owns the id
                              # (`analyze_moves` does)
        "eval_level": level,
        "moves": moves,
        "elapsed": round(time.perf_counter() - started, 3),
    }


def _evaluated(notation: str, board: list[int], state: PositionState, analyzer,
               jacoby: bool = True, beaver: bool = True) -> dict:
    """One post-move board, in the vocabulary an `alternatives` row uses."""
    r = analyzer.post_move_analytics(
        board,
        cube_owner=state.cube_owner,
        cube_value=state.cube_value,
        away1=state.away1,
        away2=state.away2,
        is_crawford=state.is_crawford,
        jacoby=jacoby,
        **({"beaver": beaver} if BEAVER_UPGRADES else {}),
    )
    move: dict = {
        "notation": notation,
        "equity": round(float(r.cubeful_equity), 4),
        "cubeless_equity": round(float(r.cubeless_equity), 4),
        "eval": _probs_to_eval(_probs(r.probs)),
    }
    eval_level = _normalize_eval_level(r.eval_level)
    if eval_level is not None:
        move["eval_level"] = eval_level
    # A rollout's standard error is the honest part of a rolled-out number:
    # it is what says how much of a gap between two upgraded rows is signal.
    # Static evals have none, and the key is absent rather than zero.
    if r.cubeful_se is not None:
        move["se"] = round(float(r.cubeful_se), 4)
    return move


def analyze_moves(
    position_id: str,
    notations: list[str],
    *,
    level: str,
    analyzer=None,
    jacoby: bool = True,
    beaver: bool = True,
) -> dict:
    """`evaluate_moves` for a caller holding only a position id (XGID or OGID).

    The counterpart of `analyze_position` for named moves: it parses the id,
    refuses a board that cannot be a position (`board_problems`, the check
    `POST /position/moves` makes before the engine sees anything), builds an
    analyzer at `level` when none is passed, and echoes the id back as
    `position_id`. A prebuilt `analyzer` must have been built at `level`.

    Everything wrong with the *request* raises `MoveEvalError` -- an unknown
    level, an unreadable id, an impossible board, a cube decision, a move the
    position does not offer -- so a caller has one exception to answer for.
    """
    if level not in EVAL_LEVELS:
        raise MoveEvalError(f"level must be one of {list(EVAL_LEVELS)}, got {level!r}")
    try:
        state = parse_position_id(position_id)
    except Exception as exc:  # noqa: BLE001 - any parse failure is a bad request
        raise MoveEvalError(f"could not read position: {exc}") from None
    problems = board_problems(state.board)
    if problems:
        raise MoveEvalError("; ".join(problems))

    if analyzer is None:
        from bgsage import BgBotAnalyzer
        analyzer = BgBotAnalyzer(eval_level=level, cubeful=True)

    result = evaluate_moves(
        state, notations, analyzer=analyzer, level=level,
        jacoby=jacoby, beaver=beaver)
    result["position_id"] = position_id.strip()
    return result
