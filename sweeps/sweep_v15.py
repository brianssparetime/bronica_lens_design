#!/usr/bin/env python3
"""v15 sweep: rank designs on the merged prio-1 Petzval-portrait character
(swirl_FoM + medial plateau + on-axis sharpness), then prio-2 EFL/speed and
prio-3 chromatic aberration, via a gated priority-weighted geometric mean.
See DESIGN-REQS.md prio 1 and "Coma" in
https://brianssparetime.com/posts/zenzanot-4-swirl/ (the coma-versus-astigmatism swirl
direction this generation is built to find).

Carried over unchanged from sweep_v14.py: the enumeration engine
(list_elements, by_type, stop_geometry, try_design, sweep_3) and the
flange-clearance hard rule.  What changed from v14 is the direction term:

  swirl is DIRECTION times DEPTH (swirl_pct, the field-curvature magnitude),
  but DIRECTION is a contest between astigmatism and coma, not the sign of
  astigmatism alone.  Coma is always radial; astigmatism is tangential only
  when positive AND larger than coma.  The v14 sweep read astig sign only and
  so scored deep tie-dye (positive astig, larger coma) as full swirl -- the
  exact failure that made the v14 family render radially.  direction_score now
  collapses the FoM to the tie-dye floor once coma dominates.  The flat center
  is the MEDIAL plateau_radius, distinct from on-axis sharp_radius.

Coma is therefore no longer a low-priority nudge alone; it gates the headline
swirl term through direction_score.  Things still NOT scored (reported for the
top-N under --verbose): image-circle fill and the concentrated-power flag, each
order-of-magnitude or without a principled target.
"""

import argparse
import itertools
import math
import sqlite3

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import evaluate as ev


FLANGE_Z = ev.THROAT_FAR_MM     # 101.7
# Flange forbidden band (z from flange): no part of the stop slot may sit in
# [-3.5, +5].  A slot is valid only wholly forward of +5 (outside the flange)
# or wholly behind -3.5 (deep in the body).
STOP_FORBID_CAMERA_MM = -3.5
STOP_FORBID_LENS_MM = 5.0


# ---------------------------------------------------------------------------
# Engine carried over verbatim from sweep_v13.py
# ---------------------------------------------------------------------------

def list_elements():
    con = sqlite3.connect(ev.INVENTORY_DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM elements WHERE purchased=1").fetchall()
    con.close()
    return [dict(r) for r in rows]


def by_type(elements, types):
    return [e for e in elements if e["type"] in types]


def stop_geometry(res, spacings, elements):
    """Stop slot z-coordinates from the film plane.  None if no stop."""
    sa = res["stop_after"]
    ss = res["stop_spacing"]
    if sa is None:
        return None
    thick = res["thickness"]
    dist = res["dist_to_focal"]
    half_t_stop = ev.STOP_THICKNESS_MM / 2.0

    if sa == 0:
        z_center = dist[0] + thick[0] / 2.0 + ss + half_t_stop
    else:
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
    """Hard pass/fail.  Returns reason string if fails, None if passes.

    Physical and sanity gates only.  Swirl DIRECTION is intentionally NOT a
    hard gate -- it is a soft floored sub-score in score().
    """
    if not res["bfd_compliant"]:
        return "bfd_compliant"
    if sg is None:
        return "no_stop"
    # Slot valid only wholly outside the flange (camera edge >= +5) or wholly
    # behind the band (lens edge <= -3.5).
    cg, lg = sg["flange_gap_camera"], sg["flange_gap_lens"]
    if not (cg >= STOP_FORBID_LENS_MM or lg <= STOP_FORBID_CAMERA_MM):
        return "stop_in_flange_forbidden_band"
    efl = res["efl"]
    if not math.isfinite(efl) or efl <= 70 or efl > 140:
        return "efl_out_of_range"
    fN = res["f_number"]
    if not math.isfinite(fN) or fN < 1.9 or fN > 4.0:
        return "speed_out_of_range"
    # P>0 (petzval inward) is necessary for any swirl OR tie-dye; an outward
    # field bowls the wrong way and produces neither.
    if fs["P"] <= 0:
        return "petzval_outward"
    return None


# ---------------------------------------------------------------------------
# Scoring (rewritten for v14)
# ---------------------------------------------------------------------------

def _ramp(x, lo, hi, ylo, yhi):
    """Linear interpolation of y from ylo (at x<=lo) to yhi (at x>=hi)."""
    if x <= lo:
        return ylo
    if x >= hi:
        return yhi
    return ylo + (yhi - ylo) * (x - lo) / (hi - lo)


def direction_score(swirl_astig, coma_indicator):
    """Soft direction sub-score gating swirl_FoM on the coma-versus-astigmatism
    contest (see evaluate.swirl_orientation and "Coma" in
    https://brianssparetime.com/posts/zenzanot-4-swirl/).

    Coma always smears the corner radially; astigmatism smears it tangentially
    only when its sign is positive AND it outweighs coma.  Two factors multiply:
      - streak: astigmatic magnitude, ramping 0.1 -> 1.0 by swirl_astig ~ 0.30
      - coma_factor: 1.0 while astigmatism clearly dominates coma
        (|coma|/astig <= 0.5), collapsing to 0 once coma dominates
        (|coma|/astig >= 2.0, the tie-dye regime)
    The result floors at the 0.1 tie-dye level, so a coma-dominated or radial
    design is demoted exactly as a negative-astigmatism design is.  This
    replaces the v14 sweep's astig-sign-only direction, which scored deep
    tie-dye (positive astig, larger coma) as full swirl."""
    if swirl_astig <= 0:
        return 0.1
    streak = _ramp(swirl_astig, 0.0, 0.30, 0.1, 1.0)
    ratio = abs(coma_indicator) / swirl_astig
    coma_factor = _ramp(ratio, 0.5, 2.0, 1.0, 0.0)
    return 0.1 + (streak - 0.1) * coma_factor


def s_swirl_fom(fom):
    """prio-1 swirl: band on swirl_FoM = swirl_pct * direction_score.
    Optimal 125-150, preferred >100, acceptable ~100.  Gentle decline above
    150 (over-deep field over-curves and shrinks the plateau -- the plateau
    term carries most of that penalty)."""
    if 125 <= fom <= 150:
        return 1.0
    if fom > 150:
        return _ramp(fom, 150, 220, 1.0, 0.8)
    if 100 <= fom < 125:
        return _ramp(fom, 100, 125, 0.7, 1.0)
    if 90 <= fom < 100:
        return _ramp(fom, 90, 100, 0.5, 0.7)
    return _ramp(fom, 60, 90, 0.1, 0.5)


def s_plateau(pct):
    """prio-1 flat center: band on medial plateau radius (% half-diagonal).
    Optimal >=40, preferred >=30, acceptable >=20."""
    if pct >= 40:
        return 1.0
    if pct >= 30:
        return _ramp(pct, 30, 40, 0.7, 1.0)
    if pct >= 20:
        return _ramp(pct, 20, 30, 0.4, 0.7)
    return _ramp(pct, 0, 20, 0.1, 0.4)


def s_sharp(pct):
    """prio-1 on-axis sharpness: band on sharp_radius (% half-diagonal)."""
    if pct >= 33:
        return 1.0
    if pct >= 25:
        return _ramp(pct, 25, 33, 0.8, 1.0)
    if pct >= 12:
        return _ramp(pct, 12, 25, 0.4, 0.8)
    return _ramp(pct, 0, 12, 0.1, 0.4)


def s_efl(efl):
    """prio-2 EFL: optimal ~100, preferred to 110, acceptable to 135."""
    if 95 <= efl <= 105:
        return 1.0
    if 105 < efl <= 110:
        return _ramp(efl, 105, 110, 1.0, 0.85)
    if 90 <= efl < 95:
        return _ramp(efl, 90, 95, 0.85, 1.0)
    if 110 < efl <= 135:
        return _ramp(efl, 110, 135, 0.85, 0.4)
    if 80 <= efl < 90:
        return _ramp(efl, 80, 90, 0.4, 0.85)
    return 0.1


def s_speed(fN):
    """prio-2 speed: optimal ~f/2.4, preferred f/2-3.2, acceptable to f/4."""
    if 2.2 <= fN <= 2.8:
        return 1.0
    if 2.0 <= fN < 2.2:
        return _ramp(fN, 2.0, 2.2, 0.85, 1.0)
    if 2.8 < fN <= 3.2:
        return _ramp(fN, 2.8, 3.2, 1.0, 0.8)
    if 3.2 < fN <= 4.0:
        return _ramp(fN, 3.2, 4.0, 0.8, 0.5)
    return 0.1


def s_ca(lca):
    """prio-3 chromatic aberration: |F-C| longitudinal, mm."""
    a = abs(lca)
    if a < 0.3:
        return 1.0
    if a < 0.6:
        return _ramp(a, 0.3, 0.6, 1.0, 0.7)
    if a < 1.0:
        return _ramp(a, 0.6, 1.0, 0.7, 0.5)
    return 0.3


def s_tol(sens):
    """low-prio spacing tolerance: lower max BFD sensitivity is better."""
    if sens < 0.3:
        return 1.0
    if sens < 0.6:
        return 0.8
    if sens < 1.0:
        return 0.6
    if sens < 1.5:
        return 0.4
    return 0.2


def s_protrusion(protrusion_mm):
    """low-prio: a lens that protrudes less past the flange is mildly
    preferred.  Keyed to the front element's subject-side face, not stack
    length -- recessed glass aids compactness even in a long stack."""
    return _ramp(protrusion_mm, 40, 100, 1.0, 0.3)


def s_coma(coma_blur):
    """low-prio: coma is a radial swirl contaminant, but reducing it fights
    swirl and the magnitude is order-of-magnitude only -- so it only nudges."""
    if coma_blur < 0.05:
        return 1.0
    if coma_blur < 0.15:
        return _ramp(coma_blur, 0.05, 0.15, 1.0, 0.5)
    return _ramp(coma_blur, 0.15, 0.5, 0.5, 0.1)


def s_flange(sg):
    """low-prio: prefer the stop slot outside the flange (camera-side edge
    forward of +5) over the deep-in-body placement, which is valid but less
    convenient to build and operate."""
    cg = sg["flange_gap_camera"]
    if cg >= STOP_FORBID_LENS_MM:
        return 1.0      # wholly outside the flange: preferred side
    return 0.5          # valid only by sitting behind the band, deep inside


# Priority exponents for the geometric mean: prio-1 cluster heaviest
# (swirl 3, plateau 2, sharp 1), prio-2 EFL/speed 2 each, prio-3 CA 0.5.
PRIO_WEIGHTS = {"fom": 3.0, "pl": 2.0, "sh": 1.0,
                "ef": 2.0, "sp": 2.0, "ca": 0.5}


def _plateau_pct(res):
    pct = res.get("plateau_radius_pct")
    if pct is None or not math.isfinite(pct):
        # A flat medial surface yields an unbounded plateau; cap at the frame.
        return 100.0
    return pct


def score(res, fs, sg):
    """Gated priority-weighted geometric mean.  Higher is better.  Returns
    the full sub-score vector so the ranking stays inspectable."""
    swirl_pct = res["swirl_pct"]
    sw_astig = res["swirl_astig_mm"]
    plat_pct = _plateau_pct(res)
    sharp_pct = res.get("sharp_radius_pct") or 0.0
    efl = res["efl"]
    fN = res["f_number"]
    lca = res.get("lca") or 0.0
    sens = res.get("max_bc_sensitivity") or 0.0
    # Protrusion: front element's subject-side face, z from the flange.
    dist = res["dist_to_focal"]
    thick = res["thickness"]
    protrusion = dist[0] + thick[0] / 2.0 - FLANGE_Z
    coma = res.get("coma_indicator_mm") or 0.0
    coma_blur = abs(coma) / fN if fN > 0 and math.isfinite(fN) else 0.0

    d = direction_score(sw_astig, coma)
    fom = swirl_pct * d

    s_fom = s_swirl_fom(fom)
    s_pl = s_plateau(plat_pct)
    s_sh = s_sharp(sharp_pct)
    s_ef = s_efl(efl)
    s_sp = s_speed(fN)
    s_c = s_ca(lca)

    w = PRIO_WEIGHTS
    wsum = sum(w.values())
    base = (
        s_fom ** w["fom"] * s_pl ** w["pl"] * s_sh ** w["sh"]
        * s_ef ** w["ef"] * s_sp ** w["sp"] * s_c ** w["ca"]
    ) ** (1.0 / wsum)

    # Low-priority tiebreakers: mild multipliers near 1, never decisive.
    tie = (
        (0.85 + 0.15 * s_tol(sens))
        * (0.95 + 0.05 * s_flange(sg))
        * (0.97 + 0.03 * s_protrusion(protrusion))
        * (0.90 + 0.10 * s_coma(coma_blur))
    )
    total = base * tie

    ap_ratio = fs["A"] / fs["P"] if abs(fs["P"]) > 1e-12 else 0.0
    return {
        "swirl_fom": fom,
        "direction": d,
        "ap_ratio": ap_ratio,
        "coma_blur": coma_blur,
        "s_fom": s_fom, "s_plateau": s_pl, "s_sharp": s_sh,
        "s_efl": s_ef, "s_speed": s_sp, "s_ca": s_c,
        "base": base, "tie": tie, "total": total,
    }


def pareto_front(results):
    """Non-dominated set on the three highest-tension axes:
    swirl_FoM (up), medial plateau % (up), EFL closeness score (up).
    A scalar ranking averages these away; the frontier keeps the tradeoff
    winners visible.

    The raw sweep emits hundreds of thousands of near-identical designs
    (one per spacing/stop micro-variation), which would make the O(n^2)
    domination check intractable and the frontier unreadable.  We first
    collapse them onto a coarse grid -- FoM to 5, plateau to 2%, EFL-score
    to 0.05 -- keeping the best-scoring design per cell, then take the
    frontier of those representatives."""
    def axes(r):
        res = r["result"]
        return (r["score"]["swirl_fom"], _plateau_pct(res), s_efl(res["efl"]))

    # Collapse near-duplicates: best total per coarse (FoM, plateau, EFL) cell.
    cells = {}
    for r in results:
        fom, plat, efl_s = axes(r)
        cell = (round(fom / 5.0), round(plat / 2.0), round(efl_s / 0.05))
        cur = cells.get(cell)
        if cur is None or r["score"]["total"] > cur["score"]["total"]:
            cells[cell] = r
    reps = list(cells.values())

    keys = [axes(r) for r in reps]
    front = []
    for i, kr in enumerate(keys):
        dominated = False
        for j, kq in enumerate(keys):
            if i == j:
                continue
            if all(a >= b for a, b in zip(kq, kr)) and \
               any(a > b for a, b in zip(kq, kr)):
                dominated = True
                break
        if not dominated:
            front.append(reps[i])
    return front


# ---------------------------------------------------------------------------
# Search engine (carried over from v13)
# ---------------------------------------------------------------------------

def sweep_n(inventory, spacings_grid, stop_grid, archs):
    """General N-element sweep (N = number of letters in the arch name).

    Architecture names are slot letters joined by '-':  N (negative), P
    (positive singlet), A (cemented achromat).  Examples: 'N-A-P' (the v13
    family), 'N-A-P-P', 'N-P-A-P'.  compute_design already handles any element
    count; this just enumerates element/spacing/stop combinations for an
    arbitrary slot list and reuses the same gate and score as sweep_3.
    """
    slot_of = {
        "N": by_type(inventory, ("NMN", "DCV", "BCV", "PCV")),
        "P": by_type(inventory, ("DCX", "BCX", "PCX", "PMN")),
        "A": by_type(inventory, ("ACH",)),
    }
    results = []
    for arch_name in archs:
        codes = arch_name.split("-")
        slots = [slot_of[c] for c in codes]
        n = len(slots)
        n_gaps = n - 1
        for combo in itertools.product(*slots):
            cset = [e["code"] for e in combo]
            if len(set(cset)) != n:
                continue
            elems = list(combo)
            for spc in itertools.product(spacings_grid, repeat=n_gaps):
                spacings = list(spc)
                for stop_after, stop_spacing in stop_grid:
                    if stop_after > n_gaps:
                        continue
                    if (stop_after >= 1
                            and stop_spacing >= spacings[stop_after - 1] - 4):
                        continue
                    out = try_design(elems, spacings, stop_after, stop_spacing)
                    if out is None:
                        continue
                    res, fs, sg = out
                    if hard_gate(res, fs, sg) is not None:
                        continue
                    sc = score(res, fs, sg)
                    results.append({
                        "arch": arch_name,
                        "elements": cset,
                        "spacings": spacings,
                        "stop_after": stop_after,
                        "stop_spacing": stop_spacing,
                        "result": res,
                        "fs": fs,
                        "sg": sg,
                        "score": sc,
                    })
    return results


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


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def fmt_row(r):
    res = r["result"]
    sg = r["sg"]
    sc = r["score"]
    return (
        f"{r['arch']:<7} "
        f"{'+'.join(r['elements']):<30} "
        f"sp={r['spacings']} "
        f"st={r['stop_after']}/{r['stop_spacing']:.1f} "
        f"EFL={res['efl']:.0f} f/{res['f_number']:.2f} "
        f"FoM={sc['swirl_fom']:.0f} "
        f"dir={res['swirl_astig_mm']:+.2f} "
        f"plat={_plateau_pct(res):.0f}% "
        f"sharp={res['sharp_radius_pct']:.0f}% "
        f"sens={res['max_bc_sensitivity']:.2f} "
        f"flg={sg['flange_gap_camera']:+.1f}/{sg['flange_gap_lens']:+.1f} "
        f"sc={sc['total']:.3f}"
    )


def fmt_verbose(r):
    res = r["result"]
    sc = r["score"]
    sub = (f"      sub: s_fom={sc['s_fom']:.2f} s_plat={sc['s_plateau']:.2f} "
           f"s_sharp={sc['s_sharp']:.2f} s_efl={sc['s_efl']:.2f} "
           f"s_speed={sc['s_speed']:.2f} s_ca={sc['s_ca']:.2f} "
           f"base={sc['base']:.3f} tie={sc['tie']:.3f}")
    # Diagnostics reported but deliberately NOT scored.
    diag = (f"      diag (unscored): coma~{sc['coma_blur']:.2f}mm  "
            f"img-circle fill={res['stop_score']:.2f}  "
            f"A/P={sc['ap_ratio']:+.3f}  "
            f"neg-power={res['conc_neg_label']}")
    return sub + "\n" + diag


def main():
    ap = argparse.ArgumentParser(
        description="v15 sweep: Petzval-portrait swirl_FoM (coma-gated "
                    "direction) + plateau ranking.")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--archs", nargs="+",
                    default=["N-A-P", "N-P-A", "P-N-A", "A-N-P", "A-P-N",
                             "P-A-N", "P-N-P", "N-P-P"])
    ap.add_argument("--quick", action="store_true", help="Coarser grid")
    ap.add_argument("--swirl-only", action="store_true",
                    help="Filter to background-swirl direction (swirl_astig>0)")
    ap.add_argument("--pareto", action="store_true",
                    help="Show the non-dominated frontier on "
                         "(swirl_FoM, plateau, EFL) instead of the ranked list")
    ap.add_argument("--verbose", action="store_true",
                    help="Print the sub-score vector and unscored diagnostics "
                         "for each shown design")
    ap.add_argument("--sortby", default="total",
                    choices=["total", "swirl_fom", "plateau", "sharp", "efl",
                             "sens", "flange"])
    args = ap.parse_args()

    inventory = list_elements()

    max_n = max(len(a.split("-")) for a in args.archs)

    if max_n >= 4:
        # 4-element: 3 gaps, so a coarse spacing grid keeps the product
        # tractable.  Span narrow-to-wide to exploit the unused barrel length.
        spacings_grid = [12.0, 20.0, 28.0] if not args.quick else [14.0, 24.0]
        stop_grid = [(0, 4.0),
                     (1, 2.0), (1, 6.0), (1, 12.0), (1, 18.0),
                     (2, 2.0), (2, 6.0), (2, 12.0), (2, 18.0),
                     (3, 2.0), (3, 6.0), (3, 12.0), (3, 18.0)]
    elif args.quick:
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

    results = sweep_n(inventory, spacings_grid, stop_grid, args.archs)

    if args.swirl_only:
        results = [r for r in results if r["result"]["swirl_astig_mm"] > 0]

    if args.pareto:
        results = pareto_front(results)

    sort_keys = {
        "swirl_fom": (lambda r: r["score"]["swirl_fom"], True),
        "plateau": (lambda r: _plateau_pct(r["result"]), True),
        "sharp": (lambda r: r["result"]["sharp_radius_pct"], True),
        "efl": (lambda r: s_efl(r["result"]["efl"]), True),
        "sens": (lambda r: r["result"]["max_bc_sensitivity"], False),
        "flange": (lambda r: r["sg"]["flange_gap_camera"], True),
        "total": (lambda r: r["score"]["total"], True),
    }
    keyfn, rev = sort_keys[args.sortby]
    results.sort(key=keyfn, reverse=rev)

    label = "Pareto-frontier" if args.pareto else "compliant"
    print(f"# {len(results)} {label} designs "
          f"(stop slot clear of the flange band [{STOP_FORBID_CAMERA_MM:+.1f},"
          f"{STOP_FORBID_LENS_MM:+.1f}]mm; sorted by {args.sortby})")
    print()
    for r in results[:args.top]:
        print(fmt_row(r))
        if args.verbose:
            print(fmt_verbose(r))


if __name__ == "__main__":
    main()
