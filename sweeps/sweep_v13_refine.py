#!/usr/bin/env python3
"""Fine sweep around the best v13 candidates, with stop-to-flange clearance
enforced as a hard gate.  Reports per-design why it wins.
"""

import argparse
import math
import sqlite3

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import evaluate as ev


FLANGE_Z = ev.THROAT_FAR_MM
HARD_STOP_FLANGE_MM = 2.5


def list_elements():
    con = sqlite3.connect(ev.INVENTORY_DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM elements WHERE purchased=1").fetchall()
    con.close()
    return [dict(r) for r in rows]


def stop_geometry(res):
    sa = res["stop_after"]
    ss = res["stop_spacing"]
    if sa is None:
        return None
    thick = res["thickness"]
    dist = res["dist_to_focal"]
    if sa == 0:
        z_center = dist[0] + thick[0] / 2.0 + ss + 1.0
    else:
        z_center = dist[sa - 1] - thick[sa - 1] / 2.0 - ss - 1.0
    return {
        "stop_z_center": z_center,
        "stop_camera_edge": z_center - 1.0,
        "stop_lens_edge": z_center + 1.0,
        "flange_gap_camera": (z_center - 1.0) - FLANGE_Z,
        "flange_gap_lens": (z_center + 1.0) - FLANGE_Z,
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
    sg = stop_geometry(result)
    return result, fs, sg


def hard_gate(res, fs, sg, min_throat_margin=0.0, min_rear_gap=2.0):
    if not res["bfd_compliant"]:
        return "bfd_compliant"
    if sg is None:
        return "no_stop"
    if sg["flange_gap_camera"] < HARD_STOP_FLANGE_MM:
        return "stop_too_close_to_flange_camera"
    if sg["flange_gap_lens"] < HARD_STOP_FLANGE_MM:
        return "stop_too_close_to_flange_lens"
    # throat margin: prefer at least min_throat_margin (positive)
    if res["throat_margin"] < min_throat_margin:
        return f"throat_margin<{min_throat_margin}"
    # rear gap between stop and post-stop element
    sa = res["stop_after"]
    if sa is not None and sa > 0 and sa < len(res["dist_to_focal"]):
        gap_info = res["element_gaps"][sa - 1]
        if gap_info.get("stop_in_gap"):
            if gap_info["gap_rear_mm"] < min_rear_gap:
                return "rear_gap_too_tight"
            if gap_info["gap_front_mm"] < min_rear_gap:
                return "front_gap_too_tight"
    efl = res["efl"]
    if not math.isfinite(efl) or efl < 85 or efl > 135:
        return "efl_out_of_range"
    fN = res["f_number"]
    if not math.isfinite(fN) or fN < 1.9 or fN > 4.1:
        return "speed_out_of_range"
    if fs["P"] <= 0:
        return "petzval_outward"
    return None


def best_stop_for(elements, spacings, min_throat=0.0, min_rear_gap=2.0,
                  stop_step=0.5, want_bowl=True, min_efl=85, max_efl=135):
    """For a given element set and spacings, scan every legal stop position
    (front-of-1, in d1, in d2, ...) at 0.5mm resolution and return the best
    one according to a multi-objective composite."""
    best = None
    # Try a few stop_after positions.
    n_el = len(elements)
    for stop_after in range(0, n_el):
        # Determine legal stop_spacing range for this position.
        # We use try_design with various stop_spacings and let evaluate handle
        # validation.
        if stop_after == 0:
            s_min = 2.0
            s_max = 28.0  # near STOP_FRONT_CAP_MM
        else:
            j = stop_after - 1
            t_left = elements[j].get("max_thickness") or 10.0
            t_right = elements[j + 1].get("max_thickness") or 10.0
            air = spacings[j] - t_left / 2.0 - t_right / 2.0
            min_left = 2.0 if elements[j].get("max_thickness") is not None else 5.0
            min_right = 2.0 if elements[j + 1].get("max_thickness") is not None else 5.0
            s_min = max(min_left, min_rear_gap)
            s_max = air - ev.STOP_THICKNESS_MM - max(min_right, min_rear_gap)
            if s_max < s_min:
                continue
        s = s_min
        while s <= s_max + 1e-9:
            out = try_design(elements, spacings, stop_after, s)
            if out is not None:
                res, fs, sg = out
                gate = hard_gate(res, fs, sg, min_throat, min_rear_gap)
                if gate is None:
                    # Composite tradeoff score
                    # DEPRECATED: swirl_pct (Dallmeyer Petzval ratio) is the old
                    # conflated swirl metric, superseded by the swirl
                    # decomposition. Read only because the DB column persists.
                    swirl = res["swirl_pct"]
                    A = fs["A"]
                    P = fs["P"]
                    ap = A / P if abs(P) > 1e-12 else 0.0
                    bowl = max(0.0, -ap)
                    sharp = res.get("sharp_radius_pct") or 0.0
                    sens = res.get("max_bc_sensitivity") or 0.0
                    efl = res["efl"]
                    fN = res["f_number"]
                    # Hard gates for desire
                    if want_bowl and ap >= -0.02:
                        s += stop_step
                        continue
                    if not (min_efl <= efl <= max_efl):
                        s += stop_step
                        continue
                    # Composite
                    s_swirl = (1.0 if 120 <= swirl <= 150 else
                               0.8 if 100 < swirl < 120 else
                               0.6 if swirl >= 100 else
                               0.3)
                    s_efl = (1.0 if 95 <= efl <= 115 else 0.6)
                    s_speed = (1.0 if 2.2 <= fN <= 3.4 else 0.7)
                    s_bowl = (1.0 if bowl >= 0.05 else 0.7 + bowl * 6)
                    s_sharp = (1.0 if sharp >= 25 else
                               0.7 + 0.3 * sharp / 25 if sharp > 0 else
                               0.4)
                    s_tol = (1.0 if sens < 0.3 else
                             0.8 if sens < 0.6 else
                             0.5 if sens < 1.0 else 0.3)
                    s_flange = (1.0 if sg["flange_gap_camera"] >= 3.5
                                else 0.7)
                    s_throat = (1.0 if res["throat_margin"] >= 3.0 else
                                0.7 if res["throat_margin"] >= 1.0 else
                                0.5)
                    score = (s_swirl * s_efl * s_speed * s_bowl
                             * s_sharp * s_tol * s_flange * s_throat)
                    cand = (score, stop_after, s, res, fs, sg)
                    if best is None or cand[0] > best[0]:
                        best = cand
            s += stop_step
    return best


def make_label(elements, spacings, stop_after, stop_spacing, res, fs, sg):
    swirl = res["swirl_pct"]
    A = fs["A"]
    P = fs["P"]
    ap = A / P if abs(P) > 1e-12 else 0.0
    return (
        f"{'+'.join(e['code'] for e in elements)} sp={list(spacings)} "
        f"stop=after{stop_after}/{stop_spacing:.1f}  "
        f"EFL={res['efl']:.1f} f/{res['f_number']:.2f} "
        f"swirl={swirl:.1f}% A/P={ap:+.3f} "
        f"sharp={res['sharp_radius_pct']:.1f}% "
        f"sens={res['max_bc_sensitivity']:.2f} "
        f"back_clr={res['back_clearance']:.1f} throat={res['throat_margin']:+.1f} "
        f"flg_cam={sg['flange_gap_camera']:+.1f} "
        f"flg_lens={sg['flange_gap_lens']:+.1f}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--throat-min", type=float, default=2.0,
                    help="Minimum throat margin (mm)")
    ap.add_argument("--rear-gap-min", type=float, default=3.0,
                    help="Minimum stop-to-element gap (mm)")
    ap.add_argument("--bowl-only", action="store_true", default=True)
    args = ap.parse_args()

    inv = {e["code"]: e for e in list_elements()}

    # Hand-picked element triplets to explore based on first sweep
    triplets = [
        # ACH-middle, weak NMN front, BCX rear (existing v12 family)
        ("L11018", "L11561", "L13088"),
        ("L11018", "L14523", "L13088"),
        ("L11018", "L14929", "L13088"),
        ("L14234", "L11561", "L13088"),
        ("L14234", "L14523", "L13088"),
        ("L14234", "L14929", "L13088"),
        ("L7325", "L11561", "L13088"),
        ("L7325", "L14523", "L13088"),
        ("L7325", "L14929", "L13088"),
        ("L14801", "L11561", "L13088"),
        # big-PCX as rear (the sharpness winner)
        ("L11018", "L11561", "big-PCX"),
        ("L11018", "L14523", "big-PCX"),
        ("L11018", "L14929", "big-PCX"),
        ("L14234", "L11561", "big-PCX"),
        ("L14234", "L14523", "big-PCX"),
        ("L14234", "L14929", "big-PCX"),
        ("L7325", "L11561", "big-PCX"),
        ("L7325", "L14929", "big-PCX"),
        ("L14801", "L11561", "big-PCX"),
        # Other rear positives to consider
        ("L11018", "L11561", "LCV706"),
        ("L14234", "L11561", "LCV706"),
        ("L11018", "L11561", "Ed-31-795"),
        ("L11018", "L11561", "LCV5A5"),
        # ACH-rear variants for compare
        ("L11018", "L13088", "L11561"),
        ("L11018", "LCV5A5", "L11561"),
        # Ed-32-886 (big ACH 150mm 50mm dia) middle
        ("L11018", "Ed-32-886", "L13088"),
        ("L11018", "Ed-32-886", "big-PCX"),
        ("L14234", "Ed-32-886", "big-PCX"),
        # Two-singlet retro (no ACH) for comparison
        ("L11018", "LCV5A5", "L13088"),
        ("L11018", "L13088", "LCV5A5"),
        # 4-position N-P-A-P? skipped for now; 3-element only
    ]
    # Spacing grid around insightful values
    d_grid = [16.0, 18.0, 20.0, 22.0, 24.0, 26.0, 28.0, 30.0]

    rows = []
    for codes in triplets:
        try:
            elems = [inv[c] for c in codes]
        except KeyError:
            continue
        for d1 in d_grid:
            for d2 in d_grid:
                best = best_stop_for(
                    elems, [d1, d2],
                    min_throat=args.throat_min,
                    min_rear_gap=args.rear_gap_min,
                )
                if best is None:
                    continue
                score, sa, ss, res, fs, sg = best
                rows.append({
                    "codes": codes,
                    "spacings": [d1, d2],
                    "stop_after": sa,
                    "stop_spacing": ss,
                    "res": res,
                    "fs": fs,
                    "sg": sg,
                    "score": score,
                })

    rows.sort(key=lambda r: r["score"], reverse=True)
    seen_triplet = {}
    print(f"# {len(rows)} candidates (throat>={args.throat_min}, "
          f"rear_gap>={args.rear_gap_min}, bowl-only)")
    for r in rows:
        key = tuple(r["codes"])
        seen_triplet[key] = seen_triplet.get(key, 0) + 1
        if seen_triplet[key] > 3:
            continue
        elems = [inv[c] for c in r["codes"]]
        print(make_label(elems, r["spacings"], r["stop_after"], r["stop_spacing"],
                         r["res"], r["fs"], r["sg"]) +
              f"  score={r['score']:.3f}")


if __name__ == "__main__":
    main()
