#!/usr/bin/env python3
"""Evaluate candidate lens designs using sequential conjugate propagation.

Usage:
    python3 evaluate.py "Design Name" ELEM1 spacing1 ELEM2 spacing2 ELEM3 [stop N S]

Elements are looked up by code from the inventory database. Spacings are in mm.
Optional 'stop N S' places the aperture stop after element N, S mm behind it.
If omitted, the stop defaults to after the last positive-FL element, 5mm behind.
"""

import argparse
import math
import sqlite3
import sys
from datetime import datetime

from design_db import resolve_design_name, DesignNameError

INVENTORY_DB = "inventory.db"
DESIGNS_DB = "lens_designs.db"

DALLMEYER_PETZVAL_RADIUS = 200.0  # mm reference benchmark
DEFAULT_THICKNESS = 10.0  # mm, assumed when element max_thickness is unknown
SHARP_COC_THRESHOLD_MM = 0.05  # 50um, conventional "sharp" criterion for 6x6
FRAME_HALF_DIAG_MM = 39.6  # sqrt(2)*28 for the 56mm square Bronica S2 frame

# Aperture stop physical model.  An iris diaphragm is treated as a 2mm
# thick disc that lives somewhere along the optical axis.  Auto-search
# considers positions in the air gap in front of element 1 (capped at
# STOP_FRONT_CAP_MM ahead of the front vertex) and inside every
# inter-element gap, discretized at STOP_SEARCH_STEP_MM.
STOP_THICKNESS_MM = 2.0
STOP_FRONT_CAP_MM = 30.0
STOP_SEARCH_STEP_MM = 0.5

# Default crown/flint glass model for cemented achromats (ACH).  We do not
# yet store per-element glass data, so any ACH element is assumed to be a
# standard BK7 (crown) + F2 (flint) cemented doublet.  This is used only for
# the Petzval sum, where treating the doublet as a single n=1.5 element gives
# the wrong contribution.
ACH_CROWN_INDEX = 1.5168  # BK7
ACH_CROWN_ABBE  = 64.17
ACH_FLINT_INDEX = 1.620   # F2
ACH_FLINT_ABBE  = 36.43

# Bronica S2a body geometry (mm from film plane)
THROAT_FAR_MM = 101.7    # flange focal distance (flange plane = z 0)

# Stepped diameter clearance zones, keyed off an element's deepest face
# position z measured from the flange (z = 0, +z toward the subject).  As
# the lens recedes into the body the bore narrows: the limit drops in steps
# until the hard depth floor, past which nothing fits regardless of size.
CLEARANCE_DEPTH_FLOOR_MM = -30.0


def clearance_diam_limit(z):
    """Max element diameter (mm) allowed at deepest-face position z (mm from
    flange).  Returns float('inf') where unconstrained, or None when z is
    past the hard depth floor (any diameter violates)."""
    if z < CLEARANCE_DEPTH_FLOOR_MM:
        return None
    if z > 30.0:
        return float("inf")
    if z > -7.3:
        return 52.7
    if z > -20.3:
        return 50.45
    return 45.7


DESIGNS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS designs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    created                 TEXT NOT NULL,
    efl                     REAL NOT NULL,
    bfd                     REAL NOT NULL,
    back_clearance          REAL,
    inf_offset              REAL,
    f_number                REAL NOT NULL,
    vignetting_abruptness   REAL,
    petzval_radius          REAL NOT NULL,
    swirl_pct               REAL NOT NULL,
    swirl_astig_mm          REAL,
    plateau_radius_mm       REAL,
    sharp_radius_mm         REAL,
    sharp_radius_pct        REAL,
    corner_field_coc_mm     REAL,
    bfd_compliant           INTEGER NOT NULL,
    throat_margin           REAL,
    stop_after              INTEGER,
    stop_spacing            REAL,
    lca                     REAL,
    total_length            REAL,
    max_bc_sensitivity     REAL,
    notes                   TEXT
);

CREATE TABLE IF NOT EXISTS design_elements (
    design_id        INTEGER NOT NULL,
    position         INTEGER NOT NULL,
    element_code     TEXT NOT NULL,
    spacing_after    REAL,
    bfd_sensitivity  REAL,
    efl_sensitivity  REAL,
    orientation      TEXT,
    PRIMARY KEY (design_id, position),
    FOREIGN KEY (design_id) REFERENCES designs(id)
);
"""
# design_elements.orientation: which way the element's curved/convex/crown
# surface faces along the optical axis.  Only meaningful for the asymmetric,
# bending-undetermined families -- meniscus (NMN/PMN) and achromat (ACH) --
# where the model assigns shape factor q = 0 and the build orientation is a
# free choice that must be recorded.
#   'CX_FRONT' -- curved/strong side toward the object (front of the lens)
#   'CX_REAR'  -- curved/strong side toward the camera (image)
#   NULL       -- unspecified.  Correct for symmetric DCX/DCV/BCX/BCV (no
#                 meaning) and for PCX/PCV (evaluate.py fixes those at the
#                 curved-side-front q = +1 convention, so no field is needed).


def migrate_designs_db(con):
    """Add new columns to existing designs / design_elements tables."""
    cols = {row[1] for row in con.execute("PRAGMA table_info(designs)")}
    # swirl_astig_mm: signed corner swirl direction (+ = background swirl,
    #   - = tie-dye; see compute_design).  plateau_radius_mm: medial-surface
    #   plateau.  Both nullable -- pre-Tier-2 rows carry NULL until rebuilt.
    #   plateau pct is intentionally NOT stored (a pure transform of mm,
    #   derived at display), unlike the older redundant sharp_radius_pct.
    # f_soft (the max aperture with the limiting element removed) is recomputed
    # at display time, not stored; drop the legacy column if a pre-existing DB
    # still carries it.
    if "f_soft" in cols:
        con.execute("ALTER TABLE designs DROP COLUMN f_soft")
    # mirror_margin retired: clearance is now the stepped per-element diameter
    # check against the depth floor, not a single back-clearance margin.
    if "mirror_margin" in cols:
        con.execute("ALTER TABLE designs DROP COLUMN mirror_margin")
    if "swirl_astig_mm" not in cols:
        con.execute("ALTER TABLE designs ADD COLUMN swirl_astig_mm REAL")
    if "plateau_radius_mm" not in cols:
        con.execute("ALTER TABLE designs ADD COLUMN plateau_radius_mm REAL")
    if "back_clearance" not in cols:
        con.execute("ALTER TABLE designs ADD COLUMN back_clearance REAL")
    if "inf_offset" not in cols:
        con.execute("ALTER TABLE designs ADD COLUMN inf_offset REAL")
    if "sharp_radius_mm" not in cols:
        con.execute("ALTER TABLE designs ADD COLUMN sharp_radius_mm REAL")
    if "sharp_radius_pct" not in cols:
        con.execute("ALTER TABLE designs ADD COLUMN sharp_radius_pct REAL")
    if "corner_field_coc_mm" not in cols:
        con.execute("ALTER TABLE designs ADD COLUMN corner_field_coc_mm REAL")
    if "max_bc_sensitivity" not in cols:
        if "max_bfd_sensitivity" in cols:
            con.execute("ALTER TABLE designs RENAME COLUMN "
                        "max_bfd_sensitivity TO max_bc_sensitivity")
        else:
            con.execute("ALTER TABLE designs ADD COLUMN max_bc_sensitivity REAL")
    elem_cols = {row[1] for row in con.execute("PRAGMA table_info(design_elements)")}
    if "bfd_sensitivity" not in elem_cols:
        con.execute("ALTER TABLE design_elements ADD COLUMN bfd_sensitivity REAL")
    if "efl_sensitivity" not in elem_cols:
        con.execute("ALTER TABLE design_elements ADD COLUMN efl_sensitivity REAL")
    if "orientation" not in elem_cols:
        con.execute("ALTER TABLE design_elements ADD COLUMN orientation TEXT")


# ---------------------------------------------------------------------------
# Inventory lookup
# ---------------------------------------------------------------------------

def ensure_hypothetical_in_inventory(code, elem_type, focal_length, diameter):
    """Insert a hypothetical (non-purchased) element into the inventory DB."""
    abbe = 1000 if elem_type == "ACH" else 64
    con = sqlite3.connect(INVENTORY_DB)
    con.execute(
        "INSERT OR REPLACE INTO elements "
        "(code, type, focal_length, diameter, order_id, refractive_index, "
        " abbe_number, purchased) "
        "VALUES (?, ?, ?, ?, 'hypothetical', 1.5, ?, 0)",
        (code, elem_type, focal_length, diameter, abbe),
    )
    con.commit()
    con.close()


def get_elements(codes):
    """Look up elements from the inventory database."""
    con = sqlite3.connect(INVENTORY_DB)
    con.row_factory = sqlite3.Row
    elements = []
    for code in codes:
        row = con.execute(
            "SELECT code, type, focal_length, diameter, refractive_index, "
            "       abbe_number, max_thickness, edge_thickness, mounted_od "
            "FROM elements WHERE code = ?",
            (code,),
        ).fetchone()
        if row is None:
            con.close()
            sys.exit(f"Error: element '{code}' not found in inventory.")
        elements.append(dict(row))
    con.close()
    return elements


# ---------------------------------------------------------------------------
# Optical calculations
# ---------------------------------------------------------------------------

def petzval_contribution(elem):
    """Per-element contribution to the Petzval sum (1/R_p).

    For singlets this is the standard 1/(n*f).  For cemented achromats we
    split the doublet into its crown and flint components using the
    achromat condition (zero F-C focal shift):

        phi_c = phi_total * V_c / (V_c - V_f)
        phi_f = -phi_total * V_f / (V_c - V_f)

    and sum 1/(n_c f_c) + 1/(n_f f_f).  The single-element approximation
    1/(1.5 * f_ach) is wrong because the two glasses' focal lengths are
    individually much shorter than f_total and have opposite signs.
    """
    f = elem["focal_length"]
    if elem["type"] == "ACH":
        n_c, v_c = ACH_CROWN_INDEX, ACH_CROWN_ABBE
        n_f, v_f = ACH_FLINT_INDEX, ACH_FLINT_ABBE
        f_c = f * (v_c - v_f) / v_c
        f_f = -f * (v_c - v_f) / v_f
        return 1.0 / (n_c * f_c) + 1.0 / (n_f * f_f)
    return 1.0 / (elem["refractive_index"] * f)


def spherical_lsa_factor(elem):
    """Third-order longitudinal spherical aberration factor G for a thin
    lens at infinity, per Smith (Modern Lens Design):

        LA' = (y^2 / (8 f)) * G(n, q)

    where y is the marginal ray height at the lens, f the focal length,
    n the refractive index, and q = (R2 + R1) / (R2 - R1) the Coddington
    shape factor.  Returns G; the caller multiplies by y^2/(8f).

    Shape factor by type (object at infinity):
        BCX/DCX/BCV/DCV  symmetric         q = 0
        PCX/PCV      curved-side-front     q = +1   (R2 = inf)
        PMN/NMN      bending undetermined  q = 0    (neutral default)
        ACH          designed to correct spherical  -> return 0

    The thin-lens-in-air, isolated-element formulation has known
    limitations: it does not capture cross-coupling between separated
    elements, and it assumes object at infinity (true for our use case).
    For symmetric BK7 (n=1.5, q=0) it gives G = 22, matching textbook
    values.  For best-form BK7 (q ~= 0.71) it gives G ~= 17.2 (the
    minimum).  These are the right magnitudes; the formula is adequate
    for an order-of-magnitude assessment of "is this design dominated
    by spherical, by field curvature, or by neither?"
    """
    typ = elem["type"]
    if typ == "ACH":
        return 0.0
    q_table = {
        "BCX": 0.0, "DCX": 0.0, "BCV": 0.0, "DCV": 0.0,
        "PCX": 1.0, "PCV": 1.0,
        "PMN": 0.0, "NMN": 0.0,
    }
    q = q_table.get(typ, 0.0)
    n = elem["refractive_index"]
    if n <= 1.0 + 1e-9:
        return 0.0
    return (
        (n + 2) / (n * (n - 1)**2) * q**2
        - 4 * (n + 1) / (n * (n - 1)) * q
        + (3 * n + 2) / (n - 1)
        + n**2 / (n - 1)**2
    )


def principal_plane_offsets(elem):
    """Return (delta_front, delta_rear, t) for an element.

    delta_front = signed offset from front vertex to front principal plane
                  (positive = into the glass, toward the rear)
    delta_rear  = signed offset from rear vertex to rear principal plane
                  (negative = into the glass, toward the front; this is
                  the usual case for a positive lens)
    t           = axial thickness used (max_thickness if known, else
                  DEFAULT_THICKNESS)

    Shape resolution by type:
      BCX/DCX/BCV/DCV (symmetric)  solve thick lensmaker for R, derive PPs
      PCX/PCV (curved-side-front)  closed form: (0, -t/n)
      anything else, OR unknown thickness  thin-lens-at-center fallback
                                           (+t/2, -t/2)

    The fallback algebraically reproduces today's center-to-center
    propagation: substituting (+t_{k+1}/2) - (-t_k/2) into the PP-distance
    formula collapses it back to spacings[k].  So any element that falls
    back contributes today's behavior unchanged.
    """
    t_known = elem["max_thickness"]
    t = t_known if t_known is not None else DEFAULT_THICKNESS
    typ = elem["type"]

    if t_known is None:
        return (t / 2.0, -t / 2.0, t)

    f = elem["focal_length"]
    n = elem["refractive_index"]

    if typ in ("BCX", "DCX", "BCV", "DCV"):
        # Symmetric biconvex/biconcave: R1 = R, R2 = -R.  Thick lensmaker
        #   1/f = 2(n-1)/R - (n-1)^2 t / (n R^2)
        # Solving the quadratic for R, the physical root (one that goes to
        # the thin-lens limit R -> 2(n-1)f as t -> 0) is the '+' branch:
        #   R = (n-1) f * (1 + sqrt(1 - t/(n f)))
        # For f>0 the discriminant is in (0,1); for f<0 it is >1.  In both
        # cases the '+' root yields the geometrically correct R.
        k = n - 1.0
        disc = 1.0 - t / (n * f)
        if disc < 0:
            # Too thick to be physically realized at this (f, n).  Bail to
            # the safe fallback rather than producing a complex radius.
            return (t / 2.0, -t / 2.0, t)
        R = k * f * (1.0 + math.sqrt(disc))
        # Standard thick-lens PP offsets:
        #   delta_front = -(f (n-1) t) / (n R2)    with R2 = -R
        #   delta_rear  = -(f (n-1) t) / (n R1)    with R1 = +R
        delta_front =  (f * k * t) / (n * R)
        delta_rear  = -(f * k * t) / (n * R)
        return (delta_front, delta_rear, t)

    if typ in ("PCX", "PCV"):
        # Curved-side-front convention (SurplusShed/industry default for
        # SA minimization, and the natural orientation for a camera lens
        # used at infinity focus): R1 finite, R2 = infinity.
        #   delta_front = -(f (n-1) t) / (n * inf) = 0
        #   delta_rear  = -(f (n-1) t) / (n R1) = -t/n
        # PCX (R1 = +(n-1)f) and PCV (R1 = -(n-1)f) both reduce to the
        # same -t/n.
        return (0.0, -t / n, t)

    # PMN, NMN, ACH, or anything else: bending is underdetermined from
    # (f, n, t) alone.  Fall back to thin-lens-at-center.
    return (t / 2.0, -t / 2.0, t)


def rim_protrusion(elem, orientation):
    """How far the rear element's rim sticks out past its on-axis rear vertex
    toward the film, in mm.

    The rim is the closest glass to the film only when the film-facing surface
    is concave toward the film; then the on-axis vertex is recessed and the rim
    protrudes by that surface's sag.  When the film-facing surface is flat or
    convex, the vertex is the closest point and the protrusion is zero.

    The film-facing surface is identified from `type` + `orientation`
    (CX_FRONT = curved/convex/crown toward the object; CX_REAR = toward the
    film; None/NULL -> CX_FRONT, the curved-side-front convention).  Center and
    edge thickness only give the *sum* of the two surface sags, so for shapes
    where they cannot be separated -- asymmetric biconcave, a meniscus with its
    concave side toward the film, an achromat with a concave flint toward the
    film -- the result is a best estimate and `undetermined` is True (record
    radii for an exact figure).

    Returns (protrusion_mm, undetermined).
    """
    t_c = elem["max_thickness"]
    t_e = elem["edge_thickness"]
    typ = elem["type"]
    if t_c is None or t_e is None:
        return 0.0, False
    cx_front = (orientation != "CX_REAR")   # None/NULL -> curved-side-front
    sag_sum = max(t_e - t_c, 0.0)           # total concave sag (>=0)

    # Film-facing surface flat or convex -> vertex is the closest glass.
    if typ in ("PCX", "DCX", "BCX"):
        return 0.0, False
    if typ == "PCV":
        # Concave (curved) side faces the film only when CX_REAR.
        return (sag_sum, False) if not cx_front else (0.0, False)
    if typ == "DCV":
        # Symmetric biconcave: split the sag evenly between the two surfaces.
        return sag_sum / 2.0, False
    if typ in ("NMN", "PMN"):
        # Convex side toward the film -> vertex closest.  Otherwise the concave
        # side faces the film; its sag is not separable from (t_c, t_e).
        return (0.0, False) if not cx_front else (sag_sum / 2.0, True)
    if typ == "BCV":
        return sag_sum / 2.0, True
    if typ == "ACH":
        # Crown convex toward the film -> vertex closest.  Otherwise the flint
        # rear is typically near-flat; default to zero but flag for radii.
        return (0.0, False) if not cx_front else (0.0, True)
    return 0.0, False


def compute_stop_magnification(fl, pp_dists, offsets, stop_after, stop_spacing,
                               stop_thickness_mm):
    """Linear magnification |M| from physical-stop plane to entrance-pupil plane.

    The entrance pupil is the image of the physical aperture stop in object
    space, formed by the elements in FRONT of (object-side of) the stop.
    f-numbers are defined against the entrance pupil, so:

        D_physical_stop = |M| * D_entrance_pupil = |M| * (EFL / N)

    Method: paraxial conjugate, traced backward through the front group.
    Start with v_N = signed distance from element N's rear PP to the stop
    center; apply 1/u = 1/v - 1/f to find the object-side conjugate; carry
    that as the next image distance for element N-1, and so on.

    Returns (M_signed, u_entrance_signed, traceable).
      M_signed         per-lens magnifications multiplied (sign tracks
                       image inversion across the front group)
      u_entrance_signed object distance of the entrance pupil from element 0's
                       front PP (negative = object-side; the usual case)
      traceable        False if any front-group element has v == f exactly
                       (singular -- entrance pupil at infinity)

    For stop_after == 0 (stop in front of element 1) the front group is
    empty; the entrance pupil IS the stop, so M = 1 trivially.
    """
    if stop_after == 0:
        return (1.0, 0.0, True)

    n_elem = len(fl)
    last_idx = stop_after - 1  # 0-based index of the most-rearward front-group element
    delta_rear = [o[1] for o in offsets]

    # Image distance from element[last_idx]'s rear PP to stop center.
    # Positive = image-side (toward film), matching the existing convention.
    v = stop_spacing + stop_thickness_mm / 2.0 - delta_rear[last_idx]

    M = 1.0
    u = None
    for k in range(last_idx, -1, -1):
        denom = v - fl[k]
        if abs(denom) < 1e-9:
            return (None, None, False)
        u = v * fl[k] / denom
        if abs(u) < 1e-12:
            return (None, None, False)
        M *= v / u
        if k > 0:
            # v for the previous element measures from its rear PP to the
            # same physical point that u measures from this element's front
            # PP, separated by pp_dists[k-1] of axial distance.
            v = u + pp_dists[k - 1]

    return (M, u, True)


# Standard f-stops for stop-sizing output.
STANDARD_F_STOPS = [1.0, 1.4, 2.0, 2.8, 4.0, 5.6, 8.0, 11.0, 16.0, 22.0]


def compute_stop_calc(efl, magnification, lens_max_aperture):
    """For each standard f-stop slower than the lens's wide-open speed,
    return (f_number, entrance_pupil_diameter_mm, physical_iris_diameter_mm)."""
    rows = []
    if not (math.isfinite(efl) and efl > 0):
        return rows
    for N in STANDARD_F_STOPS:
        if N < lens_max_aperture:
            continue
        D_pupil = efl / N
        D_phys = abs(magnification) * D_pupil
        rows.append((N, D_pupil, D_phys))
    return rows


def display_stop_calc(efl, magnification, traceable, u_entrance,
                      lens_max_aperture, rows):
    """Print the stop-sizing table."""
    print()
    print("  " + "=" * 68)
    print("  Stop sizing (entrance-pupil math)")
    print("  " + "=" * 68)
    print(f"  EFL                                   = {efl:>7.2f} mm")
    print(f"  Wide-open speed (lens limit)          = f/{lens_max_aperture:.2f}")
    if not traceable:
        print("  Front-group magnification              = SINGULAR "
              "(entrance pupil at infinity; stop sized as if no front group)")
        print()
        print("  Falling back to D_physical = EFL/N (magnification = 1.0).")
    elif magnification is None:
        print("  Front-group magnification              = UNCOMPUTABLE")
        return
    else:
        print(f"  Front-group magnification |M|         = {abs(magnification):.4f}  "
              f"(D_phys / D_pupil)")
        if u_entrance is not None and math.isfinite(u_entrance):
            print(f"  Entrance pupil position (from elem 1) = "
                  f"{u_entrance:+7.2f} mm  ({'object' if u_entrance < 0 else 'image'}-side)")
    if not rows:
        print("  No standard stops slower than the lens's wide-open speed.")
        return
    print()
    print("    f-number   entrance pupil   physical iris hole")
    print("    --------   --------------   ------------------")
    for N, D_pup, D_phys in rows:
        print(f"    f/{N:<6.1f}   {D_pup:>8.2f} mm        {D_phys:>8.2f} mm")
    print("  " + "=" * 68)


def compute_field_shape(fl, stop_chief_heights, petzval_radius, half_diag_mm,
                        n_samples=11):
    """Sagittal, tangential, and medial focal-surface positions vs field
    height, third-order Seidel approximation.

    For a thin-lens system at infinity conjugates with stop at a known
    location, per-element astigmatism contribution scales as the chief
    ray height squared at that element times the element power.  Summed
    over elements and normalized by corner-field-height squared:

        A = sum_k phi_k * (chief_k_corner / h_corner)^2

    so A has units of 1/mm (matching Petzval P = 1/R_p).  The three
    focal surfaces are then parabolic in field height:

        z_S(h) = -h^2 * (P + A) / 2          sagittal
        z_T(h) = -h^2 * (P + 3A) / 2         tangential
        z_M(h) = -h^2 * (P + 2A) / 2         medial = (S + T) / 2

    Sign convention: negative z is forward of the paraxial image plane
    (toward the lens).

    Returns a dict with the per-sample rows and the system-level P, A.

    Caveats:
    - Third-order Seidel only.  Higher-order field curvature, which is
      what produces the 'flat plateau then aggressive edge curl' bowl
      shape described under "Controlling field curvature" in
      https://brianssparetime.com/posts/zenzanot-4-swirl/, is *not* captured.
      A real lens with strong shape factors may show edge curvature
      well beyond what these parabolas predict.
    - Thin-lens approximation; cross-coupling between separated elements
      is not modeled.
    - Object at infinity.
    """
    n_elem = len(fl)
    if stop_chief_heights is None or half_diag_mm <= 0:
        astig_sum = 0.0
    else:
        astig_sum = sum(
            (1.0 / fl[k]) * (stop_chief_heights[k] / half_diag_mm) ** 2
            for k in range(n_elem)
        )

    if math.isfinite(petzval_radius) and abs(petzval_radius) > 1e-12:
        P = 1.0 / petzval_radius
    else:
        P = 0.0
    A = astig_sum

    rows = []
    for i in range(n_samples):
        h = half_diag_mm * i / (n_samples - 1)
        z_s = -h * h * (P + A) / 2.0
        z_t = -h * h * (P + 3.0 * A) / 2.0
        z_m = -h * h * (P + 2.0 * A) / 2.0
        rows.append({"h": h, "z_s": z_s, "z_t": z_t, "z_m": z_m})

    return {"rows": rows, "P": P, "A": A}


def classify_field_shape(P, A):
    """Return a one-line interpretation of the (P, A) Seidel pair."""
    if abs(P) < 1e-7 and abs(A) < 1e-7:
        return "Flat field (no Petzval, no astigmatism)."
    if abs(A) < 0.05 * abs(P):
        return ("Petzval-dominated -- smooth parabolic field, sagittal "
                "and tangential nearly coincident.")
    if abs(P) < 0.05 * abs(A):
        return ("Astigmatism-dominated -- medial surface near flat but "
                "S/T diverge strongly with field height.")
    same_sign = (P > 0) == (A > 0)
    if same_sign:
        return ("Petzval and astigmatism reinforce -- both surfaces curve "
                "the same way, no flattening of the medial surface.")
    return ("Petzval and astigmatism oppose -- medial surface flatter than "
            "Petzval alone, but S and T diverge at the edge (bowl-style "
            "field signature).")


# Below this astig magnitude (mm) the astigmatic direction signal is in the
# noise of a thin-lens 3rd-order estimate; coma alone then sets the look.
SWIRL_ASTIG_FLOOR_MM = 0.05
# Dominance factor: one orienting force must exceed the other by this much to
# dictate the bokeh axis on its own; between 1/F and F the look is mixed.
SWIRL_DOMINANCE = 2.0


def swirl_orientation(swirl_astig_mm, coma_indicator_mm):
    """Classify the background-bokeh axis from the competition between
    astigmatism and coma.

    Both are radial-vs-tangential orienting forces on an out-of-focus
    highlight, expressed in the same longitudinal-mm units, so their ratio
    is aperture-independent (the f-number cancels).  Coma is ALWAYS radial
    (tie-dye).  Astigmatism is tangential (background swirl) when
    swirl_astig > 0 and radial when < 0.  The two therefore reinforce when
    astig is negative and FIGHT when astig is positive -- and when coma wins
    that fight the nominal "swirl" renders as tie-dye regardless of sign.

    Returns (label, ratio) where label is one of "background swirl",
    "tie-dye", "mixed", or "near-isotropic", and ratio is |coma|/|astig|
    (inf when astig is negligible, 0 when coma is negligible).
    """
    a = abs(swirl_astig_mm)
    c = abs(coma_indicator_mm)
    if a < SWIRL_ASTIG_FLOOR_MM:
        # No usable astigmatic direction; coma decides.
        if c < SWIRL_ASTIG_FLOOR_MM:
            return "near-isotropic", 0.0
        return "tie-dye", float("inf")
    ratio = c / a
    if swirl_astig_mm < 0:
        # Astigmatism is itself radial; coma only reinforces.  Always tie-dye.
        return "tie-dye", ratio
    # Astigmatism is tangential and fights radial coma.
    if ratio >= SWIRL_DOMINANCE:
        return "tie-dye", ratio
    if ratio <= 1.0 / SWIRL_DOMINANCE:
        return "background swirl", ratio
    return "mixed", ratio


def pp_distances(spacings, offsets):
    """Convert center-to-center spacings into PP-to-PP distances.

    For each adjacent pair (k, k+1):
        d_PP[k] = spacings[k] - t_k/2 - t_{k+1}/2
                  + delta_front[k+1] - delta_rear[k]
    """
    pp = []
    for k in range(len(spacings)):
        df_kp1, _, t_kp1 = offsets[k + 1]
        _, dr_k,  t_k   = offsets[k]
        pp.append(spacings[k] - t_k / 2.0 - t_kp1 / 2.0 + df_kp1 - dr_k)
    return pp


def propagate_optical(fl, pp_dists):
    """Sequential conjugate propagation through thin lenses at their PPs.

    Pure helper used both for the main calculation and for sensitivity /
    chromatic perturbations.  Returns (v_last, conjugates) where v_last
    is the image distance from H' of the last element, and each conjugate
    is (v_k, u_{k+1}) -- v_k from H'_k, u_{k+1} to H_{k+1}.  For the last
    element u_next is None.

    Distances must be principal-plane-to-principal-plane (see
    pp_distances()), not center-to-center.
    """
    n_elem = len(fl)
    conjugates = []
    v = fl[0]
    for k in range(n_elem):
        if k == 0:
            v = fl[0]
        else:
            u = conjugates[k - 1][0] - pp_dists[k - 1]
            denom = fl[k] + u
            if abs(denom) < 1e-12:
                sys.exit(f"Error: singularity at element {k + 1} (f + u = 0).")
            v = fl[k] * u / denom

        if k < n_elem - 1:
            u_next = v - pp_dists[k]
        else:
            u_next = None
        conjugates.append((v, u_next))

    return v, conjugates


def compute_efl(fl, pp_dists):
    """Compute EFL via paraxial ray trace through principal planes."""
    h = 1.0
    angle = 0.0
    for k in range(len(fl)):
        angle = angle - h / fl[k]
        if k < len(fl) - 1:
            h = h + pp_dists[k] * angle
    return -1.0 / angle if abs(angle) > 1e-12 else float("inf")


def compute_design(elements, spacings, stop_after=None, stop_spacing=None,
                   orientations=None, inf_offset=0.0):
    """Compute all design properties via sequential conjugate propagation.

    Returns a dict with efl, bfd, back_clearance, f_number, f_soft,
    vignetting_abruptness, petzval_radius, swirl_pct, bfd_compliant,
    bfd_violations, margins, conjugate data, BFD sensitivity, and diagnostic
    fields.

    `orientations`, when given, is a per-element list of CX_FRONT/CX_REAR/None
    surface orientations (from design_elements.orientation); only the rear
    element's value is consulted, to locate the closest glass to the film.

    `inf_offset` (mm) shifts the whole optical core as a rigid body relative to
    the film: + away from the film, - toward it.  It moves every absolute
    position (bfd, back_clearance, element/stop placement, mirror/throat
    margins, throat-overlap check) but leaves all optical metrics -- EFL,
    speed, swirl, Petzval, sharpness, sensitivities, LCA -- untouched, since
    those depend only on the prescription and inter-element spacings.  Stored
    design rows are computed with inf_offset = 0; tools apply the measured
    offset at display/export time.
    """
    n_elem = len(elements)
    fl = [e["focal_length"] for e in elements]
    diam = [e["diameter"] for e in elements]
    # mounted_od is the outer diameter of the mounted element including its
    # cell.  When NULL we fall back to the bare lens diameter.  Used only for
    # the throat clearance check, never for aperture / ray tracing.
    mounted = [
        e.get("mounted_od") if e.get("mounted_od") is not None else e["diameter"]
        for e in elements
    ]
    abbe = [e["abbe_number"] for e in elements]

    # ------------------------------------------------------------------
    # 0. Per-element principal-plane offsets and PP-to-PP distances.
    #    Spacings are user-supplied center-to-center mechanical distances
    #    (what assembly cares about).  Optical propagation requires
    #    PP-to-PP distances; principal_plane_offsets() derives them from
    #    (type, f, n, t) where it can, and falls back to thin-lens-at-
    #    center for shapes whose bending is underdetermined.
    # ------------------------------------------------------------------
    offsets = [principal_plane_offsets(e) for e in elements]
    delta_rear = [o[1] for o in offsets]
    thick      = [o[2] for o in offsets]
    pp_dists = pp_distances(spacings, offsets)

    # ------------------------------------------------------------------
    # 1. Sequential conjugate propagation  -->  BFD + intermediates.
    #    propagate_optical() returns the image distance from H' of the
    #    last element; convert to a rear-vertex-referenced BFD by adding
    #    the rear PP offset.  (For shape-fallback elements delta_rear =
    #    -t/2, so this just shifts BFD inward by half the assumed
    #    thickness, which is the geometrically correct thing.)
    # ------------------------------------------------------------------
    bfd_optical, conjugates = propagate_optical(fl, pp_dists)
    # bfd: optical rear-vertex-to-film distance.  inf_offset shifts the whole
    # core relative to the film (+ away, - toward), so it adds straight onto
    # every absolute position downstream of here.
    bfd = bfd_optical + delta_rear[-1] + inf_offset

    # back_clearance: film to the closest physical glass of the rear element.
    # Equals bfd unless the rear element's film-facing surface is concave, in
    # which case the rim protrudes past the vertex toward the film.  This is
    # the dimension that governs manufacturability; bfd is the optical
    # intermediate used to reach it.
    rear_orient = orientations[-1] if orientations else None
    rear_prot, back_clearance_undetermined = rim_protrusion(
        elements[-1], rear_orient)
    back_clearance = bfd - rear_prot

    # ------------------------------------------------------------------
    # 2. Paired paraxial trace: marginal ray (h0=1, u0=0) and an
    #    auxiliary ray (h0=0, u0=1).  These two form a fundamental basis
    #    -- any other paraxial ray is a linear combination -- which lets
    #    us reconstruct a chief ray for any candidate aperture stop
    #    position without re-tracing.  Heights are evaluated at each
    #    element's front PP; angles are stored as the post-refraction
    #    angle in the gap leading to the next element (the last entry
    #    is the angle in image space).
    # ------------------------------------------------------------------
    h_m, u_m = 1.0, 0.0
    h_a, u_a = 0.0, 1.0
    ray_heights = []  # marginal ray heights at each element
    aux_heights = []  # auxiliary ray heights at each element
    u_m_after = []    # marginal angle after refraction by each element
    u_a_after = []    # auxiliary angle after refraction by each element
    for k in range(n_elem):
        ray_heights.append(h_m)
        aux_heights.append(h_a)
        u_m = u_m - h_m / fl[k]
        u_a = u_a - h_a / fl[k]
        u_m_after.append(u_m)
        u_a_after.append(u_a)
        if k < n_elem - 1:
            h_m = h_m + pp_dists[k] * u_m
            h_a = h_a + pp_dists[k] * u_a

    efl = -1.0 / u_m_after[-1] if abs(u_m_after[-1]) > 1e-12 else float("inf")

    # ------------------------------------------------------------------
    # 3. F-number, f_soft, vignetting abruptness
    # ------------------------------------------------------------------
    ratios = []
    for k in range(n_elem):
        rh = abs(ray_heights[k])
        if rh < 1e-12:
            ratios.append(float("inf"))
        else:
            ratios.append((diam[k] / 2.0) / rh)

    sorted_ratios = sorted(ratios)
    min_ratio = sorted_ratios[0]
    second_min_ratio = sorted_ratios[1] if len(sorted_ratios) > 1 else min_ratio
    limiting_idx = ratios.index(min_ratio)

    entrance_pupil_diam = 2.0 * min_ratio
    f_number = abs(efl) / entrance_pupil_diam if entrance_pupil_diam > 0 else float("inf")

    entrance_pupil_soft = 2.0 * second_min_ratio
    f_soft = abs(efl) / entrance_pupil_soft if entrance_pupil_soft > 0 else float("inf")

    vignetting_abruptness = min_ratio / second_min_ratio if second_min_ratio > 1e-12 else 1.0

    # ------------------------------------------------------------------
    # 4. Petzval radius (signed).  Cemented achromats are split into their
    #    crown/flint components -- see petzval_contribution().
    # ------------------------------------------------------------------
    petzval_sum = sum(petzval_contribution(elements[k]) for k in range(n_elem))
    if abs(petzval_sum) < 1e-12:
        petzval_radius = float("inf")
    else:
        petzval_radius = 1.0 / petzval_sum  # signed: + = inward, - = outward

    # ------------------------------------------------------------------
    # 5. Swirl vs Dallmeyer (%).  Swirl requires the field to curve inward
    #    (toward the lens), i.e. positive Petzval radius.  A flat or
    #    backward-curving field produces no swirl regardless of magnitude.
    # ------------------------------------------------------------------
    if math.isfinite(petzval_radius) and petzval_radius > 1e-12:
        swirl_pct = (DALLMEYER_PETZVAL_RADIUS / petzval_radius) * 100.0
    else:
        swirl_pct = 0.0

    # ------------------------------------------------------------------
    # 5b. Sharp-center radius
    #
    # Two on-axis-degrading terms are modeled:
    #
    #   (a) Field curvature.  A flat film intersects the curved Petzval
    #       surface at radius h with longitudinal defocus h^2/(2|R|),
    #       producing a blur diameter (CoC):
    #           CoC_field(h) = h^2 / (2 |R| N)
    #
    #   (b) Third-order spherical, summed across elements.  Per element
    #       k with marginal ray height y_k (real units), Smith's thin-
    #       lens formula gives a longitudinal spherical aberration of
    #           LSA_k = (y_k^2 / (8 f_k)) * G(n_k, q_k)
    #       Signed contributions sum (so a positive + negative pair can
    #       partially cancel, as in an achromat).  The system transverse
    #       blur diameter at the paraxial focus is then
    #           CoC_sph = |sum(LSA_k)| / N
    #       This is approximate: each element is treated in isolation,
    #       so cross-coupling between separated elements is not modeled.
    #       Adequate for order-of-magnitude ranking.
    #
    # The two terms are independent first-order contributions, so they
    # combine in quadrature.  Sharp-center radius is the largest h for
    # which the combined CoC is still within the sharpness threshold:
    #
    #     CoC_total(h)^2 = CoC_sph^2 + (h^2 / (2 |R| N))^2
    #     solve CoC_total(h_sharp) = THRESHOLD for h_sharp
    #
    # If the on-axis spherical term alone already exceeds the threshold,
    # the lens is never sharp at the chosen f-number and h_sharp = 0.
    # ------------------------------------------------------------------
    # y_real / lsa_per_elem are kept as lists (not just their sum) so the
    # coma indicator below can reuse the per-element spherical via the
    # thin-lens stop-shift relation S_II ~= (chief/marginal) * S_I.
    y_real = None
    lsa_per_elem = None
    if f_number > 0 and math.isfinite(f_number):
        # Real marginal ray heights: ray_heights are normalized to h0=1,
        # and the actual entrance pupil radius (marginal ray height at
        # the limiting element) is min_ratio in mm.
        y_real = [ray_heights[k] * min_ratio for k in range(n_elem)]
        lsa_per_elem = [
            (y_real[k] ** 2 / (8.0 * fl[k])) * spherical_lsa_factor(elements[k])
            for k in range(n_elem)
        ]
        lsa_total = sum(lsa_per_elem)
        axial_spherical_coc_mm = abs(lsa_total) / f_number
    else:
        axial_spherical_coc_mm = 0.0

    if abs(petzval_radius) > 1e-12 and f_number > 0 and math.isfinite(f_number):
        corner_field_coc_only = (FRAME_HALF_DIAG_MM ** 2) / (
            2 * abs(petzval_radius) * f_number
        )
        # Field-CoC at the corner combined with axial spherical:
        corner_field_coc_mm = math.sqrt(
            corner_field_coc_only ** 2 + axial_spherical_coc_mm ** 2
        )
        # Solve for sharp radius:
        #   threshold^2 = sph^2 + (h^2/(2RN))^2
        residual_sq = SHARP_COC_THRESHOLD_MM ** 2 - axial_spherical_coc_mm ** 2
        if residual_sq <= 0:
            sharp_radius_mm = 0.0
        else:
            sharp_radius_mm = math.sqrt(
                2 * abs(petzval_radius) * f_number * math.sqrt(residual_sq)
            )
    else:
        sharp_radius_mm = float("inf")
        corner_field_coc_mm = axial_spherical_coc_mm
    sharp_radius_pct = min(sharp_radius_mm / FRAME_HALF_DIAG_MM * 100.0, 100.0)

    # ------------------------------------------------------------------
    # 6. Clearance compliance check
    #    The Bronica S2a body bore narrows as the lens recedes behind the
    #    flange.  Each element's deepest face has a position z (from the
    #    flange, +z toward the subject); the diameter allowed at that depth
    #    steps down through CLEARANCE_DEPTH_FLOOR_MM, past which nothing
    #    fits.  Lens thickness counts: the deepest face, not the center,
    #    sets z, so an element centered in a wide zone can still poke its
    #    rear face into a narrower one.  Compliance is binary.
    # ------------------------------------------------------------------
    # throat_margin is informational: rear-most glass position relative to
    # the flange (back_clearance is the closest glass to the film).
    throat_margin = back_clearance - THROAT_FAR_MM

    bfd_compliant = True
    bfd_violations = []

    # dist_to_focal[k] is the distance from the *center* of element k to
    # the film plane.  bfd is rear-vertex-to-film, so the center of the
    # last element is bfd + t_last/2 from the film.  For shape-fallback
    # elements this is identical to the prior seed (because bfd shifted
    # inward by exactly t_last/2).
    dist_to_focal = [0.0] * n_elem
    dist_to_focal[n_elem - 1] = bfd + thick[n_elem - 1] / 2.0
    for k in range(n_elem - 2, -1, -1):
        dist_to_focal[k] = dist_to_focal[k + 1] + spacings[k]

    for k in range(n_elem):
        # Each element is a bounding cylinder of axial height max_thickness
        # and diameter mounted_od (or bare diameter).  This is conservative
        # -- the glass surface lies inside the cylinder regardless of shape.
        # The deepest face is the one nearest the film; its z from the
        # flange sets which diameter zone applies.
        half_t = thick[k] / 2.0
        rear_surface = dist_to_focal[k] - half_t
        z_deep = rear_surface - THROAT_FAR_MM
        lim = clearance_diam_limit(z_deep)
        mod_note = (
            f"mounted OD {mounted[k]:.1f}mm"
            if elements[k].get("mounted_od") is not None
            else f"diameter {mounted[k]:.1f}mm"
        )
        if lim is None:
            bfd_compliant = False
            bfd_violations.append(
                f"Element {k + 1} ({elements[k]['code']}) deepest face at "
                f"z={z_deep:.1f}mm is past the {CLEARANCE_DEPTH_FLOOR_MM:.0f}mm "
                f"depth floor"
            )
        elif mounted[k] > lim:
            bfd_compliant = False
            bfd_violations.append(
                f"Element {k + 1} ({elements[k]['code']}) deepest face at "
                f"z={z_deep:.1f}mm allows {lim:.2f}mm, {mod_note} too large"
            )

    # ------------------------------------------------------------------
    # 7. Aperture stop placement.
    #
    # Stop position is found by searching every legal axial slot for the
    # one that gives the most symmetric off-axis bundle -- the position
    # where a chief ray from the corner of the frame clears every
    # element rim with the most margin.  This is the geometric
    # "vignetting symmetry" criterion: lower score = more headroom
    # before mechanical vignetting kicks in.  It does NOT optimize for
    # coma or distortion (those would need third-order Seidel sums).
    #
    # The stop is treated as a 2mm-thick disc.  Each side of the stop
    # must clear the adjacent element by at least the same minimum gap
    # used elsewhere (2mm if the adjacent element's thickness is known,
    # 5mm otherwise).  Candidates are enumerated at 0.5mm steps in
    #   - the air space in front of element 1, capped at 30mm forward
    #   - every inter-element air gap that has room for stop + min gaps
    # The stop does NOT change the wide-open f-number; the existing
    # marginal-ray calc already gives the speed of the lens unstopped.
    #
    # User-supplied stop_after / stop_spacing override the search; the
    # score is still computed for diagnostic purposes.
    # ------------------------------------------------------------------
    theta_corner = FRAME_HALF_DIAG_MM / abs(efl) if math.isfinite(efl) and efl != 0 else 0.0

    def _min_gap_for(elem_idx):
        return 2.0 if elements[elem_idx].get("max_thickness") is not None else 5.0

    def _air_gap(j):
        return spacings[j] - thick[j] / 2.0 - thick[j + 1] / 2.0

    # Reject physically impossible geometry before scoring anything.  Two
    # elements may never overlap, and a pair whose thicknesses are both known
    # must keep the 2mm minimum air gap (DESIGN-REQS).  Unknown-thickness
    # elements already carry the 10mm default in thick[], so only their hard
    # overlap is enforced -- the 2mm floor applies "if max_thickness is known
    # for both".  Until now only the stop's own clearances were validated, so
    # a non-stop element overlap passed through as a cosmetic warning.
    for _j in range(len(spacings)):
        _air = _air_gap(_j)
        _both_known = (elements[_j].get("max_thickness") is not None
                       and elements[_j + 1].get("max_thickness") is not None)
        if _air <= 0.0:
            sys.exit(
                f"Error: elements {_j + 1} ({elements[_j]['code']}) and "
                f"{_j + 2} ({elements[_j + 1]['code']}) overlap: air gap "
                f"{_air:.2f}mm <= 0 at spacing {spacings[_j]:.2f}mm."
            )
        if _both_known and _air < 2.0:
            sys.exit(
                f"Error: elements {_j + 1} ({elements[_j]['code']}) and "
                f"{_j + 2} ({elements[_j + 1]['code']}) gap {_air:.2f}mm "
                f"below 2mm minimum at spacing {spacings[_j]:.2f}mm."
            )

    def _chief_heights(s_after, s_spacing):
        """Reconstruct chief ray heights at each element for a stop at
        (s_after, s_spacing).  Returns None if the configuration is
        degenerate (chief ray cannot be defined)."""
        if s_after == 0:
            # Stop in front of element 1.  s_spacing measures the air
            # gap from the stop's rear face to elem 1's front vertex.
            # The stop's center sits (s_spacing + STOP_THICKNESS/2)
            # ahead of the front vertex; convert to a distance back to
            # element 1's front PP for chief-ray reconstruction.
            d_to_front_pp = (s_spacing + STOP_THICKNESS_MM / 2.0
                             + offsets[0][0])
            # Marginal ray enters at h=1, u=0; aux ray at h=0, u=1.
            h_m_S = 1.0
            h_a_S = -d_to_front_pp
        else:
            i = s_after - 1
            # PP-relative distance from elem i's rear PP to stop center.
            # delta_rear is signed; subtracting it shifts by the offset
            # of the rear PP from the rear vertex.
            d_from_rear_pp = (s_spacing + STOP_THICKNESS_MM / 2.0
                              - delta_rear[i])
            h_m_S = ray_heights[i] + d_from_rear_pp * u_m_after[i]
            h_a_S = aux_heights[i] + d_from_rear_pp * u_a_after[i]
        if abs(h_m_S) < 1e-12:
            return None
        # Chief ray = (M * marginal + 1 * aux) * theta_corner, where
        # M is the multiplier that makes the chief cross axis at the
        # stop.  At each element k:
        #     h_chief[k] = theta_corner * (M * h_m[k] + h_a[k])
        M = -h_a_S / h_m_S
        return [
            theta_corner * (M * ray_heights[k] + aux_heights[k])
            for k in range(n_elem)
        ]

    def _vignetting_score(chief):
        if chief is None:
            return float("inf")
        return max(
            abs(chief[k]) / (diam[k] / 2.0)
            for k in range(n_elem)
        )

    def _enumerate_candidates():
        """Yield (stop_after, stop_spacing) tuples for every legal slot."""
        # Front-of-element-1 region.
        front_min = _min_gap_for(0)
        front_max = STOP_FRONT_CAP_MM - STOP_THICKNESS_MM
        if front_max >= front_min:
            s = front_min
            while s <= front_max + 1e-9:
                yield (0, s)
                s += STOP_SEARCH_STEP_MM
        # Each inter-element gap.
        for j in range(len(spacings)):
            air = _air_gap(j)
            min_front = _min_gap_for(j)
            min_rear = _min_gap_for(j + 1)
            s_lo = min_front
            s_hi = air - STOP_THICKNESS_MM - min_rear
            if s_hi < s_lo:
                continue
            s = s_lo
            while s <= s_hi + 1e-9:
                yield (j + 1, s)
                s += STOP_SEARCH_STEP_MM

    def _distortion_indicator(chief):
        """Qualitative distortion sign indicator.

        Computes the leading-order term of the per-thin-lens Seidel S_V
        at infinity: sum_k (ybar_k^3 / f_k^3), where ybar_k is the chief
        ray height at element k.  This captures the dominant cubic-in-
        chief-ray-height term of the full S_V polynomial; the higher-
        order terms involving marginal heights and the Lagrange
        invariant are dropped.

        Sign convention follows Welford ('positive S_V = pincushion'):

            indicator > 0  ->  pincushion
            indicator < 0  ->  barrel
            indicator ~ 0  ->  neutral

        Verified analytically for:
            - single positive lens, stop in front  -> barrel  (box camera)
            - single positive lens, stop behind    -> pincushion
            - symmetric design with central stop   -> neutral

        Known failure modes: designs where the dropped higher-order
        terms dominate the cubic.  This typically happens when the stop
        is far from every element (so chief ray heights are small at
        all elements but marginal heights aren't), or in heavily
        asymmetric layouts.  The MAGNITUDE of this indicator is NOT a
        physical quantity -- only the SIGN should be trusted, and even
        the sign should be treated as directional guidance rather than
        ground truth.  Full Seidel S_V is not implemented.

        Sign-convention note: our auxiliary basis ray has u_0 = +1,
        which corresponds to a chief ray for an object BELOW the axis
        (Welford's convention is object above axis).  All chief heights
        are therefore negated relative to Welford's, and so is the
        cubic.  We compensate by negating the final sum -- with that
        flip, positive = pincushion, negative = barrel, matching the
        Welford convention used everywhere else when reasoning about
        S_V.  Verified analytically against:
            - box camera (single positive lens, stop in front): barrel
            - stop behind single positive lens: pincushion
            - symmetric design with central stop: neutral
        """
        if chief is None:
            return 0.0
        return -sum(
            (chief[k] ** 3) / (fl[k] ** 3)
            for k in range(n_elem)
        )

    if stop_after is None:
        # Auto-search.
        best = None  # (score, after, spacing, chief)
        for cand_after, cand_spacing in _enumerate_candidates():
            chief = _chief_heights(cand_after, cand_spacing)
            score = _vignetting_score(chief)
            if best is None or score < best[0]:
                best = (score, cand_after, cand_spacing, chief)
        if best is None:
            # No legal slot anywhere -- shouldn't happen for any sane
            # design, but degrade to "stop just behind last element".
            stop_after = n_elem
            stop_spacing = _min_gap_for(n_elem - 1)
            stop_score = float("inf")
            stop_chief_heights = _chief_heights(stop_after, stop_spacing)
            stop_source = "fallback"
        else:
            stop_score, stop_after, stop_spacing, stop_chief_heights = best
            stop_source = "auto"
    else:
        # User-supplied position.  Validate physical fit: the stop disc
        # plus its required clearances must fit inside the chosen gap.
        if stop_spacing is None:
            stop_spacing = _min_gap_for(max(stop_after - 1, 0))
        if stop_after == 0:
            # Front of element 1: no constraint from existing spacings;
            # stop sits in the open air before the first element.
            pass
        else:
            j = stop_after - 1  # index of the element the stop sits behind
            air = _air_gap(j)
            min_rear = _min_gap_for(j + 1)
            min_front = _min_gap_for(j)
            front_gap = stop_spacing
            rear_gap = air - STOP_THICKNESS_MM - stop_spacing
            problems = []
            if front_gap < min_front:
                problems.append(
                    f"front gap {front_gap:.2f}mm below {min_front:.0f}mm minimum"
                )
            if rear_gap < min_rear:
                problems.append(
                    f"rear gap {rear_gap:.2f}mm below {min_rear:.0f}mm minimum "
                    f"(air gap {air:.2f}mm only fits stop_spacing up to "
                    f"{air - STOP_THICKNESS_MM - min_rear:.2f}mm)"
                )
            if problems:
                sys.exit(
                    f"Error: stop_after={stop_after} stop_spacing={stop_spacing}mm "
                    f"does not fit physically: " + "; ".join(problems)
                )
        stop_chief_heights = _chief_heights(stop_after, stop_spacing)
        stop_score = _vignetting_score(stop_chief_heights)
        stop_source = "user"

    # Qualitative distortion sign at the chosen stop position.  See
    # _distortion_indicator() for the rule and its limitations.
    distortion_indicator = _distortion_indicator(stop_chief_heights)
    # Neutral threshold: empirically calibrated against the saved
    # Bronica-scale designs (chief heights ~5mm at f~100-200mm gives
    # per-element contributions ~1e-4).  Indicators below 1e-4 in
    # absolute value are reported as neutral, both because the cubic
    # approximation gets dominated by dropped higher-order terms in
    # that regime and because the corresponding physical distortion is
    # too small to be visually meaningful anyway.
    if abs(distortion_indicator) < 1e-4:
        distortion_label = "neutral"
    elif distortion_indicator > 0:
        distortion_label = "pincushion"
    else:
        distortion_label = "barrel"

    # ------------------------------------------------------------------
    # 8. Element gap check
    #    gap = center-to-center spacing - half-thickness of each element.
    #    Default thickness (10mm) is used when an element's max_thickness
    #    is unknown.  Acceptable gap minimum is 2mm only when both
    #    adjacent elements have a known max_thickness; otherwise 5mm.
    #
    #    When the auto-located stop sits inside one of these gaps, the
    #    gap is split into two sub-gaps (front of stop / rear of stop)
    #    and both are reported.  The split gap entry carries
    #    `stop_in_gap=True`; display code knows how to render it.
    # ------------------------------------------------------------------
    element_gaps = []
    for j in range(len(spacings)):
        gap = _air_gap(j)
        both_thickness_known = (
            elements[j].get("max_thickness") is not None
            and elements[j + 1].get("max_thickness") is not None
        )
        entry = {
            "gap_mm": gap,
            "both_thickness_known": both_thickness_known,
            "stop_in_gap": False,
        }
        if stop_after == j + 1:
            entry["stop_in_gap"] = True
            entry["stop_spacing"] = stop_spacing
            entry["gap_front_mm"] = stop_spacing
            entry["gap_rear_mm"] = gap - STOP_THICKNESS_MM - stop_spacing
        element_gaps.append(entry)

    # ------------------------------------------------------------------
    # 9. BFD and EFL spacing sensitivity (central finite difference).
    #    Per-spacing dBFD/dS and dEFL/dS values matter for manufacturing
    #    tolerance: a spacing with sensitivity 2.0 mm/mm means a 0.5mm
    #    assembly error shifts BFD by 1mm.  We also report the largest
    #    absolute BFD sensitivity as a single "tightest tolerance" metric.
    # ------------------------------------------------------------------
    delta = 0.1
    bfd_sensitivity = []
    efl_sensitivity = []
    for j in range(len(spacings)):
        sp_plus = list(spacings)
        sp_plus[j] += delta
        sp_minus = list(spacings)
        sp_minus[j] -= delta

        # Offsets are functions of (f, n, t) only; perturbing a spacing
        # leaves them unchanged.  Just rebuild pp_dists.
        pp_plus  = pp_distances(sp_plus,  offsets)
        pp_minus = pp_distances(sp_minus, offsets)

        bfd_plus_opt,  _ = propagate_optical(fl, pp_plus)
        bfd_minus_opt, _ = propagate_optical(fl, pp_minus)
        # The constant delta_rear[-1] cancels in the difference.
        bfd_sensitivity.append((bfd_plus_opt - bfd_minus_opt) / (2.0 * delta))

        efl_plus  = compute_efl(fl, pp_plus)
        efl_minus = compute_efl(fl, pp_minus)
        efl_sensitivity.append((efl_plus - efl_minus) / (2.0 * delta))

    max_bc_sensitivity = (max(abs(s) for s in bfd_sensitivity)
                           if bfd_sensitivity else 0.0)

    # ------------------------------------------------------------------
    # 10. Longitudinal chromatic aberration (F-to-C focal shift)
    #    Perturb each element's FL for F-line and C-line using Abbe number,
    #    then propagate at each wavelength and take the BFD difference.
    # ------------------------------------------------------------------
    fl_F = [fl[k] / (1.0 + 1.0 / (2.0 * abbe[k])) for k in range(n_elem)]
    fl_C = [fl[k] / (1.0 - 1.0 / (2.0 * abbe[k])) for k in range(n_elem)]
    # PP offsets depend on n, but the F-to-C n-shift is ~0.005, which
    # moves PPs by sub-mm -- well below the other approximations here.
    # Hold pp_dists fixed; the constant delta_rear[-1] cancels in the
    # difference, so we don't bother adding it back.
    bfd_F_opt, _ = propagate_optical(fl_F, pp_dists)
    bfd_C_opt, _ = propagate_optical(fl_C, pp_dists)
    lca = bfd_C_opt - bfd_F_opt  # positive = C focuses further back than F

    # ------------------------------------------------------------------
    # 11. Total physical length: front vertex of element 1 to rear vertex
    #     of element N.  Inter-element spacings only would understate the
    #     mechanical length by half a thickness on each end.
    # ------------------------------------------------------------------
    # Mechanical length includes the stop disc (always 2mm).  When the
    # stop is in front of element 1, it adds stop_spacing + thickness
    # to the front; when in an inter-element gap it occupies space that
    # was already counted in `spacings`, so only the stop's own
    # thickness is new -- the air on either side is part of the
    # existing gap.
    total_length = sum(spacings) + thick[0] / 2.0 + thick[-1] / 2.0
    if stop_after == 0:
        total_length += stop_spacing + STOP_THICKNESS_MM
    else:
        total_length += STOP_THICKNESS_MM

    # ------------------------------------------------------------------
    # Swirl DIRECTION at the corner (signed, mm).  See "Astigmatism sets the
    # direction" and "Coma" in https://brianssparetime.com/posts/zenzanot-4-swirl/
    #
    # The raw third-order quantity is the astigmatic separation
    #   astig_gap = sum_k chief_k^2 / fl_k = z_sag - z_tan   (at the corner)
    # Background-bokeh direction is set by which astigmatic focus the flat
    # film ends up nearer.  For a background point both foci shift toward the
    # lens, so the tangential focus is nearer the film exactly when it lies
    # FARTHER from the lens than the sagittal, i.e. when z_tan > z_sag, i.e.
    # when astig_gap < 0.  Film nearer the tangential focus -> the radial
    # blur collapses and the kernel elongates tangentially -> classic
    # background SWIRL.  The opposite sign is radial smear (tie-dye).
    #
    # We therefore report swirl_astig = -(astig_gap) = z_tan - z_sag, so that
    #   swirl_astig > 0  ->  background swirl (tangential arcs)
    #   swirl_astig < 0  ->  background tie-dye (radial smear)
    # The orientation INVERTS in the foreground.  This metric captures only
    # the astigmatic contribution; cat's-eye vignetting (always tangential,
    # unmodeled here) often dominates the photographed look.  Third-order
    # Seidel; see compute_field_shape() caveats.
    # ------------------------------------------------------------------
    fl_list = [e["focal_length"] for e in elements]
    if stop_chief_heights is not None:
        astig_gap_corner_mm = sum(
            (stop_chief_heights[k] ** 2) / fl_list[k]
            for k in range(n_elem)
        )
    else:
        astig_gap_corner_mm = 0.0
    swirl_astig_mm = -astig_gap_corner_mm

    # ------------------------------------------------------------------
    # Plateau radius from the MEDIAL surface (mm).  The bowl's flat-centre
    # plateau is the region where the medial-surface defocus blur stays
    # under the sharpness threshold -- the quantity DESIGN-REQS prio-3
    # asks for.  This is distinct from sharp_radius_mm, which uses the
    # Petzval surface plus axial spherical; when astigmatism opposes
    # Petzval the medial surface is flatter, so the true plateau is wider.
    #
    #   z_med(h) = -h^2 (P + 2A) / 2 ,   blur_med(h) = |z_med(h)| / N
    # with P = 1/R_p and A = astig_gap / h_corner^2.  Solving
    # blur_med = THRESHOLD gives the closed form below; a near-flat medial
    # surface (|P+2A| ~ 0) yields a plateau capped at the frame edge.
    # ------------------------------------------------------------------
    A_coeff = astig_gap_corner_mm / (FRAME_HALF_DIAG_MM ** 2)
    P_coeff = (1.0 / petzval_radius
               if math.isfinite(petzval_radius) and abs(petzval_radius) > 1e-12
               else 0.0)
    medial_coeff = P_coeff + 2.0 * A_coeff
    if f_number > 0 and math.isfinite(f_number):
        if abs(medial_coeff) < 1e-12:
            plateau_radius_mm = FRAME_HALF_DIAG_MM
        else:
            plateau_radius_mm = min(
                math.sqrt(2.0 * SHARP_COC_THRESHOLD_MM * f_number
                          / abs(medial_coeff)),
                FRAME_HALF_DIAG_MM,
            )
    else:
        plateau_radius_mm = float("nan")
    plateau_radius_pct = (plateau_radius_mm / FRAME_HALF_DIAG_MM * 100.0
                          if math.isfinite(plateau_radius_mm) else float("nan"))

    # ------------------------------------------------------------------
    # Coma indicator (order-of-magnitude, mm).  Part 4 of the blog series flags coma as a
    # RADIAL contaminant that fights classic tangential swirl and rides
    # along with concentrated power.  Per the Seidel stop-shift relation
    # S_II = S_II0 + E*S_I, the dominant term for a stop remote from each
    # element is E_k*S_I,k with E_k ~= chief_k / marginal_k.  We sum that
    # using the per-element spherical (lsa_per_elem) already computed:
    #   coma ~ sum_k (chief_k / y_real_k) * LSA_k
    # The intrinsic (stop-at-element) coma term is omitted, so this is an
    # order-of-magnitude indicator like _distortion_indicator: trust the
    # magnitude only coarsely, and the sign as directional guidance.
    # ------------------------------------------------------------------
    if (stop_chief_heights is not None and lsa_per_elem is not None
            and y_real is not None):
        coma_per_element = [
            ((stop_chief_heights[k] / y_real[k]) * lsa_per_elem[k]
             if abs(y_real[k]) > 1e-12 else 0.0)
            for k in range(n_elem)
        ]
        coma_indicator_mm = sum(coma_per_element)
    else:
        coma_per_element = [0.0] * n_elem
        coma_indicator_mm = 0.0

    # ------------------------------------------------------------------
    # Concentrated-negative-power flag (qualitative, unscored).  The swirl decomposition's
    # sharp edge-curl lever is negative power CONCENTRATED in one strong
    # element, which third-order Seidel cannot see.  Report the topology
    # (one dominant negative vs spread) plus the strongest negative's |f|,
    # which carries the strength the topology alone misses.
    # ------------------------------------------------------------------
    neg_powers = [abs(1.0 / fl[k]) for k in range(n_elem) if fl[k] < 0]
    if not neg_powers:
        conc_neg_label = "no negative element"
    else:
        frac = max(neg_powers) / sum(neg_powers)
        strongest_neg_f = min(abs(fl[k]) for k in range(n_elem) if fl[k] < 0)
        topo = "concentrated" if frac > 0.66 else "spread"
        conc_neg_label = (f"{topo} ({len(neg_powers)} neg, "
                          f"|f|min={strongest_neg_f:.0f}mm)")

    return {
        "efl": efl,
        "bfd": bfd,
        "back_clearance": back_clearance,
        "back_clearance_undetermined": back_clearance_undetermined,
        "inf_offset": inf_offset,
        "f_number": f_number,
        "f_soft": f_soft,
        "vignetting_abruptness": vignetting_abruptness,
        "petzval_radius": petzval_radius,
        "swirl_pct": swirl_pct,
        "astig_gap_corner_mm": astig_gap_corner_mm,
        "swirl_astig_mm": swirl_astig_mm,
        "plateau_radius_mm": plateau_radius_mm,
        "plateau_radius_pct": plateau_radius_pct,
        "coma_indicator_mm": coma_indicator_mm,
        "coma_per_element": coma_per_element,
        "swirl_orientation": swirl_orientation(swirl_astig_mm,
                                               coma_indicator_mm),
        "conc_neg_label": conc_neg_label,
        "sharp_radius_mm": sharp_radius_mm,
        "sharp_radius_pct": sharp_radius_pct,
        "corner_field_coc_mm": corner_field_coc_mm,
        "axial_spherical_coc_mm": axial_spherical_coc_mm,
        "bfd_compliant": bfd_compliant,
        "bfd_violations": bfd_violations,
        "throat_margin": throat_margin,
        "limiting_idx": limiting_idx,
        "ray_heights": ray_heights,
        "conjugates": conjugates,
        "dist_to_focal": dist_to_focal,
        "thickness": thick,
        "element_gaps": element_gaps,
        "bfd_sensitivity": bfd_sensitivity,
        "efl_sensitivity": efl_sensitivity,
        "max_bc_sensitivity": max_bc_sensitivity,
        "lca": lca,
        "total_length": total_length,
        "stop_after": stop_after,
        "stop_spacing": stop_spacing,
        "stop_score": stop_score,
        "stop_chief_heights": stop_chief_heights,
        "stop_source": stop_source,
        "distortion_indicator": distortion_indicator,
        "distortion_label": distortion_label,
        "offsets": offsets,
        "pp_dists": pp_dists,
    }


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def display_field_shape(field_shape, f_number):
    """Print the field-shape table, ASCII plot, and one-line interpretation."""
    rows = field_shape["rows"]
    P = field_shape["P"]
    A = field_shape["A"]

    print()
    print("  " + "=" * 68)
    print("  Field shape (3rd-order Seidel, see 'Field curvature' in https://brianssparetime.com/posts/zenzanot-4-swirl/)")
    print("  " + "=" * 68)
    print(f"  Petzval coeff   P = {P:+.5e} 1/mm   "
          f"(R_p = {1.0/P if abs(P) > 1e-12 else float('inf'):>+8.1f} mm)")
    print(f"  Astig.  coeff   A = {A:+.5e} 1/mm")
    print()
    print("  Defocus from paraxial image plane (mm; - = forward of focus):")
    print()
    print("    h (mm)    z_sag    z_tan    z_med    blur_med")
    print("    ------   -------  -------  -------   --------")
    for row in rows:
        h = row["h"]
        if f_number > 0 and math.isfinite(f_number):
            blur = abs(row["z_m"]) / f_number
            blur_str = f"{blur:6.3f}"
        else:
            blur_str = "  -   "
        print(f"    {h:6.2f}  {row['z_s']:+7.3f}  {row['z_t']:+7.3f}  "
              f"{row['z_m']:+7.3f}   {blur_str}")

    # ASCII plot of S, T, M curves vs h.
    z_vals = [v for r in rows for v in (r["z_s"], r["z_t"], r["z_m"])]
    z_vals.append(0.0)
    z_min, z_max = min(z_vals), max(z_vals)
    span = z_max - z_min
    if span < 1e-9:
        print("\n  (all surfaces coincide at the paraxial focus)")
    else:
        h_lines, w_cols = 11, 52
        grid = [[" "] * w_cols for _ in range(h_lines)]
        h_max = rows[-1]["h"] if rows[-1]["h"] > 0 else 1.0

        def to_row(z):
            return int(round((z_max - z) / span * (h_lines - 1)))

        def to_col(h):
            return int(round(h / h_max * (w_cols - 1)))

        for row in rows:
            col = to_col(row["h"])
            for label, z in (("S", row["z_s"]),
                             ("T", row["z_t"]),
                             ("M", row["z_m"])):
                r = to_row(z)
                if 0 <= r < h_lines and 0 <= col < w_cols:
                    cur = grid[r][col]
                    if cur == " ":
                        grid[r][col] = label
                    elif cur != label:
                        grid[r][col] = "*"

        # Annotate axis line (z = 0) if it falls inside the plot.
        if z_min <= 0 <= z_max:
            r0 = to_row(0.0)
            for c in range(w_cols):
                if grid[r0][c] == " ":
                    grid[r0][c] = "-"

        print()
        print("  Focal-surface profiles (S=sagittal, T=tangential, M=medial,")
        print("                          *=overlap, -=paraxial focus):")
        print()
        top_label = f"{z_max:+7.3f}"
        bot_label = f"{z_min:+7.3f}"
        lw = max(len(top_label), len(bot_label))
        pad = " " * lw
        print(f"  {top_label} +" + "-" * w_cols + "+")
        for r in range(h_lines):
            print(f"  {pad} |" + "".join(grid[r]) + "|")
        print(f"  {bot_label} +" + "-" * w_cols + "+")
        print(f"  {pad}  0" + " " * (w_cols - 8) +
              f"{rows[-1]['h']:>5.1f} mm  h")

    print()
    print(f"  Interpretation: {classify_field_shape(P, A)}")
    print("  " + "=" * 68)


def display_results(name, elements, spacings, results, notes=None):
    """Print the evaluation to stdout."""
    print(f"\n{'=' * 72}")
    print(f"  Design: {name}")
    print(f"{'=' * 72}")

    inf_offset = results.get("inf_offset") or 0.0
    if abs(inf_offset) > 1e-9:
        direction = "away from" if inf_offset > 0 else "toward"
        print(f"  inf_offset = {inf_offset:+.1f}mm applied "
              f"(core shifted {direction} the film)")

    # -- Element table with conjugate data --
    print("\n  Elements:")

    def _gap_warn(gap_mm, both_known):
        min_acceptable = 2.0 if both_known else 5.0
        if gap_mm <= 0:
            return " << OVERLAP"
        if gap_mm < min_acceptable:
            return f" << BELOW {min_acceptable:.0f}mm MIN"
        if gap_mm < 5.0:
            return " << TIGHT (preferred 5mm)"
        return ""

    if results["stop_after"] == 0:
        ss = results["stop_spacing"]
        print(f"    *  STOP ({STOP_THICKNESS_MM:.0f}mm)  {ss:.1f}mm in front of element 1")
        print(f"       --- (gap {ss:.1f}mm) ---")

    for k, elem in enumerate(elements):
        flags = []
        if results["limiting_idx"] == k:
            flags.append("limiting aperture")
        flag_str = f"  [{', '.join(flags)}]" if flags else ""

        v_k, u_next = results["conjugates"][k]
        img_str = f"img_dist={v_k:>7.1f}mm"
        t_k = results["thickness"][k]
        half_t = t_k / 2.0
        z_k = results["dist_to_focal"][k]
        envelope = f"[{z_k - half_t:.1f}..{z_k + half_t:.1f}]"
        tmax_val = elem["max_thickness"]
        tedge_val = elem["edge_thickness"]
        tmax_str = f"{tmax_val:>4.1f}" if tmax_val is not None else "   ?"
        tedge_str = f"{tedge_val:>4.1f}" if tedge_val is not None else "   ?"
        mod_val = elem.get("mounted_od")
        if mod_val is not None and abs(mod_val - elem["diameter"]) > 0.05:
            mod_str = f"  mOD={mod_val:>6.1f}mm"
        else:
            mod_str = ""
        print(
            f"    {k + 1}. {elem['code']:<16} {elem['type']:<5} "
            f"f={elem['focal_length']:>7.1f}mm  "
            f"d={elem['diameter']:>6.1f}mm{mod_str}  "
            f"tmax={tmax_str}mm  "
            f"tedge={tedge_str}mm  "
            f"h={results['ray_heights'][k]:>7.3f}  "
            f"z={z_k:>6.1f}mm  "
            f"{envelope}{flag_str}"
        )
        if u_next is not None:
            gap_info = results["element_gaps"][k]
            both_known = gap_info["both_thickness_known"]
            print(f"       {img_str}  obj_dist={u_next:>7.1f}mm")
            if gap_info.get("stop_in_gap"):
                gf = gap_info["gap_front_mm"]
                gr = gap_info["gap_rear_mm"]
                print(
                    f"       --- {gf:.1f}mm (gap front {gf:.1f}mm"
                    f"{_gap_warn(gf, both_known)}) ---"
                )
                print(f"       *  STOP ({STOP_THICKNESS_MM:.0f}mm)")
                print(
                    f"       --- {gr:.1f}mm (gap rear {gr:.1f}mm"
                    f"{_gap_warn(gr, both_known)}) ---"
                )
            else:
                gap = gap_info["gap_mm"]
                print(
                    f"       --- {spacings[k]:.1f}mm "
                    f"(gap {gap:.1f}mm{_gap_warn(gap, both_known)}) ---"
                )
        else:
            print(f"       {img_str}")

    # -- Summary stats --
    print(f"\n  {'EFL:':<22} {results['efl']:>8.1f} mm")
    efl = results["efl"]
    if math.isfinite(efl) and efl > 0:
        fov_diag = 2.0 * math.degrees(math.atan(FRAME_HALF_DIAG_MM / efl))
        fov_side = 2.0 * math.degrees(math.atan((FRAME_HALF_DIAG_MM / math.sqrt(2)) / efl))
        print(
            f"  {'FOV (6x6 frame):':<22} {fov_diag:>8.1f} deg diagonal, "
            f"{fov_side:.1f} deg side"
        )
    bc_flag = " (est; record radii)" if results.get(
        "back_clearance_undetermined") else ""
    print(f"  {'Back clearance:':<22} {results['back_clearance']:>8.1f} mm"
          f"{bc_flag}")
    print(f"  {'Speed:':<22} f/{results['f_number']:.2f}")
    print(
        f"  {'Max ap. w/o limiter:':<22} f/{results['f_soft']:.2f}"
        f"  (limiting element removed)"
    )

    va = results["vignetting_abruptness"]
    if va < 0.7:
        va_label = "gradual rolloff"
    elif va <= 0.9:
        va_label = "moderate"
    else:
        va_label = "hard cutoff"
    print(f"  {'Vignetting:':<22} {va:.2f} ({va_label})")

    # -- Aperture stop summary --
    sa = results["stop_after"]
    ss = results["stop_spacing"]
    src = results["stop_source"]
    if sa == 0:
        loc = f"{ss:.1f}mm in front of element 1"
    else:
        loc = f"{ss:.1f}mm behind element {sa}"
    score = results["stop_score"]
    if math.isfinite(score):
        if score < 0.7:
            score_label = "well-centered"
        elif score < 1.0:
            score_label = "marginal"
        else:
            score_label = "off-axis bundle clipped"
        score_str = f"score {score:.2f} ({score_label})"
    else:
        score_str = "score n/a"
    print(f"  {'Aperture stop:':<22} {loc}  [{src}, {score_str}]")
    # Distortion sign only -- magnitude of the indicator is not a
    # physical quantity.  See _distortion_indicator() for the
    # caveats.
    print(f"  {'Distortion (sign):':<22} {results['distortion_label']}")

    print(f"  {'Petzval radius:':<22} {results['petzval_radius']:>8.1f} mm")

    # -- Swirl character (thin-lens 3rd-order Seidel; see "Swirl" in https://brianssparetime.com/posts/zenzanot-4-swirl/).
    # Reports the swirl decomposition's independent levers separately; deliberately no single
    # combined "swirl score", which would re-bury what the panel separates.
    sw = results["swirl_astig_mm"]
    dir_label, coma_astig_ratio = results["swirl_orientation"]
    # The bare astigmatic-sign tendency, shown alongside so the reader can see
    # what coma overrode when the two disagree.
    astig_tendency = ("tangential" if sw > 0.05
                      else "radial" if sw < -0.05 else "none")
    cf = results["corner_field_coc_mm"]
    pl_mm = results["plateau_radius_mm"]
    pl_pct = results["plateau_radius_pct"]
    fill = results["stop_score"]
    coma = results["coma_indicator_mm"]
    fnum = results["f_number"]
    coma_blur = abs(coma) / fnum if fnum > 0 and math.isfinite(fnum) else 0.0
    if coma_blur < 0.05:
        coma_mag = "negligible"
    elif coma_blur < 0.15:
        coma_mag = "moderate"
    else:
        coma_mag = "strong"
    coma_dir = "outward" if coma > 0 else "inward" if coma < 0 else "--"
    field_desc = ("deep field" if abs(results["petzval_radius"]) < 250
                  else "shallow field")
    plat_desc = (f"{pl_pct:.0f}% plateau" if math.isfinite(pl_pct)
                 else "plateau n/a")
    verdict = f"{dir_label}, {field_desc}, {plat_desc}, {coma_mag} coma"

    if not math.isfinite(coma_astig_ratio):
        ratio_str = "coma/astig inf"
    else:
        ratio_str = f"coma/astig {coma_astig_ratio:.1f}"
    print("  Swirl character (thin-lens 3rd-order; see 'Swirl' in https://brianssparetime.com/posts/zenzanot-4-swirl/):")
    print(f"    {'direction:':<18} {dir_label}  "
          f"(astig {sw:+.3f}mm {astig_tendency}; {ratio_str}; "
          f"foreground inverts)")
    print(f"    {'edge defocus:':<18} corner blur {cf:.2f}mm  "
          f"(field-curvature enabler)")
    if math.isfinite(pl_mm):
        print(f"    {'plateau (medial):':<18} {pl_mm:.1f}mm "
              f"({pl_pct:.1f}% of frame)")
    else:
        print(f"    {'plateau (medial):':<18} n/a")
    if math.isfinite(fill):
        print(f"    {'image-circle fill:':<18} {fill:.2f}  "
              f"(higher = tighter = more swirl-prone)")
    print(f"    {'coma:':<18} ~{coma_blur:.2f}mm {coma_dir}  "
          f"({coma_mag} radial contaminant; order-of-mag, sign approx)")
    print(f"    {'conc. neg. power:':<18} {results['conc_neg_label']}  "
          f"(higher-order edge curl unmodeled)")
    print(f"    {'verdict:':<18} {verdict}")
    print(f"    {'caveat:':<18} cat-eye vignetting not modeled "
          f"(often dominates the real look)")
    sr_mm = results["sharp_radius_mm"]
    sr_pct = results["sharp_radius_pct"]
    cf = results["corner_field_coc_mm"]
    sph = results["axial_spherical_coc_mm"]
    if math.isfinite(sr_mm):
        print(
            f"  {'Sharp center radius:':<22} {sr_mm:>7.1f} mm "
            f"({sr_pct:.1f}% of frame)"
        )
        print(
            f"  {'  axial spherical CoC:':<22} {sph:>7.3f} mm    "
            f"corner CoC: {cf:.2f}mm"
        )
    else:
        print(f"  {'Sharp center radius:':<22} infinite")

    # -- BFD compliance + margins --
    status = "PASS" if results["bfd_compliant"] else "FAIL"
    print(
        f"\n  Clearance compliance: {status}    "
        f"rear glass at z = {results['throat_margin']:+.1f}mm from flange"
    )
    for v in results["bfd_violations"]:
        print(f"    - {v}")

    # -- Chromatic aberration --
    lca = results["lca"]
    if abs(lca) < 0.5:
        ca_label = "minimal"
    elif abs(lca) < 1.5:
        ca_label = "low"
    elif abs(lca) < 3.0:
        ca_label = "moderate"
    else:
        ca_label = "severe"
    print(f"  {'Axial CA (F-C):':<22} {lca:>+7.2f} mm ({ca_label})")

    # -- Total physical length --
    print(f"  {'Total length:':<22} {results['total_length']:>8.1f} mm")
    # -- BFD: optical rear-vertex-to-film, the intermediate behind back
    #    clearance.  Low priority; back clearance is the build dimension.
    print(f"  {'BFD (rear vertex):':<22} {results['bfd']:>8.1f} mm  (optical)")

    # -- Spacing sensitivity --
    bfd_parts = []
    efl_parts = []
    for j in range(len(results["bfd_sensitivity"])):
        bfd_parts.append(f"d{j + 1} = {results['bfd_sensitivity'][j]:+.2f}")
        efl_parts.append(f"d{j + 1} = {results['efl_sensitivity'][j]:+.2f}")
    print(f"\n  BFD sensitivity (mm/mm): {', '.join(bfd_parts)}")
    print(f"  EFL sensitivity (mm/mm): {', '.join(efl_parts)}")
    max_sens = results["max_bc_sensitivity"]
    print(f"  Max |BFD sens|: {max_sens:>5.2f} mm/mm")
    if notes:
        print(f"\n  Notes: {notes}")
    print()


# ---------------------------------------------------------------------------
# Database persistence
# ---------------------------------------------------------------------------

def save_design(name, elements, spacings, results, notes=None, inf_offset=None):
    """Write the design and its results to lens_designs.db.

    `results` must be design-frame (computed with inf_offset = 0); the measured
    `inf_offset` is stored alongside as its own column and applied at render
    time, never baked into the stored geometry.
    """
    con = sqlite3.connect(DESIGNS_DB)
    con.executescript(DESIGNS_SCHEMA)
    migrate_designs_db(con)

    cur = con.execute(
        "INSERT INTO designs "
        "(name, created, efl, bfd, back_clearance, inf_offset, f_number, "
        " vignetting_abruptness, "
        " petzval_radius, swirl_pct, swirl_astig_mm, plateau_radius_mm, "
        " sharp_radius_mm, sharp_radius_pct, "
        " corner_field_coc_mm, bfd_compliant, "
        " throat_margin, stop_after, stop_spacing, lca, total_length, "
        " max_bc_sensitivity, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "        ?, ?, ?)",
        (
            name,
            datetime.now().isoformat(),
            results["efl"],
            results["bfd"],
            results["back_clearance"],
            inf_offset,
            results["f_number"],
            results["vignetting_abruptness"],
            results["petzval_radius"],
            results["swirl_pct"],
            results["swirl_astig_mm"],
            (results["plateau_radius_mm"]
             if math.isfinite(results["plateau_radius_mm"]) else None),
            results["sharp_radius_mm"],
            results["sharp_radius_pct"],
            results["corner_field_coc_mm"],
            1 if results["bfd_compliant"] else 0,
            results["throat_margin"],
            results["stop_after"],
            results["stop_spacing"],
            results["lca"],
            results["total_length"],
            results["max_bc_sensitivity"],
            notes,
        ),
    )
    design_id = cur.lastrowid

    bfd_sens = results["bfd_sensitivity"]
    efl_sens = results["efl_sensitivity"]
    for k, elem in enumerate(elements):
        spacing = spacings[k] if k < len(spacings) else None
        b = bfd_sens[k] if k < len(bfd_sens) else None
        e = efl_sens[k] if k < len(efl_sens) else None
        con.execute(
            "INSERT INTO design_elements (design_id, position, element_code, "
            "spacing_after, bfd_sensitivity, efl_sensitivity, orientation) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (design_id, k + 1, elem["code"], spacing, b, e,
             elem.get("orientation")),
        )

    con.commit()
    con.close()
    return design_id


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------

def parse_spec(tokens):
    """Parse alternating element-codes and spacings, with optional 'stop N S'.

    Returns (codes, spacings, stop_after, stop_spacing).
    """
    codes = []
    spacings = []
    stop_after = None
    stop_spacing = None

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        if tok.lower() == "stop":
            if i + 2 >= len(tokens):
                sys.exit("Error: 'stop' requires two arguments: element_number spacing_mm")
            try:
                stop_after = int(tokens[i + 1])
                stop_spacing = float(tokens[i + 2])
            except ValueError:
                sys.exit("Error: 'stop' arguments must be: integer float")
            i += 3
            continue

        # Try to interpret as a number (spacing) or a string (element code).
        try:
            spacings.append(float(tok))
        except ValueError:
            codes.append(tok)
        i += 1

    if len(codes) < 2:
        sys.exit("Error: a design needs at least 2 elements.")
    if len(spacings) != len(codes) - 1:
        sys.exit(
            f"Error: expected {len(codes) - 1} spacing(s) for {len(codes)} "
            f"elements, got {len(spacings)}."
        )
    return codes, spacings, stop_after, stop_spacing


def load_design_from_db(name):
    """Load a saved design's spec from the database. Returns (codes, spacings,
    stop_after, stop_spacing, notes, orientations, inf_offset).

    orientations is parallel to codes (CX_FRONT/CX_REAR/None per element);
    inf_offset is the stored empirical offset (None if unset)."""
    con = sqlite3.connect(DESIGNS_DB)
    con.row_factory = sqlite3.Row
    try:
        name = resolve_design_name(con, name)
    except DesignNameError as e:
        con.close()
        sys.exit(f"Error: {e}.")
    row = con.execute(
        "SELECT * FROM designs WHERE name = ? ORDER BY id DESC LIMIT 1",
        (name,),
    ).fetchone()
    if row is None:
        con.close()
        sys.exit(f"Error: no design named '{name}' found in {DESIGNS_DB}.")
    design = dict(row)
    elems = con.execute(
        "SELECT element_code, spacing_after, orientation FROM design_elements "
        "WHERE design_id = ? ORDER BY position",
        (design["id"],),
    ).fetchall()
    con.close()
    codes = [e["element_code"] for e in elems]
    spacings = [e["spacing_after"] for e in elems if e["spacing_after"] is not None]
    orientations = [e["orientation"] for e in elems]
    return (codes, spacings, design["stop_after"], design["stop_spacing"],
            design["notes"], orientations, design.get("inf_offset"))


def rebuild_all_designs(name_prefix=None):
    """Recompute metrics for saved designs and update each row in place.

    With name_prefix set, only designs whose name starts with it are rebuilt;
    the rest keep their stored metrics untouched."""
    con = sqlite3.connect(DESIGNS_DB)
    con.executescript(DESIGNS_SCHEMA)
    migrate_designs_db(con)
    con.row_factory = sqlite3.Row
    if name_prefix:
        rows = con.execute(
            "SELECT id, name FROM designs WHERE name LIKE ? ORDER BY id",
            (name_prefix + "%",),
        ).fetchall()
    else:
        rows = con.execute("SELECT id, name FROM designs ORDER BY id").fetchall()
    if not rows:
        print("No designs to rebuild.")
        con.close()
        return

    for row in rows:
        design_id = row["id"]
        name = row["name"]
        elem_rows = con.execute(
            "SELECT element_code, spacing_after, orientation FROM design_elements "
            "WHERE design_id = ? ORDER BY position",
            (design_id,),
        ).fetchall()
        codes = [e["element_code"] for e in elem_rows]
        spacings = [e["spacing_after"] for e in elem_rows
                    if e["spacing_after"] is not None]
        orientations = [e["orientation"] for e in elem_rows]
        stop_row = con.execute(
            "SELECT stop_after, stop_spacing FROM designs WHERE id = ?",
            (design_id,),
        ).fetchone()
        try:
            elements = get_elements(codes)
            # Design-frame (inf_offset = 0); the stored inf_offset column is
            # left untouched by this rebuild.
            results = compute_design(
                elements, spacings,
                stop_row["stop_after"], stop_row["stop_spacing"],
                orientations,
            )
        except SystemExit as e:
            print(f"  #{design_id} {name}: skipped ({e})")
            continue

        con.execute(
            "UPDATE designs SET "
            "  efl=?, bfd=?, back_clearance=?, f_number=?, "
            "  vignetting_abruptness=?, "
            "  petzval_radius=?, swirl_pct=?, swirl_astig_mm=?, "
            "  plateau_radius_mm=?, sharp_radius_mm=?, "
            "  sharp_radius_pct=?, corner_field_coc_mm=?, bfd_compliant=?, "
            "  throat_margin=?, lca=?, total_length=?, "
            "  max_bc_sensitivity=? "
            "WHERE id=?",
            (
                results["efl"], results["bfd"], results["back_clearance"],
                results["f_number"], results["vignetting_abruptness"],
                results["petzval_radius"], results["swirl_pct"],
                results["swirl_astig_mm"],
                (results["plateau_radius_mm"]
                 if math.isfinite(results["plateau_radius_mm"]) else None),
                results["sharp_radius_mm"], results["sharp_radius_pct"],
                results["corner_field_coc_mm"],
                1 if results["bfd_compliant"] else 0,
                results["throat_margin"],
                results["lca"], results["total_length"],
                results["max_bc_sensitivity"],
                design_id,
            ),
        )
        # Update per-spacing sensitivities on existing design_elements rows.
        bfd_sens = results["bfd_sensitivity"]
        efl_sens = results["efl_sensitivity"]
        for k in range(len(elem_rows)):
            b = bfd_sens[k] if k < len(bfd_sens) else None
            e = efl_sens[k] if k < len(efl_sens) else None
            con.execute(
                "UPDATE design_elements "
                "SET bfd_sensitivity=?, efl_sensitivity=? "
                "WHERE design_id=? AND position=?",
                (b, e, design_id, k + 1),
            )
        sr_str = (f"{results['sharp_radius_mm']:.1f}mm "
                  f"({results['sharp_radius_pct']:.1f}%)"
                  if math.isfinite(results['sharp_radius_mm']) else "inf")
        print(f"  #{design_id} {name}: swirl {results['swirl_pct']:.1f}%  "
              f"sharp {sr_str}  "
              f"max BFD sens {results['max_bc_sensitivity']:.2f}")
    con.commit()
    con.close()
    print(f"\nRebuilt {len(rows)} design(s).")


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate a candidate lens design.",
        epilog=(
            "Examples:\n"
            "  %(prog)s \"Test Design\" big-BCX-120 30 Ed-32-886 8 L7325 stop 2 5\n"
            "  %(prog)s --from-db v9a-retro --no-save"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("name", nargs="?", help="Design name")
    parser.add_argument(
        "spec", nargs="*",
        help="Alternating element codes and spacings (mm), "
             "optionally followed by 'stop N S'",
    )
    parser.add_argument(
        "--from-db", action="store_true",
        help="Load the design spec from the database instead of the command line",
    )
    parser.add_argument(
        "--rebuild-all", action="store_true",
        help="Re-evaluate every saved design and update its stored metrics in place",
    )
    parser.add_argument(
        "--rebuild", metavar="PREFIX",
        help="Re-evaluate only saved designs whose name starts with PREFIX "
             "(e.g. v14), updating their stored metrics in place",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List the names of every saved design and exit",
    )
    parser.add_argument(
        "-w", "--with-element", action="append", metavar='"CODE TYPE FL DIAM"',
        help="Add a hypothetical element: \"code type focal_length diameter\" "
             "(saved to inventory as non-purchased)",
    )
    parser.add_argument(
        "--notes", help="Notes about this design (stored in lens_designs.db)",
    )
    parser.add_argument(
        "--set-inf-offset", type=float, metavar="MM", default=None,
        help="Record the empirical infinity-focus offset (mm; + shifts the "
             "optical core away from the film, - toward it) on the named saved "
             "design and exit",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Display results without saving the design to the database",
    )
    parser.add_argument(
        "--field-shape", action="store_true",
        help="Also print sagittal/tangential/medial focal-surface curves "
             "vs field height (3rd-order Seidel; see 'Field curvature' in https://brianssparetime.com/posts/zenzanot-4-swirl/)",
    )
    parser.add_argument(
        "--stop-calc", action="store_true",
        help="Also print physical iris hole diameter for each standard "
             "f-stop (entrance-pupil math accounting for the front group)",
    )
    parser.add_argument(
        "--extras", action="store_true",
        help="Turn on every diagnostic extra (currently --field-shape and "
             "--stop-calc); convenience switch that picks up future flags too",
    )

    args = parser.parse_args()

    if args.extras:
        args.field_shape = True
        args.stop_calc = True

    if args.rebuild_all:
        rebuild_all_designs()
        return
    if args.rebuild:
        rebuild_all_designs(args.rebuild)
        return
    if args.list:
        con = sqlite3.connect(DESIGNS_DB)
        for row in con.execute("SELECT name FROM designs ORDER BY name"):
            print(row[0])
        con.close()
        return
    if args.set_inf_offset is not None:
        if args.name is None:
            sys.exit("Error: --set-inf-offset requires a design name.")
        con = sqlite3.connect(DESIGNS_DB)
        con.executescript(DESIGNS_SCHEMA)
        migrate_designs_db(con)
        try:
            full = resolve_design_name(con, args.name)
        except DesignNameError as e:
            con.close()
            sys.exit(f"Error: {e}.")
        con.execute("UPDATE designs SET inf_offset=? WHERE name=?",
                    (args.set_inf_offset, full))
        con.commit()
        con.close()
        print(f"Set inf_offset = {args.set_inf_offset:+.1f}mm for design "
              f"'{full}'.")
        return
    if args.name is None:
        sys.exit("Error: design name required (or use --rebuild-all / --list).")

    # Register any hypothetical elements before looking up codes.
    for spec_str in (args.with_element or []):
        parts = spec_str.split()
        if len(parts) != 4:
            sys.exit(
                f"Error: --with-element requires exactly 4 values "
                f"(code type fl diam), got: {spec_str!r}"
            )
        code, elem_type = parts[0], parts[1]
        try:
            fl, diam = float(parts[2]), float(parts[3])
        except ValueError:
            sys.exit(f"Error: focal_length and diameter must be numbers in: {spec_str!r}")
        ensure_hypothetical_in_inventory(code, elem_type, fl, diam)

    orientations = None
    inf_offset = None
    if args.from_db:
        if args.spec:
            sys.exit("Error: do not provide a spec when using --from-db.")
        (codes, spacings, stop_after, stop_spacing, db_notes,
         orientations, inf_offset) = load_design_from_db(args.name)
        if args.notes is None:
            args.notes = db_notes
    else:
        if not args.spec:
            sys.exit("Error: provide a spec (element codes and spacings) or use --from-db.")
        codes, spacings, stop_after, stop_spacing = parse_spec(args.spec)
    elements = get_elements(codes)
    # Design-frame metrics (inf_offset = 0) -- this is what gets stored.
    results = compute_design(elements, spacings, stop_after, stop_spacing,
                             orientations)
    # As-built view for display: apply the measured offset if present.
    offset = inf_offset or 0.0
    if abs(offset) > 1e-9:
        disp = compute_design(elements, spacings, stop_after, stop_spacing,
                              orientations, inf_offset=offset)
    else:
        disp = results
    display_results(args.name, elements, spacings, disp, notes=args.notes)
    if args.field_shape:
        fl_list = [e["focal_length"] for e in elements]
        field_shape = compute_field_shape(
            fl_list,
            results.get("stop_chief_heights"),
            results["petzval_radius"],
            FRAME_HALF_DIAG_MM,
        )
        display_field_shape(field_shape, results["f_number"])
    if args.stop_calc:
        fl_list = [e["focal_length"] for e in elements]
        M, u_ent, traceable = compute_stop_magnification(
            fl_list,
            results["pp_dists"],
            results["offsets"],
            results["stop_after"],
            results["stop_spacing"],
            STOP_THICKNESS_MM,
        )
        # Use the wide-open f-number as the floor for iris stops.
        # f_number is the max aperture; f_soft is the same with the limiting
        # element removed (recomputed for display, not used here).
        wide_open = results["f_number"]
        eff_M = abs(M) if (traceable and M is not None) else 1.0
        rows = compute_stop_calc(results["efl"], eff_M, wide_open)
        display_stop_calc(results["efl"], M, traceable, u_ent,
                          wide_open, rows)
    if not args.no_save:
        design_id = save_design(args.name, elements, spacings, results,
                                notes=args.notes, inf_offset=inf_offset)
        print(f"  Saved as design #{design_id} in {DESIGNS_DB}\n")


if __name__ == "__main__":
    main()
