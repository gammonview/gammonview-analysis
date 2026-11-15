# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""``analyze_moves`` — named moves of one checker play, evaluated on their own.

This is the half of "upgrade this move" that lives in the library; the site's
server and the desktop helper both call it. What is worth pinning is the
contract a caller leans on, not the engine's numbers:

  1. **A row is an `alternatives` row.** It carries `equity`, `eval` and
     `eval_level`, so a client can drop it into a list it already rendered.
  2. **A bad request is a `MoveEvalError`** -- one exception for a move the
     position does not offer, a cube decision, or an unreadable id -- so a
     caller can answer "your request was wrong" without sorting engine
     failures from it.
  3. **The id is echoed**, stripped, since the caller owns it.

Requires the bgsage engine (the ``[engine]`` extra).

Run directly:
    uv run python tests/test_position_moves.py   (exit 0 = all passed)
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from gvanalysis.positionmove import MoveEvalError, analyze_moves

_checks = 0
_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _failures.append(label)
        print(f"   FAIL: {label}")
    else:
        print(f"   ok:   {label}")


def raises_move_error(fn, label: str) -> None:
    try:
        fn()
    except MoveEvalError:
        check(True, label)
    else:
        check(False, f"{label} (no MoveEvalError)")


#: The opening 31, and the same board with no dice (a cube decision).
OPENING_31_OGID = "11ccccchhhjjjjj:66666888dddddoo:N0N:13:B:R:0:0:0:0"
OPENING_CUBE_XGID = "XGID=-b----E-C---eE---c-e----B-:0:0:-1:00:0:0:0:0:8"


def main() -> int:
    print("1. a legal move comes back as an alternatives-shaped row")
    res = analyze_moves(OPENING_31_OGID, ["8/5 6/5", "24/23 13/10"], level="1ply")
    rows = res["moves"]
    check([m["notation"] for m in rows] == ["8/5 6/5", "24/23 13/10"],
          "rows come back in the order asked, under the notation given")
    top = rows[0]
    check(isinstance(top["equity"], float), "a row has an equity")
    check(set(top["eval"]) == {"win", "gammon_win", "bg_win",
                               "gammon_loss", "bg_loss", "equity"},
          "and an Eval object with the OGXM keys")
    check(top["eval_level"] == "1ply",
          f"and a canonical eval_level (got {top.get('eval_level')!r})")
    check(res["eval_level"] == "1ply", "the reply names the level")
    check(top["equity"] > rows[1]["equity"], "8/5 6/5 beats 24/23 13/10 on the 31")
    check("diff" not in top, "diff is absent: it belongs to the list, not a row")

    print("2. the position id is echoed, stripped")
    check(res["position_id"] == OPENING_31_OGID, "echoed")
    padded = analyze_moves(f"  {OPENING_31_OGID}\n", ["8/5 6/5"], level="1ply")
    check(padded["position_id"] == OPENING_31_OGID, "surrounding whitespace is dropped")

    print("3. a request the position cannot satisfy is a MoveEvalError")
    raises_move_error(
        lambda: analyze_moves(OPENING_31_OGID, ["24/1"], level="1ply"),
        "a move the position does not offer")
    raises_move_error(
        lambda: analyze_moves(OPENING_CUBE_XGID, ["8/5 6/5"], level="1ply"),
        "a cube decision has no checker play to name a move of")
    raises_move_error(
        lambda: analyze_moves("not a position", ["8/5 6/5"], level="1ply"),
        "an unreadable id")
    raises_move_error(
        lambda: analyze_moves(OPENING_31_OGID, ["8/5 6/5"], level="9ply"),
        "an unknown level")

    print(f"\n{_checks - len(_failures)}/{_checks} checks passed.")
    if _failures:
        print("FAILURES:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
