# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""An illegal play is flagged on every checker branch, not only the one that
searches the move list.

``game_eval._eval_checker_decision`` judges a play down one of three branches.
The general one found illegal plays by failing to find the played board among
the engine's moves. The other two never search: a forced move scored its one
legal play as the play made, and an already-decided position (every play
scoring the same) listed the moves and stopped. So an illegal play landing on
either went through as an ordinary one -- a real ``.mat`` export recorded a 2-1
as ``10/6`` in a decided bear-off, and it came out with no flag while XG,
reading the same match, marked it illegal.

Both positions here are built to land on one of those branches, and each is
checked to really do so (one legal play; every play equal), since a position
that drifted onto the general branch would pass this test for the wrong reason.

Needs the engine; 1-ply, so it is quick.

Run: uv run python tests/test_illegal_play_branches.py   (exit 0 = all passed)
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from bgsage import BgBotAnalyzer
    from bgsage.board import possible_moves
except ImportError:
    print("SKIP: bgsage not installed")
    sys.exit(0)

from gvanalysis.game_eval import _EvalCtx, _eval_checker_decision

_passed = 0
_failed = 0


def check(cond: bool, msg: str) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"OK    {msg}")
    else:
        _failed += 1
        print(f"FAIL  {msg}")


def _board(mine: dict[int, int], theirs: dict[int, int]) -> list[int]:
    """Mover-perspective board: own checkers positive on their points."""
    b = [0] * 26
    for p, n in mine.items():
        b[p] = n
    for p, n in theirs.items():
        b[p] = -n
    return b


def _dec(board, played, dice) -> dict:
    return {
        "kind": "checker", "board": board, "board_played": played,
        "dice": list(dice), "cube_value": 1, "cube_owner": "centered",
        "player": "P1", "is_p1": True, "away1": 0, "away2": 0,
        "is_crawford": False, "no_legal": False,
    }


def _ctx(analyzer, *, count_illegal: bool = False) -> _EvalCtx:
    return _EvalCtx(
        analyzer=analyzer, base_analyzer=None,
        mid_analyzer_checker=None, mid_analyzer_cube=None, luck_analyzer=analyzer,
        close_threshold_checker=None, close_threshold_cube=None,
        error_threshold_checker=0.0, error_threshold_cube=0.0,
        verbose=False, all_moves=False, count_illegal=count_illegal,
        level_display="1ply", game={"game_number": 1},
    )


def _entry(res) -> dict:
    return next(e for e in res.entries if e.get("kind") == "checker")


def _judge(name: str, engine, dec: dict) -> None:
    res = _eval_checker_decision(dec, _ctx(engine))
    entry = _entry(res)
    check(entry.get("illegal_move") is True, f"{name}: the play is flagged illegal")
    check(res.illegal_moves == 1, f"{name}: and counted as one illegal move")
    played = [o for o in entry["move_options"] if o.get("played")]
    check(len(played) == 1 and played[0].get("illegal_move") is True,
          f"{name}: the played option is the illegal one, not a legal play")
    check(entry.get("counted") is False and res.p1_dec == 0,
          f"{name}: and stays out of PR by default")
    counted = _eval_checker_decision(dec, _ctx(engine, count_illegal=True))
    check(counted.p1_dec == 1, f"{name}: --count-illegal counts it")


def main() -> int:
    engine = BgBotAnalyzer(eval_level="1ply", cubeful=True)

    # --- 1. A forced move ------------------------------------------------------
    # Two checkers on the ace point and a 2-1: bearing both off is the only
    # legal play. Recorded instead as one checker off.
    board = _board({1: 2}, {19: 13})
    legal = possible_moves(board, 2, 1)
    check(len(legal) == 1, f"forced: the position has one legal play ({len(legal)})")
    _judge("forced", engine, _dec(board, _board({1: 1}, {19: 13}), (2, 1)))

    # --- 2. An already-decided position ---------------------------------------
    # Two checkers left against fourteen with one already off: every 2-1 wins a
    # single game for certain, so every play scores the same. Recorded as 6/2,
    # four pips off a roll of three.
    board = _board({6: 1, 5: 1}, {19: 14})
    legal = possible_moves(board, 2, 1)
    eqs = {round(float(m.equity), 6) for m in engine.checker_play(board, 2, 1).moves}
    check(len(legal) >= 2 and len(eqs) == 1,
          f"decided: {len(legal)} legal plays, all scoring the same ({sorted(eqs)})")
    _judge("decided", engine, _dec(board, _board({5: 1, 2: 1}, {19: 14}), (2, 1)))

    # --- 3. A legal play on those branches is still an ordinary play -----------
    board = _board({1: 2}, {19: 13})
    entry = _entry(_eval_checker_decision(
        _dec(board, list(possible_moves(board, 2, 1)[0]), (2, 1)), _ctx(engine)))
    check(not entry.get("illegal_move")
          and [o.get("played") for o in entry["move_options"]] == [True],
          "forced: the legal play is marked played and not illegal")

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
