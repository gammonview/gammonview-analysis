# SPDX-License-Identifier: MIT
"""Native bgsage's answers for the parity test: test/fixtures/parity.json.

    uv run --no-project --with "bgsage==$(cat bgsage.version)" python -I scripts/fixtures.py

Twelve positions from one seeded game, a third of them at a match score, each
read by BgBotAnalyzer at 2ply, 3ply and truncated2 -- the analyzer gvanalysis
builds, so these are the Studio's and the helper's numbers. Regenerate when
bgsage.version moves; the test refuses fixtures from another version.
"""
import importlib.metadata
import json
import random
from pathlib import Path

import bgbot_cpp as cpp
from bgsage import STARTING_BOARD, BgBotAnalyzer

OWNERS = ("centered", "player", "opponent")
rng = random.Random(471)
positions, board = [], list(STARTING_BOARD)
for turn in range(80):
    d1, d2 = rng.randint(1, 6), rng.randint(1, 6)
    plays = list(cpp.possible_moves(board, d1, d2))
    if turn % 6 == 2 and plays and len(positions) < 12:
        match = len(positions) % 3 == 1
        positions.append(dict(board=board, dice=[d1, d2], cube_value=2 if len(positions) % 4 == 3 else 1,
                              cube_owner=OWNERS[len(positions) % 4 == 3],
                              away1=5 if match else 0, away2=3 if match else 0, is_crawford=False,
                              jacoby=not match, beaver=not match))
    after = list(cpp.flip_board(rng.choice(plays) if plays else board))
    done = sum(x for x in after[1:25] if x > 0) + after[25] == 0 or -sum(x for x in after[1:25] if x < 0) + after[0] == 0
    board = list(STARTING_BOARD) if done else after


def settings(p):
    return {k: p[k] for k in ("cube_value", "cube_owner", "away1", "away2", "is_crawford", "jacoby", "beaver")}


out = {"bgsage": importlib.metadata.version("bgsage"), "positions": positions, "checker": {}, "cube": {}}
for level in ("2ply", "3ply"):
    a = BgBotAnalyzer(eval_level=level)._analyzer
    out["checker"][level] = [[{"board": list(m["board"]), "equity": m["equity"]}
                              for m in a.checker_play_analytics(p["board"], *p["dice"], **settings(p))]
                             for p in positions]
for level in ("2ply", "3ply", "truncated2"):
    a = BgBotAnalyzer(eval_level=level)._analyzer
    out["cube"][level] = [{k: r[k] for k in ("equity_nd", "equity_dt", "equity_dp", "should_double", "should_take")}
                          for r in (a.cube_action_analytics(p["board"], **settings(p)) for p in positions)]
path = Path(__file__).resolve().parents[1] / "test" / "fixtures" / "parity.json"
path.write_text(json.dumps(out) + "\n")
print(f"wrote {path}: {len(positions)} positions, bgsage {out['bgsage']}")
