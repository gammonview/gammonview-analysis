# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""One checker borne off with both dice and pips to spare keeps its steps.

8/off with a 4-5 is 8 pips, not 9: the 5 bears off from the 4-point with one
to spare. The span matched neither die nor their sum, so the splitter fell
back to a single 8-pip step, ``pips`` (three bits) could not hold it, and the
alternative was written with no steps at all. A real bgsage-vs-player match
showed the engine's best play as "no move" and rated the played 8/3 4/off a
0.49 blunder against it -- right number, unreadable play. Had the player made
that play, the ply would have been restated as a set position.

Mirrors gvformat-js/test/test-bearoff-excess-steps.js.

Run directly:
    uv run python tests/test_bearoff_excess_steps.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from gvformat.export import (  # noqa: E402
    _compute_move_steps, _flip_board, _notation_to_steps, fit_alternative_steps,
)
from gvformat.ogid import parse_ogid  # noqa: E402

_checks = 0
_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _failures.append(label)
        print(f"FAIL  {label}")
    else:
        print(f"OK    {label}")


# The position from that match, White to play 4-5: White's checkers on its 8,
# 4, 4, 3, 3, 2, 2, 2 and seven on the 1; Black on the bar.
_OGID = "hllmmnnnooooooo:22334455566kkkp:W2N:45:B:R:1:0:5:55"


def main() -> int:
    st = parse_ogid(_OGID)
    board = list(st.board) if st.on_roll == "W" else _flip_board(list(st.board))
    check(board[8] == 1 and board[4] == 2 and board[3] == 2,
          "the position is White's, from White's side")

    steps = _notation_to_steps("8/off", True, 4, 5, board)
    check(steps == [{"from": 17, "pips": 4}, {"from": 21, "pips": 5}],
          "8/off with a 4-5 is 8/4 then 4/off with the 5")
    check(fit_alternative_steps(steps, "8/off", True, 4, 5) == steps,
          "and fits an alternative, rather than coming back empty")
    check(_notation_to_steps("8/off", True, 5, 4, board) == steps,
          "whichever order the dice are reported in")
    check(_notation_to_steps("8/off", True, 4, 5) == steps,
          "and with no board, smaller die first")

    # The same play as the *played* move, from the board diff.
    after = list(board)
    after[8] -= 1
    check(_compute_move_steps(board, after, True, 4, 5) == steps,
          "the played move splits the same way")

    # A stop the opponent holds is not taken: 7/off with a 2-6 would stop on
    # the 5 with the smaller die first.
    held = list(board)
    held[5] = -2
    check(_notation_to_steps("7/off", True, 2, 6, held)
          == [{"from": 18, "pips": 6}, {"from": 24, "pips": 2}],
          "a made point at the smaller die's stop sends the larger die first")

    # Exact spans are unchanged.
    check(_notation_to_steps("9/off", True, 4, 5, board)
          == [{"from": 16, "pips": 5}, {"from": 21, "pips": 4}],
          "an exact two-die bear-off is split as before")
    check(_notation_to_steps("4/off", True, 6, 1, board) == [{"from": 21, "pips": 4}],
          "a span one die covers stays one step")

    print()
    print(f"{_checks - len(_failures)}/{_checks} checks passed")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
