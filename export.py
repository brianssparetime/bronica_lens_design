#!/usr/bin/env python3
"""Export a saved lens design as a SCADwright-compatible design.json file.

Produces the schema used by ../SCADwright/s2-evolving/design.json:

    {
      "elements": [
        {"code": ..., "type": ..., "diameter": ..., "edge_thickness": ...,
         "spacing": ..., "mounted_od": ...},   # mounted_od optional
        ...
      ],
      "stop": {"depth": ..., "hole_diameters": [...]}
    }

Coordinate convention: `spacing` and `depth` are the z-coordinate of the
element / stop center measured from the M57 flange (z=0), with +z pointing
away from the film toward the object.  Internally we work in evaluate.py's
"distance from film" frame, then subtract FLANGE_DISTANCE.

`edge_thickness` is per-element edge thickness (NOT center thickness).
`hole_diameters` are the physical iris-hole diameters for each standard
f-stop slower than the lens's wide-open speed (computed via the
front-group entrance-pupil math used by `evaluate.py --stop-calc`).
"""

import argparse
import json
import math
import sys

from evaluate import (
    DEFAULT_THICKNESS,
    FRAME_HALF_DIAG_MM,
    STOP_THICKNESS_MM,
    compute_design,
    compute_stop_calc,
    compute_stop_magnification,
    get_elements,
    load_design_from_db,
)

FLANGE_DISTANCE = 101.7    # mm from film plane (M57 throat far edge)


def build_design_json(name):
    (codes, spacings, stop_after, stop_spacing, _notes,
     orientations, inf_offset) = load_design_from_db(name)
    elements = get_elements(codes)
    # Emit geometry in the design frame (inf_offset = 0): the element positions
    # and stop depth below are the un-shifted core coordinates.  The measured
    # inf_offset is recorded as its own field instead of being baked into the
    # coordinates, so a housing maker that applies it cannot double-count the
    # shift.  inf_offset is a rigid translation of the whole core relative to
    # the film; it does not change the inter-element spacings.
    inf_offset = inf_offset or 0.0
    results = compute_design(elements, spacings, stop_after, stop_spacing,
                             orientations, inf_offset=0.0)

    n = len(elements)

    # Per-element axial thickness used to position centers / faces.  This
    # mirrors spacing_diagram.py: real max_thickness when known, else the
    # default fallback.  Used only to locate face planes for the stop
    # geometry; element JSON output reports inventory edge_thickness.
    thick = [
        e["max_thickness"] if e["max_thickness"] is not None else DEFAULT_THICKNESS
        for e in elements
    ]

    # Element-center z from film.  results["bfd"] is the rear-vertex-to-film
    # distance, so the rear element's CENTER sits half a thickness farther from
    # the film; then walk forward using the saved center-to-center spacings.
    z_from_film = [0.0] * n
    z_from_film[n - 1] = results["bfd"] + thick[n - 1] / 2.0
    for k in range(n - 2, -1, -1):
        z_from_film[k] = z_from_film[k + 1] + spacings[k]

    # ---- elements ----
    elem_out = []
    for k, e in enumerate(elements):
        edge = e["edge_thickness"]
        if edge is None:
            sys.exit(
                f"Error: element {e['code']} has NULL edge_thickness in inventory; "
                "cannot export."
            )
        entry = {
            "code": e["code"],
            "type": e["type"],
            "diameter": e["diameter"],
        }
        mod = e.get("mounted_od")
        if mod is not None and abs(mod - e["diameter"]) > 0.05:
            entry["mounted_od"] = mod
        entry["edge_thickness"] = edge
        entry["spacing"] = round(z_from_film[k] - FLANGE_DISTANCE, 3)
        elem_out.append(entry)

    # ---- stop ----
    if stop_after is None or stop_spacing is None:
        sys.exit(
            f"Error: design '{name}' has no stop configured; "
            "cannot export a SCADwright design.json."
        )
    if stop_after == 0:
        # Stop sits in front of element 1 (away from film).
        front_face_z = z_from_film[0] + thick[0] / 2.0
        stop_rear_z  = front_face_z + stop_spacing
        stop_center_z = stop_rear_z + STOP_THICKNESS_MM / 2.0
    else:
        # Stop sits in the gap behind element `stop_after` (toward film).
        rear_face_z   = z_from_film[stop_after - 1] - thick[stop_after - 1] / 2.0
        stop_front_z  = rear_face_z - stop_spacing
        stop_center_z = stop_front_z - STOP_THICKNESS_MM / 2.0
    depth = round(stop_center_z - FLANGE_DISTANCE, 3)

    # Hole diameters for every standard f-stop slower than wide-open.
    fl_list = [e["focal_length"] for e in elements]
    M, _u_ent, traceable = compute_stop_magnification(
        fl_list,
        results["pp_dists"],
        results["offsets"],
        results["stop_after"],
        results["stop_spacing"],
        STOP_THICKNESS_MM,
    )
    eff_M = abs(M) if (traceable and M is not None) else 1.0
    rows = compute_stop_calc(results["efl"], eff_M, results["f_number"])
    hole_diameters = [round(D_phys, 2) for (_N, _D_pup, D_phys) in rows]
    if not hole_diameters:
        sys.exit(
            f"Error: design '{name}' has no standard f-stops slower than its "
            "wide-open speed; cannot populate stop.hole_diameters."
        )

    efl = results["efl"]
    if math.isfinite(efl) and efl > 0:
        fov = math.floor(2.0 * math.degrees(math.atan(FRAME_HALF_DIAG_MM / efl)))
        focal_length = round(efl)
    else:
        fov = None
        focal_length = None

    fN = results["f_number"]
    if math.isfinite(fN):
        fN_rounded = round(fN, 1)
        max_aperture = int(fN_rounded) if fN_rounded == int(fN_rounded) else fN_rounded
    else:
        max_aperture = None

    cfg = {
        "name": name.split("-", 1)[0],
        "focal_length": focal_length,
        "max_aperture": max_aperture,
        "fov": fov,
        "elements": elem_out,
        "stop": {
            "depth": depth,
            "hole_diameters": hole_diameters,
        },
    }
    # Record the measured infinity-focus offset for the housing maker to apply.
    # Positions above are in the inf_offset = 0 design frame and do NOT include
    # it; the housing adds this rigid shift of the whole core toward (+) or away
    # from the film.
    if abs(inf_offset) > 1e-9:
        cfg["inf_offset"] = round(inf_offset, 3)
    return cfg


def main():
    parser = argparse.ArgumentParser(
        description="Export a saved lens design to a SCADwright-compatible "
                    "design.json file.",
    )
    parser.add_argument(
        "--from-db", required=True, metavar="DESIGN_NAME",
        help="Name of the saved design in lens_designs.db",
    )
    parser.add_argument(
        "output", help="Destination path for the design.json file",
    )
    args = parser.parse_args()

    cfg = build_design_json(args.from_db)
    with open(args.output, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    offset = cfg.get("inf_offset")
    note = (f"  [inf_offset = {offset:+.1f}mm recorded; positions un-shifted]"
            if offset else "")
    print(f"Wrote {args.output} (design '{args.from_db}').{note}")


if __name__ == "__main__":
    main()
