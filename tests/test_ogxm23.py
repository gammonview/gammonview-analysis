# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""OGXM 2.3: another producer's file, written by the reference codec
(``tests/gen_ogxm23_fixtures.py``), with a priced forfeit, a stored match
equity table that is not ours, and a clock rule. A 2.2 reader refuses it;
this one reads it, converts its MWCs on the table it stores -- the anchors
are the reference's own -- and writes the table back with the numbers.

Mirrors gvformat-js/test/test-ogxm23.js -- keep the two in step -- and reads
the same fixtures. The reference-library section (needs ``libogxm``; skipped
without it) is Python's own: it checks what the JS cannot, that the bytes our
writer makes are a file the reference accepts and reads the same numbers from.

Run directly:
    uv run python tests/test_ogxm23.py
"""

from __future__ import annotations

import copy
import json
import struct
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "tests"))

from gvformat.basefill import frame_key
from gvformat.binary import write_gvab
from gvformat.met import mwc_anchors, stored_met
from gvformat.reader import read_gvab

FIXTURES = _REPO_ROOT / "gvformat-js" / "test" / "fixtures" / "ogxm2"

_checks = 0
_skips = 0
_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    global _checks
    _checks += 1
    print(f"{'OK  ' if cond else 'FAIL'}  {label}")
    if not cond:
        _failures.append(label)


def header(b: bytes) -> list[int]:
    return [struct.unpack_from("<H", b, 6)[0], struct.unpack_from("<H", b, 10)[0]]


def emg(v: float, anchors) -> float:
    lose, win = anchors
    return (v - lose) / (win - lose) * 2 - 1


def r4(x: float) -> float:
    return round(x * 1e4) / 1e4


def doc_value(a: dict):
    """The document's value for a pinned entry: the first alternative's equity, or
    the no-double value of a cube on its own ply or a dice ply's live cube."""
    if a.get("alternatives"):
        return a["alternatives"][0]["equity"]
    if a.get("no_double_equity") is not None:
        return a["no_double_equity"]
    cd = a.get("cube_decision")
    if cd and cd.get("no_double_equity") is not None:
        return cd["no_double_equity"]
    return None


def main() -> int:
    global _skips
    data = (FIXTURES / "v23-foreign.ogxm").read_bytes()
    pinned = json.loads((FIXTURES / "v23-foreign.anchors.json").read_text())

    check(header(data) == [3, 3], "the fixture is a 2.3 file a 2.2 reader refuses")

    doc = read_gvab(data)
    info = doc["analysis_info"]
    check(info.get("met") == pinned["table"], "the block keeps the table it stores")
    check(info.get("met_id") == "test-shifted", "and its name")
    check(doc["clock_info"].get("rule") == "opengammon" and doc["clock_info"].get("grants") == [1, 0],
          "the clock keeps its rule and grants")

    frames = values = apart = checked = 0
    bad: list[str] = []
    for e in pinned["plies"]:
        p = doc["games"][e["gi"]]["plies"][e["pi"]]
        if p["action_id"] != e["action_id"]:
            bad.append(f"{e['gi']},{e['pi']} is action {p['action_id']}")
            continue
        a = p.get("analysis")
        if not a:
            continue                                    # the forfeit's RESIGN is not one we keep
        checked += 1
        lose, win = e["anchors"]
        want = [round((win + lose) / 2, 8), round((win - lose) / 2, 8)]
        mf = a.get("mwc_frame")
        if mf and abs(mf[0] - want[0]) < 1e-8 and abs(mf[1] - want[1]) < 1e-8:
            frames += 1
        else:
            bad.append(f"{e['gi']},{e['pi']} frame {mf} vs {want}")
        if e.get("mwc") is None:
            continue
        got = doc_value(a)
        w = r4(emg(e["mwc"], e["anchors"]))
        if got is not None and abs(got - w) <= 1.5e-4:
            values += 1
        else:
            bad.append(f"{e['gi']},{e['pi']} value {got} vs {w}")
        key = frame_key(p)
        ours = mwc_anchors(*key)
        if abs(emg(e["mwc"], e["anchors"]) - emg(e["mwc"], [ours[1], ours[0]])) > 1e-3:
            apart += 1
    check(not bad, "every analysed ply lines up with the reference's"
          + (": " + "; ".join(bad[:5]) if bad else ""))
    check(checked > 200 and frames == checked, f"each ply's frame is the stored table's ({frames}/{checked})")
    check(values > 200, f"each value is converted on it ({values})")
    check(apart > 50, f"and on our table {apart} of them would not have been")

    # Rewritten: verbatim while untouched, and through the stated frames when not.
    again = read_gvab(write_gvab(doc))
    check(again["analysis_info"].get("met") == pinned["table"], "an untouched rewrite keeps the table")

    edited = copy.deepcopy(doc)
    edited.pop("_ogxm2_passthrough", None)              # nothing goes back verbatim
    out = write_gvab(edited)
    check(header(out)[0] == 3, "our own encoding of it is a 2.3 file")
    back = read_gvab(out)
    check(back["analysis_info"].get("met") == pinned["table"],
          "and states the table its MWCs are quoted on")
    same = differ = 0
    for gi, g in enumerate(doc["games"]):
        for pi, p in enumerate(g["plies"]):
            a = p.get("analysis")
            if not a or doc_value(a) is None:
                continue
            b = back["games"][gi]["plies"][pi].get("analysis")
            if b and doc_value(b) == doc_value(a):
                same += 1
            else:
                differ += 1
    check(differ == 0 and same > 200, f"which read back to the same equities ({same} same, {differ} not)")
    check(back["clock_info"].get("rule") == "opengammon" and back["clock_info"].get("grants") == [1, 0],
          "and the same clock")

    # Our own blocks: the table we write is ours, so the document does not carry it.
    ours_doc = copy.deepcopy(doc)
    ours_doc.pop("_ogxm2_passthrough", None)
    ours_doc["analysis_info"].pop("met", None)
    ours_doc["analysis_info"].pop("met_id", None)
    for g in ours_doc["games"]:
        for p in g["plies"]:
            if p.get("analysis"):
                p["analysis"].pop("mwc_frame", None)
    ours_out = write_gvab(ours_doc)
    ours_back = read_gvab(ours_out)
    check(header(ours_out) == [3, 0], "a block on our table is written at minor 3, readable by 2.2")
    check("met" not in ours_back["analysis_info"], "and reads back without a table in the document")
    check(stored_met(7) != pinned["table"], "(the fixture's table is not ours)")

    # The table helpers themselves.
    t = stored_met(3)
    check(t == {"pre_crawford": [0.67736, 0.75076, 0.59947], "post_crawford": [0.51197, 0.67736]},
          "ours cut at 3 is P(1,2), P(1,3), P(2,3) and the leader's post-Crawford row")
    check(stored_met(1) is None and stored_met(26) is None, "nothing below 2 away, nor past our table")
    check("post_crawford" not in stored_met(5, False), "no post-Crawford row without the rule")

    # Against the reference: what our writer makes, the reference reads the same.
    try:
        import ogxm2_oracle as oracle
        version = oracle.version()
    except (ImportError, SystemExit, RuntimeError) as exc:     # OracleUnavailable is a RuntimeError
        print(f"SKIP  reference library unavailable ({exc}) -- reference section skipped")
        _skips += 1
        version = None
    if version is not None:
        print(f"      reference libogxm {version}")
        ref = oracle.binary_to_json(out)
        check(oracle.check_json(ref) == "ok", "the reference accepts what we wrote of the foreign block")
        check(ref["analysis_info"].get("met") == pinned["table"],
              "and reads the stored table we wrote back as the pinned one")
        # Our writer quantizes each equity to a ten-thousandth of the normalized
        # scale, so an MWC comes back within half a step of that scale (half the
        # frame's span times 5e-5) plus the six places it is stored at. A
        # checker decision our writer carries in an annotation instead of DECS
        # (the reference then sees only the ply's live cube) is not compared.
        n = off = 0
        worst = 0.0
        slack = 0.0
        for e in pinned["plies"]:
            if e.get("mwc") is None:
                continue
            a = ref["games"][e["gi"]]["plies"][e["pi"]].get("analysis")
            if not a:
                continue
            if a.get("alternatives"):
                got = a["alternatives"][0]["equity"]
            elif a.get("no_double_equity") is not None:
                got = a["no_double_equity"]
            else:
                off += 1
                continue
            lose, win = e["anchors"]
            n += 1
            err = abs(got - e["mwc"])
            bound = (win - lose) / 2 * 5e-5 + 2e-6
            if err > bound:
                slack = max(slack, err - bound)
            worst = max(worst, err)
        check(n > 200 and slack == 0.0,
              f"the MWCs it reads from our file are the pinned ones to the stored precision "
              f"({n} checked, worst {worst:.2e}, {off} decisions held in annotations not compared)")

        oref = oracle.binary_to_json(ours_out)
        check(oracle.check_json(oref) == "ok", "the reference accepts a block written on our own table")
        found = None
        for gi, g in enumerate(oref["games"]):
            for pi, p in enumerate(g["plies"]):
                a = p.get("analysis")
                if a and "emg_anchors" in a:
                    found = (gi, pi, a["emg_anchors"])
                    break
            if found:
                break
        ok = False
        if found:
            gi, pi, got = found
            win, lose = mwc_anchors(*frame_key(ours_doc["games"][gi]["plies"][pi]))
            ok = abs(got[0] - lose) < 1e-6 and abs(got[1] - win) < 1e-6
        check(ok, "and its anchors on the first analysed ply are our table's")

    print()
    print(f"{_checks - len(_failures)}/{_checks} checks passed" + (f", {_skips} skipped" if _skips else "") + ".")
    if _failures:
        print("FAILURES:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
