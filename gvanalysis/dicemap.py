# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""The dice distribution of a position: how each of the 36 rolls would go.

XG calls this the Dice Distribution, and its dice map is a 6x6 grid of the
equity after each roll. Which rolls depends on the decision:

  - a **cube decision** is about the roll to come, so it is the side on roll's
    own rolls from the board as it stands -- or, with ``doubled``, from the
    board after a double and a take;
  - a **checker play** is judged by what it leaves, so it is the opponent's
    replies to one play (``play``, or the best one). That is the map XG does
    not draw, and the one that shows *why* one play beats another: the rolls
    that hit, or fail to. Each reply also says what it leaves us at the cube,
    when the cube is ours to turn: double/take, double/pass, too good.

Every equity is the analysed player's, whoever rolls, so a number means the
same thing in both maps; a reply map's are the opponent's negated. And they
are on ``analyze_position``'s scale: an after-take map is in the cube *before*
the double, as a cube analysis's ``double_take_equity`` is, so its average sits
beside that number rather than half of it.

Each roll is its best play at ``level``, an n-ply search of every legal play,
so the weighted average is the decision's own equity one ply deeper: a 2-ply
map averages, to within a few hundredths, the 3-ply read. The opponent's cube
is not consulted before a reply: it is the rolls, as if they roll.

GammonView's position analyser draws the same map in the browser
(``gva/dicemap.js``), on the WASM engine's trimmed search. The two agree roll
for roll except where that search's cuts miss a play the full search finds
best, which its own tuning puts at a tie (``tests/test_dicemap.py``).

Cost is 21 checker searches, and at a checker play 21 cube decisions more: on
an M-series Mac about 0.3s at 2-ply and 3s at 3-ply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gvformat.met import mwc_anchors
from gvformat.ogid import flip_board

from .position import PositionState, parse_position_id
from .positionmove import _key, candidate_boards

if TYPE_CHECKING:
    from collections.abc import Iterable

#: The levels a map can be read at: n-ply only. Rolling out each of 21 rolls
#: is a feature of its own, not a level of this one.
DICE_LEVELS = ("1ply", "2ply", "3ply", "4ply")

#: The 21 rolls, and their weight out of 36.
ROLLS = [(d1, d2, 1 if d1 == d2 else 2) for d1 in range(1, 7) for d2 in range(d1, 7)]

_SWAP_OWNER = {"player": "opponent", "opponent": "player"}


class DiceMapError(ValueError):
    """A dice map that cannot be drawn: an unknown play, or a double that
    cannot be made."""


def _round4(x: float) -> float:
    return round(float(x), 4)


def _other_side(side: dict, board: list[int]) -> dict:
    """`side` (a board and its cube and score) from the other player's chair."""
    return {
        **side,
        "board": flip_board(board),
        "cube_owner": _SWAP_OWNER.get(side["cube_owner"], side["cube_owner"]),
        "away1": side["away2"],
        "away2": side["away1"],
    }


def _engine_kwargs(side: dict) -> dict:
    return {k: v for k, v in side.items() if k != "board"}


def _borne_off(board: Iterable[int]) -> bool:
    """Whether the side on roll has no checker left: none on a point, none on
    its bar (index 25)."""
    board = list(board)
    return board[25] == 0 and all(n <= 0 for n in board[:25])


def can_double(state: PositionState) -> bool:
    """Whether `state` is a cube decision whose side on roll may double, and so
    whether an after-take map means anything: not at a checker play, not with
    the cube on the other side, not in the Crawford game."""
    cube_decision = state.force_cube or not (state.die1 and state.die2)
    return cube_decision and state.cube_owner in ("centered", "player") and not state.is_crawford


def _rescale(equity: float, away1: int, away2: int, frm: int, to: int) -> float:
    """An equity read with the cube at `frm`, restated with it at `to`, through
    the match winning chances it stands for. Money's anchors are fixed, so
    there it is a plain ratio; at a match score a cube level moves both."""
    if not away1 or not away2:
        return equity * frm / to
    win_f, loss_f = mwc_anchors(away1, away2, frm)
    win_t, loss_t = mwc_anchors(away1, away2, to)
    mwc = (win_f + loss_f) / 2 + equity * (win_f - loss_f) / 2
    return (2 * mwc - (win_t + loss_t)) / (win_t - loss_t)


def _sweep(analyzer, side: dict) -> list[dict]:
    """The 21 rolls of whoever is on roll in `side`, each at the roller's own
    equity after their best play, with the board that play leaves.

    A roll with no play leaves the board as it is, so it is worth the other
    side's turn from there, negated: their no-double equity at the same depth.
    A result reading the roller's last checker off as a loss -- 1-ply's
    terminal misread, which luck guards against too -- is taken as a single
    win.
    """
    kwargs = _engine_kwargs(side)
    dance = None
    out = []
    for d1, d2, weight in ROLLS:
        moves = analyzer.checker_play(side["board"], d1, d2, **kwargs).moves
        if not moves:
            if dance is None:
                other = _other_side(side, side["board"])
                dance = -float(analyzer.cube_action(other["board"], **_engine_kwargs(other)).equity_nd)
            out.append({"dice": [d1, d2], "weight": weight, "equity": dance, "board": list(side["board"])})
            continue
        best = moves[0]
        equity = float(best.equity)
        if equity < -0.9 and _borne_off(best.board):
            equity = 1.0
        out.append({"dice": [d1, d2], "weight": weight, "equity": equity, "board": list(best.board)})
    return out


def _our_cube(analyzer, ours: dict) -> str | None:
    """The cube decision a reply leaves us, at the rolls' own depth:
    "double_take" or "double_pass" for a correct double, "too_good" for a
    position that would be a pass but is better played on, None for no
    double."""
    action = analyzer.cube_action(ours["board"], **_engine_kwargs(ours))
    if action.should_double:
        return "double_take" if action.should_take else "double_pass"
    return None if action.should_take else "too_good"


def die_averages(rolls: list[dict]) -> list[float]:
    """Each die's average over the 6x6 grid: the mean of the six cells in its
    column, so of the 11 rolls showing that number with the double counted
    once. The six together average to the map's own average."""
    equity = {tuple(r["dice"]): r["equity"] for r in rolls}
    return [
        _round4(sum(equity[(min(d, e), max(d, e))] for e in range(1, 7)) / 6)
        for d in range(1, 7)
    ]


def dice_distribution(
    position_id: str,
    *,
    level: str = "2ply",
    play: str | None = None,
    doubled: bool = False,
    analyzer=None,
    jacoby: bool | None = None,
    beaver: bool | None = None,
) -> dict:
    """The dice map of a position, as a dict.

    ``position_id`` is an XGID or an OGID, and which decision it poses is read
    as ``analyze_position`` reads it. At a checker play ``play`` names the play
    whose replies to map, in the notation ``analyze_position`` gives it; left
    as None it is the best play at ``level``. At a cube decision ``doubled``
    maps the rolls after a double and a take.

    ``analyzer`` is a prebuilt ``BgBotAnalyzer`` at ``level``, as for
    ``analyze_position``. ``jacoby`` and ``beaver`` are the money rules, read
    from the id when None exactly as there.

    Returns ``{"position_id", "eval_level", "roller", "doubled", "play",
    "average", "won", "rolls", "die_averages", "elapsed"}``. ``roller`` is
    "mover" for a cube decision and "opponent" for a checker play; ``play`` is
    the play mapped, or None. Each of the 21 ``rolls`` is ``{"dice": [a, b]
    (a <= b), "weight": 1 or 2 (out of 36), "equity"}``, and at a checker play
    where the cube is ours after their roll, ``"cube"`` too (``_our_cube``).
    A play that bears off the last checker has no replies: ``won`` is True,
    ``rolls`` is empty and ``average`` None.

    Raises ``DiceMapError`` for a ``play`` that is not one (or is given at a
    cube decision), or ``doubled`` where the side on roll cannot double;
    ``ValueError`` for another level.
    """
    import time

    if level not in DICE_LEVELS:
        raise ValueError(f"level must be one of {list(DICE_LEVELS)}, got {level!r}")
    state = parse_position_id(position_id)
    checker = bool(state.die1 and state.die2) and not state.force_cube
    if doubled and not can_double(state):
        raise DiceMapError("the side on roll cannot double here")
    if play is not None and not checker:
        raise DiceMapError("a cube decision has no play to map the replies to")

    if analyzer is None:
        from bgsage import BgBotAnalyzer
        analyzer = BgBotAnalyzer(eval_level=level, cubeful=True)
    money = not state.away1 and not state.away2
    if jacoby is None:
        jacoby = state.jacoby if state.jacoby is not None else True
    if beaver is None:
        beaver = state.beaver if state.beaver is not None else True
    ours = {
        "board": list(state.board), "cube_value": state.cube_value, "cube_owner": state.cube_owner,
        "away1": state.away1, "away2": state.away2, "is_crawford": state.is_crawford,
        "jacoby": bool(jacoby) and money, "beaver": bool(beaver) and money,
    }

    started = time.perf_counter()
    result = {
        "position_id": position_id.strip(),
        "eval_level": level,
        "roller": "opponent" if checker else "mover",
        "doubled": bool(doubled),
        "play": None,
    }

    if checker:
        boards = candidate_boards(state)
        if play is None:
            from bgsage.text_export import compute_move_notation
            best = analyzer.checker_play(state.board, state.die1, state.die2, **_engine_kwargs(ours)).moves[0]
            after = list(best.board)
            play = compute_move_notation(state.board, after, state.die1, state.die2)
        else:
            after = boards.get(_key(play))
            if after is None:
                raise DiceMapError(f"{play!r} is not a legal play of {state.die1}{state.die2} here")
        result["play"] = " ".join(str(play).split())
        roller = _other_side(ours, after)
        # The mover's checkers, seen from the replying side, are its negatives
        # and its bar (index 0): none left is a game already over.
        if roller["board"][0] == 0 and all(n >= 0 for n in roller["board"]):
            result.update(average=None, won=True, rolls=[], die_averages=[])
            result["elapsed"] = round(time.perf_counter() - started, 3)
            return result
    else:
        roller = dict(ours)
        if doubled:
            roller["cube_value"] = state.cube_value * 2
            roller["cube_owner"] = "opponent"

    # Our cube after their reply: ours to turn unless they own it, and not in
    # the Crawford game. The play moved no cube, so it is as the decision had it.
    ours_to_turn = checker and state.cube_owner in ("centered", "player") and not state.is_crawford
    sign = -1 if checker else 1
    rolls = []
    for r in _sweep(analyzer, roller):
        equity = r["equity"]
        if doubled:
            equity = _rescale(equity, state.away1, state.away2, roller["cube_value"], state.cube_value)
        row = {"dice": r["dice"], "weight": r["weight"], "equity": _round4(sign * equity)}
        if ours_to_turn:
            row["cube"] = _our_cube(analyzer, {**ours, "board": flip_board(r["board"])})
        rolls.append(row)

    result.update(
        average=_round4(sum(r["weight"] * r["equity"] for r in rolls) / 36),
        won=False,
        rolls=rolls,
        die_averages=die_averages(rolls),
    )
    result["elapsed"] = round(time.perf_counter() - started, 3)
    return result


_CUBE_TAG = {"double_take": "D/T", "double_pass": "D/P", "too_good": "TG"}


def print_dice_map(result: dict) -> None:
    """The map as XG lays it out: a 6x6 grid, each die's average beneath."""
    if result["roller"] == "opponent":
        print(f"Replies to {result['play']}, at {result['eval_level']} (your equity):")
    else:
        after = ", after a double and a take" if result["doubled"] else ""
        print(f"Rolls for the side on roll{after}, at {result['eval_level']}:")
    if result["won"]:
        print("  The play bears off the last checker: there are no replies.")
        return
    by_dice = {tuple(r["dice"]): r for r in result["rolls"]}
    tagged = any("cube" in r for r in result["rolls"])
    width = 11 if tagged else 8

    def cell(text: str) -> str:
        return text.ljust(width)

    print("       " + "".join(cell(str(d)) for d in range(1, 7)))
    for a in range(1, 7):
        row = []
        for b in range(1, 7):
            r = by_dice[(min(a, b), max(a, b))]
            tag = _CUBE_TAG.get(r.get("cube"), "")
            row.append(cell(f"{r['equity']:+.3f}" + (f" {tag}" if tag else "")))
        print(f"  {a}    " + "".join(row))
    print("  Avg  " + "".join(cell(f"{x:+.3f}") for x in result["die_averages"]))
    print(f"  Average {result['average']:+.4f}")
    if tagged:
        counts = {k: 0 for k in _CUBE_TAG}
        for r in result["rolls"]:
            if r.get("cube"):
                counts[r["cube"]] += r["weight"]
        doubles = counts["double_take"] + counts["double_pass"]
        line = f"  After their reply you have a double on {doubles} of 36 rolls"
        if counts["double_pass"]:
            line += f", {counts['double_pass']} of them a pass (D/P)"
        if counts["too_good"]:
            line += f"; {counts['too_good']} are too good to double (TG)"
        print(line + ".")
