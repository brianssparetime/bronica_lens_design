#!/usr/bin/env python3
"""Bowl-shape sweep for v12 generation.

Iterates over 3-element architectures (and selected 4-element ones) using the
current inventory, sweeping stop position to find designs with the
opposing-Petzval-and-astigmatism signature described under
"Controlling field curvature" in https://brianssparetime.com/posts/zenzanot-4-swirl/.  Ranks candidates by:
  - BFD compliance (hard gate)
  - Astigmatism sign and magnitude (bowl signature: A<0, P>0, |A/P| > 0.05)
  - EFL near 100mm
  - Swirl > 100%
  - Speed in the f/2-f/4 band
  - Spacing tolerance
"""

import argparse
import math
import sqlite3

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import evaluate as ev


def list_elements():
    con = sqlite3.connect(ev.INVENTORY_DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM elements WHERE purchased=1").fetchall()
    con.close()
    return [dict(r) for r in rows]


def by_type(elements, types):
    return [e for e in elements if e["type"] in types]


def score(result, field_shape):
    """Higher is better.  Returns None if hard gate fails."""
    if not result["bfd_compliant"]:
        return None
    P = field_shape["P"]
    A = field_shape["A"]

    # Bowl signature: P > 0, A < 0, with meaningful magnitude
    if P <= 0:
        return None
    ap_ratio = A / P  # negative ratio is what we want
    bowl = max(0.0, -ap_ratio)  # positive when A<0

    # EFL closeness to 100mm (gaussian-ish peak)
    efl = result["efl"]
    if not math.isfinite(efl) or efl <= 0:
        return None
    efl_score = math.exp(-((efl - 100.0) ** 2) / (2 * 25.0 ** 2))  # sigma=25mm

    # Swirl: want >100, optimal 125-150
    swirl = result["swirl_pct"]
    if swirl >= 125 and swirl <= 150:
        swirl_score = 1.0
    elif swirl > 100:
        swirl_score = 0.5 + 0.5 * (swirl - 100) / 25.0
    elif swirl > 80:
        swirl_score = 0.3 * (swirl - 80) / 20.0
    else:
        swirl_score = 0.0

    # Speed: f/2.8 optimal, f/2-f/3.2 preferred, f/2-f/4 acceptable
    f_n = result["f_number"]
    if not math.isfinite(f_n):
        speed_score = 0.0
    elif 2.0 <= f_n <= 3.2:
        speed_score = 1.0
    elif 2.0 <= f_n <= 4.0:
        speed_score = 0.6
    else:
        speed_score = 0.0

    # Tolerance bonus: max sensitivity should be modest
    sens = result.get("max_bc_sensitivity", 0.0) or 0.0
    if sens < 0.5:
        tol_score = 1.0
    elif sens < 1.0:
        tol_score = 0.7
    elif sens < 2.0:
        tol_score = 0.4
    else:
        tol_score = 0.1

    # Combined: bowl is the headline; everything else is a multiplier
    return {
        "bowl": bowl,
        "efl": efl_score,
        "swirl": swirl_score,
        "speed": speed_score,
        "tol": tol_score,
        "total": (1.0 + 4.0 * bowl) * efl_score * (0.5 + 0.5 * swirl_score)
                 * (0.3 + 0.7 * speed_score) * (0.5 + 0.5 * tol_score),
    }


def try_design(elements, spacings, stop_after, stop_spacing):
    """Run evaluate, return (result, field_shape) or None on failure."""
    try:
        result = ev.compute_design(elements, spacings, stop_after, stop_spacing)
    except SystemExit:
        return None
    except Exception:
        return None
    fl = [e["focal_length"] for e in elements]
    fs = ev.compute_field_shape(
        fl,
        result.get("stop_chief_heights"),
        result["petzval_radius"],
        ev.FRAME_HALF_DIAG_MM,
    )
    return result, fs


def sweep_3(inventory, spacings_grid, stop_grid, only_arch=None):
    """3-element architecture sweep."""
    negs = by_type(inventory, ("NMN", "DCV", "BCV", "PCV"))
    poss = by_type(inventory, ("DCX", "BCX", "PCX", "PMN"))
    achs = by_type(inventory, ("ACH",))

    arch_specs = {
        "N-A-P": [negs, achs, poss],
        "N-P-A": [negs, poss, achs],
        "N-P-P": [negs, poss, poss],
        "P-N-P": [poss, negs, poss],
        "A-N-P": [achs, negs, poss],
        "P-N-A": [poss, negs, achs],
    }

    results = []
    for arch_name, slots in arch_specs.items():
        if only_arch and arch_name not in only_arch:
            continue
        for e1 in slots[0]:
            for e2 in slots[1]:
                if e1["code"] == e2["code"]:
                    continue
                for e3 in slots[2]:
                    if e3["code"] in (e1["code"], e2["code"]):
                        continue
                    elems = [e1, e2, e3]
                    for d1 in spacings_grid:
                        for d2 in spacings_grid:
                            for stop_pos in stop_grid:
                                stop_after, stop_spacing = stop_pos
                                # Validate stop position fits
                                if stop_after == 1 and stop_spacing >= d1 - 2:
                                    continue
                                if stop_after == 2 and stop_spacing >= d2 - 2:
                                    continue
                                out = try_design(elems, [d1, d2], stop_after, stop_spacing)
                                if out is None:
                                    continue
                                res, fs = out
                                sc = score(res, fs)
                                if sc is None:
                                    continue
                                results.append({
                                    "arch": arch_name,
                                    "elements": [e1["code"], e2["code"], e3["code"]],
                                    "spacings": [d1, d2],
                                    "stop_after": stop_after,
                                    "stop_spacing": stop_spacing,
                                    "result": res,
                                    "fs": fs,
                                    "score": sc,
                                })
    return results


def sweep_4(inventory, spacings_grid, stop_grid, only_arch=None, max_results=20000):
    """4-element architecture sweep (a smaller, more constrained set)."""
    negs = by_type(inventory, ("NMN", "DCV", "BCV", "PCV"))
    poss = by_type(inventory, ("DCX", "BCX", "PCX", "PMN"))
    achs = by_type(inventory, ("ACH",))

    arch_specs = {
        "N-A-P-P": [negs, achs, poss, poss],
        "N-P-A-P": [negs, poss, achs, poss],
        "N-P-P-A": [negs, poss, poss, achs],
        "P-N-A-P": [poss, negs, achs, poss],
        "P-N-P-A": [poss, negs, poss, achs],
        "P-A-N-P": [poss, achs, negs, poss],
    }

    results = []
    count = 0
    for arch_name, slots in arch_specs.items():
        if only_arch and arch_name not in only_arch:
            continue
        for e1 in slots[0]:
            for e2 in slots[1]:
                if e1["code"] == e2["code"]:
                    continue
                for e3 in slots[2]:
                    if e3["code"] in (e1["code"], e2["code"]):
                        continue
                    for e4 in slots[3]:
                        if e4["code"] in (e1["code"], e2["code"], e3["code"]):
                            continue
                        elems = [e1, e2, e3, e4]
                        for d1 in spacings_grid:
                            for d2 in spacings_grid:
                                for d3 in spacings_grid:
                                    for stop_pos in stop_grid:
                                        stop_after, stop_spacing = stop_pos
                                        if stop_after == 1 and stop_spacing >= d1 - 2:
                                            continue
                                        if stop_after == 2 and stop_spacing >= d2 - 2:
                                            continue
                                        if stop_after == 3 and stop_spacing >= d3 - 2:
                                            continue
                                        if stop_after >= 4:
                                            continue
                                        out = try_design(elems, [d1, d2, d3],
                                                         stop_after, stop_spacing)
                                        if out is None:
                                            continue
                                        res, fs = out
                                        sc = score(res, fs)
                                        if sc is None:
                                            continue
                                        results.append({
                                            "arch": arch_name,
                                            "elements": [e["code"] for e in elems],
                                            "spacings": [d1, d2, d3],
                                            "stop_after": stop_after,
                                            "stop_spacing": stop_spacing,
                                            "result": res,
                                            "fs": fs,
                                            "score": sc,
                                        })
                                        count += 1
                                        if count >= max_results:
                                            return results
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--arch", nargs="+", default=None,
                    help="Restrict architectures (e.g. N-A-P P-N-P)")
    ap.add_argument("--bowl-only", action="store_true",
                    help="Only show designs with negative A (bowl signature)")
    ap.add_argument("--quick", action="store_true",
                    help="Coarse sweep grid for fast first pass")
    ap.add_argument("--four", action="store_true",
                    help="Sweep 4-element architectures instead of 3-element")
    args = ap.parse_args()

    inventory = list_elements()

    if args.quick:
        spacings_grid = [8.0, 12.0]
        stop_grid = [(1, 3.0), (1, 6.0), (2, 3.0), (2, 6.0)]
    else:
        spacings_grid = [6.0, 8.0, 10.0, 12.0, 15.0]
        stop_grid = [(0, 5.0),
                     (1, 2.0), (1, 4.0), (1, 6.0), (1, 8.0),
                     (2, 2.0), (2, 4.0), (2, 6.0), (2, 8.0),
                     (3, 2.0), (3, 4.0), (3, 6.0)]

    if args.four:
        # 4-element grid is reduced for combinatorial reasons
        spacings_grid = [8.0, 12.0]
        stop_grid = [(1, 4.0), (2, 4.0), (3, 4.0)]
        results = sweep_4(inventory, spacings_grid, stop_grid, args.arch)
    else:
        results = sweep_3(inventory, spacings_grid, stop_grid, args.arch)

    if args.bowl_only:
        results = [r for r in results if r["fs"]["A"] < 0]

    results.sort(key=lambda r: r["score"]["total"], reverse=True)

    print(f"Found {len(results)} compliant designs")
    print()
    print(f"{'arch':<7} {'elements':<35} {'EFL':>6} {'fN':>6} {'swirl':>6} "
          f"{'A/P':>7} {'sharp%':>7} {'sens':>6} {'score':>7}")
    print("-" * 105)
    for r in results[:args.top]:
        res = r["result"]
        fs = r["fs"]
        sc = r["score"]
        ap_ratio = fs["A"] / fs["P"] if abs(fs["P"]) > 1e-12 else 0.0
        elem_str = "+".join(r["elements"])
        print(f"{r['arch']:<7} {elem_str:<35} {res['efl']:>6.1f} "
              f"{res['f_number']:>6.2f} {res['swirl_pct']:>6.1f} "
              f"{ap_ratio:>+7.3f} {res['sharp_radius_pct']:>6.1f}% "
              f"{res['max_bc_sensitivity']:>6.2f} "
              f"{sc['total']:>7.3f}")
        print(f"          spacings={r['spacings']}  "
              f"stop_after={r['stop_after']} stop_spacing={r['stop_spacing']:.1f}  "
              f"back_clr={res['back_clearance']:.1f} "
              f"rear_z={res['throat_margin']:+.1f}")


if __name__ == "__main__":
    main()
