# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""A double that was made, and a take, always count toward PR.

XG's definition of a decision: "a double that results in a double, and a take,
are never obvious." The triviality rules (``_trivial_cube``,
``_trivial_take_pass``) decide only whether a *no-double* or a *pass* counts.
Until this was fixed, a correct double in an optional-double position (its
error under 0.001) went uncounted: 107 of 1,989 doubles and 3 of 1,170 takes
across a 522-file XG corpus. One of the sample matches holds such a double.

Run: uv run python tests/test_cube_action_counting.py   (exit 0 = all passed)
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from gvformat import convert_xg
from gvformat.xg import _trivial_cube

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


def main() -> int:
    doubles = takes = optional = 0
    uncounted: list[str] = []
    for f in sorted((_ROOT / "samples" / "xg").glob("*.xg")):
        for g in convert_xg(f)["games"]:
            for p in g["plies"]:
                a = p.get("analysis") or {}
                if a.get("double_take_equity") is None:
                    continue
                if p.get("action_id") == 21:
                    doubles += 1
                    if _trivial_cube(a["no_double_equity"], a["double_take_equity"],
                                     a["double_pass_equity"]) and a["equity_loss"] < 0.001:
                        optional += 1
                elif p.get("action_id") == 22:
                    takes += 1
                else:
                    continue
                if a.get("decision") is not True:
                    uncounted.append(f"{f.name} game {g.get('game_number')}")
    check(doubles > 0 and takes > 0, f"samples hold doubles ({doubles}) and takes ({takes})")
    check(optional > 0, f"including a correct double the triviality rule calls obvious ({optional})")
    check(not uncounted, f"every double and take counts ({uncounted or 'none uncounted'})")

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
