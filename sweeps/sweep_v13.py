#!/usr/bin/env python3
"""v13 sweep: refine v12c with better stop-to-flange clearance, lower spacing
sensitivity, more center sharpness, or a stronger bowl skirt -- ideally a
better overall compromise.

Key extra metrics computed on top of evaluate.compute_design:
  stop_z_from_film       z-coordinate of stop center (mm from film plane)
  stop_camera_edge       stop slot edge closest to camera (smallest z)
  stop_lens_edge         stop slot edge closest to lens front (largest z)
  flange_gap_camera      stop camera-edge minus flange plane (positive = clear)
  flange_gap_lens        stop lens-edge minus flange plane  (positive = clear)

The flange plane is at THROAT_FAR_MM = 101.7 mm.  Hard rule: any part of the
stop slot within 2.5 mm of the flange is invalid.  Preferred: camera-side
edge >= 3.5 mm clear, lens-side edge >= 5 mm clear of the flange.
"""

import argparse
import math
import sqlite3

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import evaluate as ev


FLANGE_Z = ev.THROAT_FAR_MM     # 101.7
# Flange forbidden band (z from flange): no part of the stop slot may sit in
# [-3.5, +5].  Valid only wholly forward of +5 or wholly behind -3.5.
STOP_FORBID_CAMERA_MM = -3.5
STOP_FORBID_LENS_MM = 5.0


def list_elements():
    con = sqlite3.connect(ev.INVENTORY_DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM elements WHERE purchased=1").fetchall()
    con.close()
    return [dict(r) for r in rows]


def by_type(elements, types):
    return [e for e in elements if e["type"] in types]


def stop_geometry(res, spacings, elements):
    """Compute the stop slot's z-coordinates from the film plane.

    Returns dict: stop_z_center, stop_camera_edge, stop_lens_edge,
    flange_gap_camera, flange_gap_lens.  None if stop_after is None.
    """
    sa = res["stop_after"]
    ss = res["stop_spacing"]
    if sa is None:
        return None
    thick = res["thickness"]
    dist = res["dist_to_focal"]
    n_elem = len(elements)
    half_t_stop = ev.STOP_THICKNESS_MM / 2.0

    if sa == 0:
        # stop sits in front of element 1.  Center is ss + 1 in front of
        # element 1's front face.
        z_center = dist[0] + thick[0] / 2.0 + ss + half_t_stop
    else:
        # stop sits in the gap between element sa and element sa+1.
        # ss = front gap (from rear face of element sa toward film).
        # element sa rear face at z = dist[sa-1] - thick[sa-1]/2
        # stop front face at that minus ss
        # stop center at stop front face minus half_t_stop
        z_center = (dist[sa - 1] - thick[sa - 1] / 2.0
                    - ss - half_t_stop)
    z_camera_edge = z_center - half_t_stop
    z_lens_edge = z_center + half_t_stop
    return {
        "stop_z_center": z_center,
        "stop_camera_edge": z_camera_edge,
        "stop_lens_edge": z_lens_edge,
        "flange_gap_camera": z_camera_edge - FLANGE_Z,
        "flange_gap_lens": z_lens_edge - FLANGE_Z,
    }


def try_design(elements, spacings, stop_after, stop_spacing):
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
    sg = stop_geometry(result, spacings, elements)
    return result, fs, sg


def hard_gate(res, fs, sg):
    """Hard pass/fail.  Returns reason string if fails, None if passes."""
    if not res["bfd_compliant"]:
        return "bfd_compliant"
    if sg is None:
        return "no_stop"
    # Stop slot must clear the flange forbidden band: valid only wholly
    # forward of +5 (outside the flange) or wholly behind -3.5.
    cg, lg = sg["flange_gap_camera"], sg["flange_gap_lens"]
    if not (cg >= STOP_FORBID_LENS_MM or lg <= STOP_FORBID_CAMERA_MM):
        return "stop_in_flange_forbidden_band"
    # Reject obviously bad EFL or speed
    efl = res["efl"]
    if not math.isfinite(efl) or efl <= 70 or efl > 140:
        return "efl_out_of_range"
    fN = res["f_number"]
    if not math.isfinite(fN) or fN < 1.9 or fN > 4.0:
        return "speed_out_of_range"
    # P>0 (petzval inward, needed for any swirl)
    P = fs["P"]
    if P <= 0:
        return "petzval_outward"
    return None


def score(res, fs, sg):
    """Composite priority score: higher is better."""
    P = fs["P"]
    A = fs["A"]
    efl = res["efl"]
    fN = res["f_number"]
    # DEPRECATED: swirl_pct (Dallmeyer-ratio of Petzval radius) is the old
    # conflated swirl metric, superseded by the swirl decomposition
    # (field-curvature depth + signed astig direction + image-circle + coma).
    # Still read here only because the DB column persists; do not treat as the
    # swirl target. To be revisited when the sweep moves to the new metrics.
    swirl = res["swirl_pct"]
    sens = res.get("max_bc_sensitivity") or 0.0
    sharp_pct = res.get("sharp_radius_pct") or 0.0
    ap_ratio = A / P if abs(P) > 1e-12 else 0.0
    bowl = max(0.0, -ap_ratio)  # negative ratio = bowl

    # EFL closeness to 100 (preferred 100-110, acceptable 110-135).
    if 95 <= efl <= 110:
        efl_score = 1.0
    elif 110 < efl <= 125:
        efl_score = 0.7
    elif 88 <= efl < 95:
        efl_score = 0.8
    elif 80 <= efl < 88:
        efl_score = 0.5
    else:
        efl_score = 0.2

    # Swirl: prio-1.  Optimal 125-150, preferred > 100, acceptable >= 100.
    if 125 <= swirl <= 150:
        swirl_score = 1.0
    elif 100 < swirl < 125:
        swirl_score = 0.7 + 0.3 * (swirl - 100) / 25.0
    elif 100 <= swirl:
        swirl_score = 0.7
    elif 85 <= swirl < 100:
        swirl_score = 0.4 + 0.3 * (swirl - 85) / 15.0
    else:
        swirl_score = 0.1

    # Speed: optimal 2.4, preferred 2.0-3.2, acceptable 2.0-4.0.
    if 2.2 <= fN <= 3.2:
        speed_score = 1.0
    elif 2.0 <= fN <= 4.0:
        speed_score = 0.6 + 0.4 * max(0, 1 - abs(fN - 2.6) / 1.4)
    else:
        speed_score = 0.2

    # Bowl signature: prio 3, want A/P negative, |A/P| > 0.05 preferred.
    if bowl >= 0.05:
        bowl_score = 1.0
    elif bowl > 0.0:
        bowl_score = 0.5 + bowl / 0.05 * 0.5
    else:
        bowl_score = 0.3

    # Sharpness: prio 3, want sharp_radius >= 20% (acceptable) ideally 33-50%.
    if sharp_pct >= 30.0:
        sharp_score = 1.0
    elif sharp_pct >= 15.0:
        sharp_score = 0.7 + 0.3 * (sharp_pct - 15) / 15.0
    elif sharp_pct > 0.0:
        sharp_score = 0.4 + 0.3 * sharp_pct / 15.0
    else:
        sharp_score = 0.3

    # Spacing tolerance: low-priority comfort, lower sensitivity is better.
    if sens < 0.3:
        tol_score = 1.0
    elif sens < 0.6:
        tol_score = 0.8
    elif sens < 1.0:
        tol_score = 0.6
    elif sens < 1.5:
        tol_score = 0.4
    else:
        tol_score = 0.2

    # Stop-to-flange: low-priority.  Prefer the slot wholly outside the
    # flange (camera edge forward of +5) over the deep-in-body placement.
    cg = sg["flange_gap_camera"]
    flange_score = 1.0 if cg >= STOP_FORBID_LENS_MM else 0.5

    total = (
        swirl_score
        * efl_score
        * speed_score
        * (0.7 + 0.3 * bowl_score)
        * (0.7 + 0.3 * sharp_score)
        * (0.7 + 0.3 * tol_score)
        * (0.9 + 0.1 * flange_score)
    )

    return {
        "bowl": bowl,
        "ap_ratio": ap_ratio,
        "efl_s": efl_score,
        "swirl_s": swirl_score,
        "speed_s": speed_score,
        "bowl_s": bowl_score,
        "sharp_s": sharp_score,
        "tol_s": tol_score,
        "flange_s": flange_score,
        "total": total,
    }


def sweep_3(inventory, spacings_grid, stop_grid, archs):
    negs = by_type(inventory, ("NMN", "DCV", "BCV", "PCV"))
    poss_singlet = by_type(inventory, ("DCX", "BCX", "PCX", "PMN"))
    achs = by_type(inventory, ("ACH",))

    arch_specs = {
        "N-A-P": [negs, achs, poss_singlet],
        "N-P-A": [negs, poss_singlet, achs],
        "P-N-A": [poss_singlet, negs, achs],
        "A-N-P": [achs, negs, poss_singlet],
        "P-A-N": [poss_singlet, achs, negs],
        "A-P-N": [achs, poss_singlet, negs],
        "P-N-P": [poss_singlet, negs, poss_singlet],
        "N-P-P": [negs, poss_singlet, poss_singlet],
    }

    results = []
    for arch_name in archs:
        slots = arch_specs[arch_name]
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
                                # Filter out positions that are obviously
                                # too small for this spacing
                                if stop_after == 1 and stop_spacing >= d1 - 4:
                                    continue
                                if stop_after == 2 and stop_spacing >= d2 - 4:
                                    continue
                                out = try_design(elems, [d1, d2],
                                                 stop_after, stop_spacing)
                                if out is None:
                                    continue
                                res, fs, sg = out
                                gate = hard_gate(res, fs, sg)
                                if gate is not None:
                                    continue
                                sc = score(res, fs, sg)
                                results.append({
                                    "arch": arch_name,
                                    "elements": [e1["code"], e2["code"],
                                                 e3["code"]],
                                    "spacings": [d1, d2],
                                    "stop_after": stop_after,
                                    "stop_spacing": stop_spacing,
                                    "result": res,
                                    "fs": fs,
                                    "sg": sg,
                                    "score": sc,
                                })
    return results


def fmt_row(r):
    res = r["result"]
    sg = r["sg"]
    sc = r["score"]
    return (
        f"{r['arch']:<7} "
        f"{'+'.join(r['elements']):<32} "
        f"sp={r['spacings']} "
        f"st={r['stop_after']}/{r['stop_spacing']:.1f} "
        f"EFL={res['efl']:.1f} f/{res['f_number']:.2f} "
        f"swirl={res['swirl_pct']:.1f}% "
        f"A/P={sc['ap_ratio']:+.3f} "
        f"sharp={res['sharp_radius_pct']:.1f}% "
        f"sens={res['max_bc_sensitivity']:.2f} "
        f"flg_cam={sg['flange_gap_camera']:+.1f} "
        f"flg_lens={sg['flange_gap_lens']:+.1f} "
        f"sc={sc['total']:.3f}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--archs", nargs="+",
                    default=["N-A-P", "N-P-A", "P-N-A", "A-N-P", "A-P-N",
                             "P-A-N", "P-N-P", "N-P-P"])
    ap.add_argument("--quick", action="store_true",
                    help="Coarser grid")
    ap.add_argument("--bowl-only", action="store_true",
                    help="Filter to negative A (bowl signature) only")
    ap.add_argument("--sortby", default="total",
                    choices=["total", "sharp", "sens", "swirl", "flange"])
    args = ap.parse_args()

    inventory = list_elements()

    if args.quick:
        spacings_grid = [10.0, 14.0, 18.0, 22.0, 26.0]
        stop_grid = [(0, 5.0),
                     (1, 3.0), (1, 6.0), (1, 10.0), (1, 14.0),
                     (2, 2.0), (2, 4.0), (2, 6.0), (2, 10.0), (2, 14.0)]
    else:
        spacings_grid = [8.0, 12.0, 16.0, 20.0, 24.0, 28.0]
        stop_grid = [(0, 4.0), (0, 8.0),
                     (1, 2.0), (1, 4.0), (1, 6.0), (1, 8.0), (1, 12.0),
                     (1, 16.0), (1, 20.0),
                     (2, 2.0), (2, 4.0), (2, 6.0), (2, 8.0), (2, 12.0),
                     (2, 16.0), (2, 20.0)]

    results = sweep_3(inventory, spacings_grid, stop_grid, args.archs)

    if args.bowl_only:
        results = [r for r in results if r["fs"]["A"] < 0]

    if args.sortby == "sharp":
        results.sort(key=lambda r: r["result"]["sharp_radius_pct"], reverse=True)
    elif args.sortby == "sens":
        results.sort(key=lambda r: r["result"]["max_bc_sensitivity"])
    elif args.sortby == "swirl":
        results.sort(key=lambda r: r["result"]["swirl_pct"], reverse=True)
    elif args.sortby == "flange":
        results.sort(key=lambda r: r["sg"]["flange_gap_camera"], reverse=True)
    else:
        results.sort(key=lambda r: r["score"]["total"], reverse=True)

    print(f"# Found {len(results)} compliant designs "
          f"(stop slot clear of the flange band "
          f"[{STOP_FORBID_CAMERA_MM:+.1f},{STOP_FORBID_LENS_MM:+.1f}]mm)")
    print()
    for r in results[:args.top]:
        print(fmt_row(r))


if __name__ == "__main__":
    main()
