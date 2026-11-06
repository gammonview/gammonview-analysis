# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""A money session is analysed under the rules it was played under.

The match reader always took Jacoby and beavers off the file, and wrote them
back into the summary -- and nothing between the two passed them to the engine,
so every money session was judged under bgsage's defaults, both on. A session
played without Jacoby had its gammons counted with the cube centred, which
moves cube verdicts, checker equities, error sizes and PR across the whole
session (fixed in 3.3.0).

Two halves, because the failure was a *plumbing* one. A recording analyzer
checks that every engine call a decision makes is handed the rules -- the guard
against a new call site that forgets them. And the real engine, on the money
fixture, checks the rules reach the numbers: a centred-cube decision comes out
differently with and without Jacoby, and luck follows the document's rules.

Requires the bgsage engine (the ``[engine]`` extra).

Run: uv run python tests/test_money_rules.py   (exit 0 = all passed)
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bgsage import BgBotAnalyzer

from gvanalysis.game_eval import _EvalCtx, _eval_checker_decision, _eval_cube_decision
from gvanalysis.luck import fill_luck
from gvanalysis.ogxm_reconstructor import reconstruct_decisions_from_ogxm
from gvformat import read_gvab

#: A money session that states neither rule (`; [Jacoby "Off"]`, `[Beaver "Off"]`).
FIXTURE = _ROOT / "tests" / "golden" / "B4_SrGcsKAQmoTyHlgJCbM.fast.gvab"

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


class _Recording:
    """The real analyzer, with every rules-taking call's keywords written down."""

    def __init__(self, inner, log):
        self._inner = inner
        self._log = log

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name not in ("checker_play", "cube_action", "post_move_analytics"):
            return attr

        def call(*args, **kw):
            self._log.append((name, kw.get("jacoby"), kw.get("beaver")))
            return attr(*args, **kw)
        return call


def _ctx(analyzer, *, jacoby, beaver):
    return _EvalCtx(
        analyzer=analyzer, base_analyzer=None,
        mid_analyzer_checker=None, mid_analyzer_cube=None, luck_analyzer=analyzer,
        close_threshold_checker=None, close_threshold_cube=None,
        error_threshold_checker=0.0, error_threshold_cube=0.0,
        verbose=False, all_moves=False, count_illegal=False,
        level_display="1ply", game={"game_number": 1},
        jacoby=jacoby, beaver=beaver,
    )


def main() -> int:
    doc = read_gvab(FIXTURE.read_bytes())
    check(doc.get("match_length") == 0 and not doc.get("jacoby") and not doc.get("beaver"),
          "the fixture is a money session played with neither rule")
    decisions = reconstruct_decisions_from_ogxm(doc)[0]["decisions"]
    # Not the opening, whose luck is measured without the 21-roll sweep.
    checker = next(d for d in decisions[2:] if d["kind"] == "checker" and d.get("board_played"))
    cube = next(d for d in decisions if d["kind"] == "cube" and d["cube_owner"] == "centered")

    engine = BgBotAnalyzer(eval_level="1ply", cubeful=True)

    # Every engine call is handed the session's rules.
    log: list = []
    rec = _Recording(engine, log)
    _eval_checker_decision(checker, _ctx(rec, jacoby=False, beaver=False))
    _eval_cube_decision(cube, _ctx(rec, jacoby=False, beaver=False))
    check(bool(log), f"the decisions reached the engine ({len(log)} calls)")
    check(all(j is False for _n, j, _b in log),
          "every call was told no Jacoby")
    check(all(b is False for n, _j, b in log if n != "post_move_analytics"),
          "every call that takes a beaver rule was told no beavers")

    # At a match score neither rule exists, whatever the context says.
    at_match = {**cube, "away1": 3, "away2": 5}
    check(_ctx(None, jacoby=True, beaver=True).rules(at_match)
          == {"jacoby": False, "beaver": False},
          "a match score has neither rule")

    # The rules reach the numbers: with the cube centred, Jacoby decides whether
    # gammons count, so a decision with gammons in it moves.
    on = _eval_cube_decision(cube, _ctx(engine, jacoby=True, beaver=True))
    off = _eval_cube_decision(cube, _ctx(engine, jacoby=False, beaver=False))
    direct = engine.cube_action(cube["board"], cube_value=1, cube_owner="centered",
                                jacoby=False, beaver=False)
    def nd(res):
        # A cube that is not doubled waits for the checker play it belongs to.
        for entry in [res.pending_cube, *res.entries]:
            if isinstance(entry, dict):
                found = entry.get("equity_no_double", (entry.get("cube") or {}).get("equity_no_double"))
                if found is not None:
                    return found
        raise AssertionError("no cube equity recorded")
    check(nd(on) != nd(off),
          f"a centred cube is judged differently without Jacoby ({nd(on)} vs {nd(off)})")
    check(abs(nd(off) - round(float(direct.equity_nd), 4)) < 1e-4,
          "and without it matches the engine asked directly")

    # Luck fills by the document's rules too.
    stripped = copy.deepcopy(doc)
    for game in stripped["games"]:
        for ply in game["plies"]:
            for a in ply.get("analyses") or ([ply["analysis"]] if ply.get("analysis") else []):
                a.pop("luck", None)
    with_jacoby = copy.deepcopy(stripped)
    with_jacoby["jacoby"] = True
    with_jacoby["beaver"] = True
    fill_luck(stripped, analyzer=engine)
    fill_luck(with_jacoby, analyzer=engine)

    def lucks(d):
        return [a.get("luck") for g in d["games"] for p in g["plies"]
                for a in (p.get("analyses") or ([p["analysis"]] if p.get("analysis") else []))]
    check(lucks(stripped) != lucks(with_jacoby), "luck is measured under the document's rules")

    print(f"\n{_checks - len(_failures)}/{_checks} checks passed.")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
