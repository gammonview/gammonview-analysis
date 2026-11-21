# SPDX-License-Identifier: MIT
"""gvanalysis's dice maps for the dice map's parity test: test/fixtures/dicemap.json.

    npm run fixtures:dicemap      # from gvengine-js; runs in the repo's own env

Unlike parity.json this needs gvanalysis, not bare bgsage: the numbers under
test are `dice_distribution`'s, which is the map's other half. Each position is
mapped at 2-ply and 3-ply -- a cube decision as it stands and, where the side
on roll may double, after a double and a take; a checker play's replies to its
best play, with `after` the OGID that play leaves, which is what `dice_map`
takes. A cube decision's 2-ply map carries its `market_losers` too, null where
there is no market to lose. Regenerate when bgsage.version moves; the test refuses fixtures from
another version.
"""
import importlib.metadata
import json
from pathlib import Path

from gvanalysis.dicemap import can_double, dice_distribution, market_losers
from gvanalysis.position import parse_position_id
from gvanalysis.positionmove import _key, candidate_boards
from gvformat.ogid import board_to_ogid, parse_ogid

POSITIONS = [
    "3cccccehhhjjjjj:1666677888dddlo:N0N::W:C:0:0:0:0",      # money, centred
    "00ccccchhhjjjjj:22446677888abfm:W1N::B:C:1:1:1:0",      # money, ours at 2
    "ccccchiijjjllnn:33444566688ddoo:N0N::B:C:5:3:9:0",      # 9-point match
    "44bbccccehhjjkk:11666888ddgiinp:N0N::W:C:3:2:8:0",      # 8-point match
    "cceeeehhjjjkkkm:1123344666ggllo:N0N:43:B:R:2:1:7:0",    # replies, a double after many
    "33ccjjjkkmmnooo:22244555666677e:B1N:11:B:R:1:3:4:0",    # replies, their cube
    "00cccehhhhjjjjj:1133666788dddoo:N0N:52:B:R:3:3:3:0",    # replies from the bar
    "13cchhhhjjjjjkl:66666888dddddmn:N0N:22:B:R:1:2:5:0",    # replies after a double hit
    "jjkln:25:W1N:54:W:R:0:0:0:0",                           # the last roll: no replies
]

KEEP = ("roller", "doubled", "average", "won", "rolls", "die_averages")


def after_ogid(ogid: str, play: str) -> str:
    """The OGID `play` leaves, still with its mover on roll: `position_after`'s."""
    s = parse_ogid(ogid)
    board = candidate_boards(parse_position_id(ogid))[_key(play)]
    return board_to_ogid(board, mover_is_white=s.mover_is_white, cube_value=s.cube_value,
                         cube_owner=s.cube_owner, dice=(s.die1, s.die2), score_white=s.score_white,
                         score_black=s.score_black, match_length=s.match_length, crawford=s.crawford)


maps = []
for ogid in POSITIONS:
    state = parse_position_id(ogid)
    for level, ply in (("2ply", 2), ("3ply", 3)):
        if state.die1 and state.die2 and not state.force_cube:
            got = dice_distribution(ogid, level=level)
            maps.append({"ogid": ogid, "ply": ply, "after": after_ogid(ogid, got["play"]),
                         **{k: got[k] for k in KEEP}})
            continue
        for doubled in (False, True) if can_double(state) else (False,):
            got = dice_distribution(ogid, level=level, doubled=doubled)
            entry = {"ogid": ogid, "ply": ply, "after": None, **{k: got[k] for k in KEEP}}
            # Market losers at 2-ply only: at 3-ply each is a minute natively.
            if ply == 2 and not doubled:
                market = market_losers(ogid, level=level)
                entry["market"] = market and {k: market[k] for k in ("pass", "too_good", "rolls")}
            maps.append(entry)

out = Path(__file__).resolve().parent.parent / "test" / "fixtures" / "dicemap.json"
out.write_text(json.dumps({"bgsage": importlib.metadata.version("bgsage"), "maps": maps},
                          separators=(",", ":")) + "\n")
print(f"wrote {len(maps)} maps to {out}")
