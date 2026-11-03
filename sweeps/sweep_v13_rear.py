#!/usr/bin/env python3
"""Sweep rear-positive substitutes for big-PCX in the v13d/v13e family,
looking for designs that preserve speed/sharpness without the 127mm element.
"""

import math
import sqlite3

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import evaluate as ev


FLANGE_Z = ev.THROAT_FAR_MM
HARD_STOP_FLANGE_MM = 2.5


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
        "z_center": z_center,
        "cam_edge": z_center - 1.0,
        "lens_edge": z_center + 1.0,
        "flange_cam": z_center - 1.0 - FLANGE_Z,
        "flange_lens": z_center + 1.0 - FLANGE_Z,
    }


def get_elements():
    con = sqlite3.connect(ev.INVENTORY_DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM elements WHERE purchased=1").fetchall()
    con.close()
    return {r["code"]: dict(r) for r in rows}


def try_d(elems, sp, sa, ss):
    try:
        res = ev.compute_design(elems, sp, sa, ss)
    except SystemExit:
        return None
    except Exception:
        return None
    fl = [e["focal_length"] for e in elems]
    fs = ev.compute_field_shape(
        fl, res.get("stop_chief_heights"),
        res["petzval_radius"], ev.FRAME_HALF_DIAG_MM,
    )
    sg = stop_geometry(res)
    return res, fs, sg


def evaluate_candidate(elems, sp, sa, ss):
    out = try_d(elems, sp, sa, ss)
    if out is None:
        return None
    res, fs, sg = out
    if not res["bfd_compliant"]:
        return None
    if sg["flange_cam"] < HARD_STOP_FLANGE_MM:
        return None
    if sg["flange_lens"] < HARD_STOP_FLANGE_MM:
        return None
    return res, fs, sg


def main():
    inv = get_elements()

    # Front candidates: weakest negatives for low spherical
    fronts = ["L11018", "L14234", "L7325"]
    # ACH middle candidates
    middles = ["L11561", "L14523", "L14929", "Ed-32-886"]
    # Rear positives in the SS catalog at reasonable diameter
    rears = [
        "L10938",      # PCX 216mm 42mm dia (in inventory)
        "L11018",      # placeholder; only positives
    ]
    # Build full positive-rear list from inventory + Surplus shed names that
    # we don't own.  For ones we don't own, we cannot run them (not in DB).
    # Just use inventory positives instead.
    positives_inv = [code for code, e in inv.items()
                     if e["type"] in ("PCX", "DCX", "BCX")
                     and 100 <= e["focal_length"] <= 250]
    rears = positives_inv

    spacings = [12, 16, 20, 22, 24, 26, 28, 30]
    stop_spacings = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0]

    results = []
    for f in fronts:
        for m in middles:
            for r in rears:
                if len({f, m, r}) < 3:
                    continue
                elems = [inv[f], inv[m], inv[r]]
                for d1 in spacings:
                    for d2 in spacings:
                        for ss in stop_spacings:
                            # stop_after=2 only
                            res = evaluate_candidate(elems, [d1, d2], 2, ss)
                            if res is None:
                                continue
                            r_, fs, sg = res
                            efl = r_["efl"]
                            fN = r_["f_number"]
                            if not (90 <= efl <= 130):
                                continue
                            if fN < 2.0 or fN > 4.0:
                                continue
                            if fs["P"] <= 0:
                                continue
                            ap = fs["A"] / fs["P"]
                            sharp = r_["sharp_radius_pct"] or 0.0
                            results.append({
                                "elems": [f, m, r],
                                "sp": [d1, d2],
                                "ss": ss,
                                "efl": efl,
                                "fN": fN,
                                # DEPRECATED: swirl_pct (Dallmeyer Petzval ratio)
                                # is the old conflated swirl metric, superseded by
                                # the swirl decomposition. Read only because
                                # the DB column persists.
                                "swirl": r_["swirl_pct"],
                                "ap": ap,
                                "sharp": sharp,
                                "sens": r_["max_bc_sensitivity"],
                                "throat": r_["throat_margin"],
                                "back_clr": r_["back_clearance"],
                                "flg_cam": sg["flange_cam"],
                                "flg_lens": sg["flange_lens"],
                                "axial_sph": r_["axial_spherical_coc_mm"],
                                "f_soft": r_["f_soft"],
                            })

    # Sort by sharp%, then by score-like combo
    def score(r):
        s_swirl = (1 if r["swirl"] >= 120 else 0.7 if r["swirl"] >= 100 else 0.3)
        s_efl = (1 if 95 <= r["efl"] <= 115 else 0.6)
        s_speed = (1 if 2.2 <= r["fN"] <= 3.4 else 0.7)
        s_bowl = (1 if r["ap"] <= -0.05 else 0.7 if r["ap"] < 0 else 0.4)
        s_sharp = (1 if r["sharp"] >= 25 else 0.7 + 0.3*r["sharp"]/25
                   if r["sharp"] > 0 else 0.4)
        s_throat = (1 if r["throat"] >= 3 else 0.7 if r["throat"] >= 0 else 0.4)
        s_flange = (1 if r["flg_cam"] >= 3.5 else 0.7)
        return s_swirl * s_efl * s_speed * s_bowl * s_sharp * s_throat * s_flange

    for r in results:
        r["score"] = score(r)

    results.sort(key=lambda r: (r["score"], r["sharp"]), reverse=True)

    print(f"# {len(results)} candidates (no big-PCX, 90<=EFL<=130, "
          f"f/<=4, stop>={HARD_STOP_FLANGE_MM}mm from flange)")
    seen = {}
    for r in results:
        key = tuple(r["elems"])
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 2:
            continue
        print(f"{'+'.join(r['elems']):<28} sp={r['sp']} ss={r['ss']:.1f}  "
              f"EFL={r['efl']:5.1f} f/{r['fN']:.2f} (soft f/{r['f_soft']:.2f}) "
              f"swirl={r['swirl']:5.1f}% A/P={r['ap']:+.3f} "
              f"sharp={r['sharp']:5.1f}% "
              f"sens={r['sens']:.2f} back_clr={r['back_clr']:.1f} "
              f"thrt={r['throat']:+.1f} flg={r['flg_cam']:+.1f}/{r['flg_lens']:+.1f} "
              f"sph={r['axial_sph']:.3f} sc={r['score']:.3f}")


if __name__ == "__main__":
    main()
