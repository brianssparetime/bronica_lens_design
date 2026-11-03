#!/usr/bin/env python3
"""Display a physical spacing diagram for a saved lens design.

Shows each element's position relative to the film plane and flange,
starting at the film plane and working outward toward the front element.
"""

import argparse
import sqlite3
import sys

from design_db import resolve_design_name, DesignNameError

DESIGNS_DB = "lens_designs.db"
INVENTORY_DB = "inventory.db"
DEFAULT_THICKNESS = 10.0
FLANGE_DISTANCE = 101.7    # mm from film plane (flange plane, z = 0)
# Stepped diameter-zone boundaries, given as z from the flange and converted
# to from-film positions.  Deeper than each boundary the bore narrows.
ZONE_BOUNDARIES = [
    (30.0, "dia <= 52.7 deeper"),
    (-7.3, "dia <= 50.45 deeper"),
    (-20.3, "dia <= 45.7 deeper"),
    (-30.0, "depth floor"),
]
STOP_THICKNESS = 2.0       # mm; matches STOP_THICKNESS_MM in evaluate.py

COL1 = 10   # from film
COL2 = 12   # from flange
COL3 = 42   # description
COL4 = 10   # diameter


def get_design(name):
    """Look up a design by name (most recent if duplicates)."""
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
        sys.exit(f"Error: no design named '{name}' found.")
    design = dict(row)
    elems = con.execute(
        "SELECT element_code, spacing_after FROM design_elements "
        "WHERE design_id = ? ORDER BY position",
        (design["id"],),
    ).fetchall()
    design["elements"] = [dict(e) for e in elems]
    con.close()
    return design


def get_element_info(codes):
    """Look up element details from the inventory."""
    con = sqlite3.connect(INVENTORY_DB)
    con.row_factory = sqlite3.Row
    info = {}
    for code in codes:
        row = con.execute(
            "SELECT code, type, focal_length, diameter, max_thickness, "
            "       edge_thickness, mounted_od "
            "FROM elements WHERE code = ?",
            (code,),
        ).fetchone()
        if row is None:
            con.close()
            sys.exit(f"Error: element '{code}' not found in inventory.")
        info[code] = dict(row)
    con.close()
    return info


def fmt_row(z_val, desc, diam=""):
    """Format one output row."""
    from_flange = z_val - FLANGE_DISTANCE
    return (
        f"  {z_val:>{COL1}.1f}  {from_flange:>+{COL2}.1f}  "
        f"{desc:<{COL3}}{diam}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Display a physical spacing diagram for a saved lens design.",
    )
    parser.add_argument("name", help="Design name")
    args = parser.parse_args()

    design = get_design(args.name)
    codes = [e["element_code"] for e in design["elements"]]
    spacings = [
        e["spacing_after"] for e in design["elements"]
        if e["spacing_after"] is not None
    ]
    elem_info = get_element_info(codes)

    n = len(codes)
    thicknesses = []
    for code in codes:
        t = elem_info[code]["max_thickness"]
        thicknesses.append(t if t is not None else DEFAULT_THICKNESS)

    bfd = design["bfd"]
    inf_offset = design.get("inf_offset") or 0.0
    back_clearance = design.get("back_clearance")
    if back_clearance is None:
        back_clearance = bfd
    # Element centers from the film.  bfd is rear-vertex-to-film, so the rear
    # element's center sits half a thickness farther out; inf_offset shifts the
    # whole core relative to the film (+ away, - toward).
    z = [0.0] * n
    z[n - 1] = bfd + thicknesses[n - 1] / 2.0 + inf_offset
    for k in range(n - 2, -1, -1):
        z[k] = z[k + 1] + spacings[k]

    # Build landmarks: (z, description, diameter_str)
    landmarks = []

    landmarks.append((0.0, "== FILM PLANE ==", ""))
    landmarks.append((FLANGE_DISTANCE, "== FLANGE (z=0) ==", ""))
    for z_flange, label in ZONE_BOUNDARIES:
        landmarks.append(
            (FLANGE_DISTANCE + z_flange, f"-- z={z_flange:+.1f}: {label} --", "")
        )

    for k in range(n):
        code = codes[k]
        info = elem_info[code]
        t = thicknesses[k]
        diam = info["diameter"]
        fl = info["focal_length"]
        etype = info["type"]
        rear = z[k] - t / 2
        front = z[k] + t / 2
        mod = info.get("mounted_od")
        tmax_val = info["max_thickness"]
        tedge_val = info["edge_thickness"]
        tmax_str = f"{tmax_val:.1f}" if tmax_val is not None else "?"
        tedge_str = f"{tedge_val:.1f}" if tedge_val is not None else "?"
        if mod is not None and abs(mod - diam) > 0.05:
            diam_str = (
                f"d={diam:.1f}mm  tmax={tmax_str}mm  tedge={tedge_str}mm  "
                f"mOD={mod:.1f}mm"
            )
        else:
            diam_str = (
                f"d={diam:.1f}mm  tmax={tmax_str}mm  tedge={tedge_str}mm"
            )

        landmarks.append((
            rear,
            f"]  {code}  rear",
            diam_str,
        ))
        landmarks.append((
            z[k],
            f"-  {code}  {etype}  f={fl:+.0f}mm",
            "",
        ))
        landmarks.append((
            front,
            f"[  {code}  front",
            "",
        ))

    # Aperture stop placement.
    # Convention from evaluate.py:
    #   stop_after = 0     -> stop in front of element 1
    #   stop_after = N>=1  -> stop in the gap behind element N (toward film)
    #   stop_spacing       -> distance from element-N rear face to stop FRONT face
    #                         (or from stop REAR face to element-1 front, when N=0)
    stop_after = design.get("stop_after")
    stop_spacing = design.get("stop_spacing")
    if stop_after is not None and stop_spacing is not None:
        if stop_after == 0:
            # In front of element 1 (largest z, away from film)
            elem1_front = z[0] + thicknesses[0] / 2
            stop_rear  = elem1_front + stop_spacing
            stop_front = stop_rear + STOP_THICKNESS
        else:
            # Behind element N, between elem N and elem N+1 (smaller z toward film)
            i = stop_after - 1
            elemN_rear = z[i] - thicknesses[i] / 2
            stop_front = elemN_rear - stop_spacing
            stop_rear  = stop_front - STOP_THICKNESS
        stop_center = (stop_front + stop_rear) / 2
        landmarks.append((
            stop_front,
            f"]  STOP  front",
            "",
        ))
        landmarks.append((
            stop_center,
            f"*  STOP  ({STOP_THICKNESS:.0f}mm thick, in air)",
            "",
        ))
        landmarks.append((
            stop_rear,
            f"[  STOP  rear",
            "",
        ))

    # Inter-element gaps.  When the stop is in a gap, split into front/rear.
    for j in range(len(spacings)):
        gap_total = spacings[j] - (thicknesses[j] / 2 + thicknesses[j + 1] / 2)
        if stop_after == j + 1:
            # Stop is in this gap (gap is between element j and element j+1)
            front_gap = stop_spacing  # elem j rear to stop front
            rear_gap = gap_total - STOP_THICKNESS - stop_spacing
            elemN_rear = z[j] - thicknesses[j] / 2
            mid_front = (elemN_rear + (elemN_rear - stop_spacing)) / 2
            mid_rear  = ((elemN_rear - stop_spacing - STOP_THICKNESS)
                         + z[j + 1] + thicknesses[j + 1] / 2) / 2
            landmarks.append((
                mid_front,
                f"   --- {front_gap:.1f}mm front-gap (elem {j+1} -> stop) ---",
                "",
            ))
            landmarks.append((
                mid_rear,
                f"   --- {rear_gap:.1f}mm rear-gap (stop -> elem {j+2}) ---",
                "",
            ))
        else:
            mid_z = (z[j] + z[j + 1]) / 2
            landmarks.append((
                mid_z,
                f"   --- {spacings[j]:.0f}mm c-t-c / {gap_total:.1f}mm gap ---",
                "",
            ))

    # Stop-in-front-of-element-1 case: also print a gap label
    if stop_after == 0:
        # Gap between stop rear face and element 1 front face equals stop_spacing
        elem1_front = z[0] + thicknesses[0] / 2
        mid_z = (elem1_front + (elem1_front + stop_spacing)) / 2
        landmarks.append((
            mid_z,
            f"   --- {stop_spacing:.1f}mm gap (stop -> elem 1) ---",
            "",
        ))

    landmarks.sort(key=lambda x: x[0])

    # Print
    print()
    print(f"  {args.name}: {' / '.join(codes)}")
    if abs(inf_offset) > 1e-9:
        direction = "away from" if inf_offset > 0 else "toward"
        print(f"  inf_offset = {inf_offset:+.1f}mm applied "
              f"(core shifted {direction} the film)")
    hdr = (
        f"  {'from film':>{COL1}}  {'from flange':>{COL2}}  "
        f"{'':>{COL3}}{'diameter'}"
    )
    print(f"  {'=' * (COL1 + COL2 + COL3 + COL4 + 6)}")
    print(hdr)

    for z_val, desc, diam in landmarks:
        print(fmt_row(z_val, desc, diam))

    print()
    tm = (design.get("throat_margin") or 0) + inf_offset
    print(f"  Back clearance = {back_clearance + inf_offset:.1f}mm"
          f"    Rear glass z = {tm:+.1f}mm from flange")
    print(f"  BFD (rear vertex) = {bfd + inf_offset:.1f}mm  (optical)")
    print()


if __name__ == "__main__":
    main()
