# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""``dice_distribution`` -- the equity after each of the 36 rolls.

What is worth pinning is that the map agrees with the analysis it explains,
not the engine's numbers, which other tests own:

  1. **A cube map averages the decision.** Each roll read at 2-ply makes the
     weighted average the no-double equity at 3-ply, to a few hundredths.
  2. **An after-take map is in the cube before the double.** Its average sits
     beside ``double_take_equity`` (beavers off: a beavered D/T is a cube at
     four), at money and at a match score, where cube levels do not scale.
  3. **A reply map is the mover's equity.** Its average sits near the play's
     own 3-ply equity -- above it where the opponent would double first, which
     the map does not consult.
  4. **The cube marks appear only where the cube is ours to turn.**
  5. **A bad request is a ``DiceMapError``**, an unknown level a ``ValueError``.
  6. **Market losers are the reply map one roll out.** A roll's count is the
     D/P and TG marks of the reply map to its play, one ply shallower; and
     there are none to count where the cube is not ours to turn. Their 1-ply
     cube decisions go to bgsage as one batch, which must read every one as
     ``cube_action`` does -- the check a bgsage upgrade has to pass.

Requires the bgsage engine (the ``[engine]`` extra).

Run directly:
    uv run python tests/test_dicemap.py   (exit 0 = all passed)
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from gvanalysis.dicemap import (DiceMapError, _our_cube, _our_cubes, _our_side, _other_side,
                               _sweep, dice_distribution, market_losers)
from gvformat.ogid import flip_board
from gvanalysis.position import analyze_position, parse_position_id

_checks = 0
_failures: list[str] = []

NEAR = 0.04

#: Cube decisions: money, centred; money, ours; a match score.
CUBE_MONEY = "3cccccehhhjjjjj:1666677888dddlo:N0N::W:C:0:0:0:0"
CUBE_OURS = "00ccccchhhjjjjj:22446677888abfm:W1N::B:C:1:1:1:0"
CUBE_MATCH = "ccccchiijjjllnn:33444566688ddoo:N0N::B:C:5:3:9:0"
#: Checker plays: a match score, cube centred; money, their cube; a last roll.
CHECKER_MATCH = "cceeeehhjjjkkkm:1123344666ggllo:N0N:43:B:R:2:1:7:0"
CHECKER_THEIRS = "33ccjjjkkmmnooo:22244555666677e:B1N:11:B:R:1:3:4:0"
CHECKER_LAST = "jjkln:25:W1N:54:W:R:0:0:0:0"


def check(cond: bool, label: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _failures.append(label)
        print(f"   FAIL: {label}")
    else:
        print(f"   ok:   {label}")


def raises(fn, error, label: str) -> None:
    try:
        fn()
    except error:
        check(True, label)
    else:
        check(False, f"{label} (no {error.__name__})")


def main() -> int:
    print("1. a cube map averages the 3-ply no-double equity")
    for ogid in (CUBE_MONEY, CUBE_OURS, CUBE_MATCH):
        dice = dice_distribution(ogid)
        read = analyze_position(ogid)
        check(dice["roller"] == "mover" and len(dice["rolls"]) == 21, f"21 rolls for the mover: {ogid}")
        check(sum(r["weight"] for r in dice["rolls"]) == 36, "weights out of 36")
        gap = dice["average"] - read["cube"]["no_double_equity"]
        check(abs(gap) < NEAR, f"average within {NEAR} of ND ({gap:+.4f})")
        check(abs(sum(dice["die_averages"]) / 6 - dice["average"]) < 1e-3, "die averages make the average")

    print("2. an after-take map is in the cube before the double")
    for ogid in (CUBE_MONEY, CUBE_OURS, CUBE_MATCH):
        dice = dice_distribution(ogid, doubled=True, beaver=False)
        read = analyze_position(ogid, beaver=False)
        gap = dice["average"] - read["cube"]["double_take_equity"]
        check(dice["doubled"] and abs(gap) < NEAR, f"average within {NEAR} of D/T ({gap:+.4f}): {ogid}")

    print("3. a reply map is the mover's equity after the play")
    read = analyze_position(CHECKER_MATCH)
    best = read["alternatives"][0]
    dice = dice_distribution(CHECKER_MATCH, play=best["notation"])
    check(dice["roller"] == "opponent" and dice["play"] == best["notation"], "the replies to the play named")
    gap = dice["average"] - best["equity"]
    # Above, by up to their cube's worth; below, only by depth.
    check(-NEAR < gap < 0.25, f"average near the play's 3-ply equity ({gap:+.4f})")
    default = dice_distribution(CHECKER_MATCH)
    check(default["play"] is not None and default["rolls"], "no play named maps the best play's replies")
    last = dice_distribution(CHECKER_LAST, play="5/off 2/off")
    check(last["won"] and last["rolls"] == [] and last["average"] is None,
          "a play bearing off the last checker has no replies")

    print("4. cube marks only where the cube is ours to turn")
    marks = [r.get("cube") for r in dice["rolls"]]
    check(all("cube" in r for r in dice["rolls"]), "a centred cube: every reply says")
    check(set(marks) <= {None, "double_take", "double_pass", "too_good"}, "in the four values")
    check(any(marks), "this position leaves doubles after some replies")
    theirs = dice_distribution(CHECKER_THEIRS)
    check(not any("cube" in r for r in theirs["rolls"]), "their cube: no marks")
    check(not any("cube" in r for r in dice_distribution(CUBE_MONEY)["rolls"]), "a cube map: no marks")

    print("5. a request the position cannot satisfy is a DiceMapError")
    raises(lambda: dice_distribution(CHECKER_MATCH, play="24/1"), DiceMapError, "a play the position does not offer")
    raises(lambda: dice_distribution(CUBE_MONEY, play="8/5 6/5"), DiceMapError, "a play at a cube decision")
    raises(lambda: dice_distribution(CHECKER_MATCH, doubled=True), DiceMapError, "after a take, at a checker play")
    raises(lambda: dice_distribution("3cccccehhhjjjjj:1666677888dddlo:W1N::W:C:0:0:0:0", doubled=True),
           DiceMapError, "after a take, with their cube")
    raises(lambda: dice_distribution(CUBE_MONEY, level="truncated2"), ValueError, "a rollout level")

    print("6. market losers are the reply map one roll out")
    market = market_losers(CUBE_MONEY)
    check(market is not None and len(market["rolls"]) == 21, "21 of our rolls")
    check(market["pass"] == sum(r["weight"] * r["pass"] for r in market["rolls"]), "the total weights each roll")
    check(all(0 <= r["pass"] + r["too_good"] <= 36 for r in market["rolls"]), "each roll out of 36 replies")
    roll = max(market["rolls"], key=lambda r: r["pass"] + r["too_good"])
    check(roll["pass"] + roll["too_good"] > 0, "this position has market losers")
    a, b = roll["dice"]
    with_dice = CUBE_MONEY.replace("N0N::W:C", f"N0N:{a}{b}:W:R")
    play = dice_distribution(with_dice)["play"]
    replies = dice_distribution(with_dice, level="1ply", play=play)
    lost = {k: sum(r["weight"] for r in replies["rolls"] if r.get("cube") == k) for k in ("double_pass", "too_good")}
    check((roll["pass"], roll["too_good"]) == (lost["double_pass"], lost["too_good"]),
          f"{a}-{b}: {roll['pass']}+{roll['too_good']} as its 1-ply reply map marks them")
    check(market_losers(CHECKER_MATCH) is None, "none at a checker play")
    check(market_losers("3cccccehhhjjjjj:1666677888dddlo:W1N::W:C:0:0:0:0") is None, "none with their cube")
    raises(lambda: market_losers(CUBE_MONEY, level="1ply"), ValueError, "no reply level below 1-ply")
    from bgsage import BgBotAnalyzer
    one = BgBotAnalyzer(eval_level="1ply", cubeful=True)
    for ogid in (CUBE_MONEY, CUBE_MATCH):
        ours = _our_side(parse_position_id(ogid), None, None)
        sides = [{**ours, "board": flip_board(reply["board"])}
                 for r in _sweep(one, ours)[::4]
                 for reply in _sweep(one, _other_side(ours, r["board"]))]
        batch = _our_cubes(one, "1ply", sides)
        check(batch == [_our_cube(one, side) for side in sides],
              f"the batched 1-ply cube reads {len(sides)} positions as cube_action does: {ogid}")

    print(f"\n{_checks - len(_failures)}/{_checks} checks passed.")
    if _failures:
        print("FAILURES:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
