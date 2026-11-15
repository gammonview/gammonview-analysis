# SPDX-License-Identifier: MIT
"""Native bgsage's answers for the parity test: test/fixtures/parity.json.

    uv run --no-project --with "bgsage==$(cat bgsage.version)" python -I scripts/fixtures.py

Twelve positions from one seeded game, a third of them at a match score, each
read by BgBotAnalyzer at 2ply, 3ply and truncated2 (and a few at truncated3),
plus single plays rolled out as `POST /position/moves` does -- the analyzer gvanalysis
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
# Single plays rolled out, as `POST /position/moves` does (post_move_analytics):
# the best, second and worst of each position at 2T, and of the first three
# at 3T, which is slow enough that three are plenty.
out["rollout"] = {}
for level, n in (("truncated2", len(positions)), ("truncated3", 3)):
    a = BgBotAnalyzer(eval_level=level)
    rows = []
    for p, plays in zip(positions[:n], out["checker"]["2ply"][:n]):
        picks = [plays[0]["board"], plays[1]["board"], plays[-1]["board"]] if len(plays) > 2 else [m["board"] for m in plays]
        picks = picks if level == "truncated2" else picks[:2]
        res = []
        for b in picks:
            r = a.post_move_analytics(b, cube_owner=p["cube_owner"], cube_value=p["cube_value"], away1=p["away1"],
                                      away2=p["away2"], is_crawford=p["is_crawford"], jacoby=p["jacoby"])
            res.append({"board": b, "equity": r.cubeful_equity, "cubeless_equity": r.cubeless_equity})
        rows.append(res)
    out["rollout"][level] = rows
a = BgBotAnalyzer(eval_level="truncated3")._analyzer
out["cube"]["truncated3"] = [{k: r[k] for k in ("equity_nd", "equity_dt", "equity_dp", "should_double", "should_take")}
                             for r in (a.cube_action_analytics(p["board"], **settings(p)) for p in positions[:3])]
path = Path(__file__).resolve().parents[1] / "test" / "fixtures" / "parity.json"
path.write_text(json.dumps(out) + "\n")
print(f"wrote {path}: {len(positions)} positions, bgsage {out['bgsage']}")
