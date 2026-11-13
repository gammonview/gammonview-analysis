# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""Pin an OGXM 2.3 file another producer wrote, for both readers.

2.3 (HedgeHog's spec, 2.1 and 6.6) adds what a 2.2 reader either refuses or
reads wrong: a ``RESIGN`` priced on a ``forfeit`` (which raises
``min_reader_minor`` to 3), the table a match block's MWCs were computed on
(``ANAL.met``), and the clock's rule and extra-time grants. OpenGammon writes
all three since October 2026.

``v23-foreign.ogxm`` is written here by the *reference* codec (it needs
``libogxm``, so it is generated and committed): one of our goldens with our
annotations stripped, so it reads as another producer's, under a stored table
that is deliberately not ours, its last game ending in a priced forfeit, and a
clock with a rule and a grant. ``v23-foreign.anchors.json`` holds what the
reference itself reads off that table: each analysed ply's ``emg_anchors``
(``[lose, win]``) and the MWC of its first alternative or its no-double value.
A reader that converts on the stored table reproduces them exactly; one that
converts on its own does not.

Rerun after a change to the source golden or to the reference codec, and
commit the result with it:
    uv run python tests/gen_ogxm23_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "tests"))

import ogxm2_oracle as oracle  # noqa: E402
from gvformat.met import stored_met  # noqa: E402

SOURCE = _REPO_ROOT / "tests" / "golden" / "5nqfGw9bWG3deTaU.fast.gvab"
FIXTURES = _REPO_ROOT / "gvformat-js" / "test" / "fixtures" / "ogxm2"

# A table that is not ours: every leader's chance a little higher. Any valid
# table would do; what matters is that converting on ours gives other numbers.
SHIFT = 0.004


def foreign_table(K: int) -> dict:
    ours = stored_met(K, True)
    return {key: [round(min(1.0, v + SHIFT), 6) for v in vals] for key, vals in ours.items()}


def build() -> tuple[bytes, dict]:
    doc = oracle.binary_to_json(SOURCE.read_bytes())
    doc["annotations"] = [a for a in doc.get("annotations", [])
                          if not a["key"].startswith("x-gammonview-")]
    info = doc["analysis_info"]
    info["met"] = foreign_table(doc["match_length"])
    info["met_id"] = "test-shifted"
    info["producer"] = "external"
    info.pop("model_id", None)
    info.pop("model_name", None)

    # The last game ends in a forfeit, priced as a resignation would be.
    last = doc["games"][-1]["plies"][-1]
    assert last["action_id"] == 28, last
    last["action_id"] = 29
    last.pop("resign_value", None)
    last["analysis"] = {"resign_error": 0.031, "equity_loss": 0.031}

    doc["clock_info"] = {"reserve_ms": 180000, "delay_ms": 10000, "increment_ms": 0,
                         "start_timestamp": 1768314600, "rule": "opengammon",
                         "grants": [1, 0]}
    problem = oracle.check_json(doc)
    if problem != "ok":
        raise SystemExit(f"the reference refuses the document: {problem}")
    return oracle.json_to_binary(doc), doc


def anchors(data: bytes) -> list:
    back = oracle.binary_to_json(data)
    out = []
    for gi, g in enumerate(back["games"]):
        for pi, p in enumerate(g["plies"]):
            a = p.get("analysis")
            if not a:
                continue
            entry = {"gi": gi, "pi": pi, "action_id": p["action_id"]}
            if "emg_anchors" in a:
                entry["anchors"] = a["emg_anchors"]
            elif "cube_decision" in a and "emg_anchors" in a["cube_decision"]:
                entry["anchors"] = a["cube_decision"]["emg_anchors"]
            else:
                continue
            if a.get("alternatives"):
                entry["mwc"] = a["alternatives"][0]["equity"]
            elif "no_double_equity" in a:
                entry["mwc"] = a["no_double_equity"]
            out.append(entry)
    return out


def main() -> int:
    data, _ = build()
    head = data[:16]
    minor = int.from_bytes(head[6:8], "little")
    min_reader = int.from_bytes(head[10:12], "little")
    if (minor, min_reader) != (3, 3):
        raise SystemExit(f"expected a 2.3 file a 2.2 reader refuses, got {minor}/{min_reader}")
    (FIXTURES / "v23-foreign.ogxm").write_bytes(data)
    pinned = {"reference": oracle.version(), "table": foreign_table(7), "plies": anchors(data)}
    (FIXTURES / "v23-foreign.anchors.json").write_text(json.dumps(pinned, indent=1) + "\n")
    print(f"wrote v23-foreign.ogxm ({len(data)} bytes) and {len(pinned['plies'])} anchors "
          f"(reference {oracle.version()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
