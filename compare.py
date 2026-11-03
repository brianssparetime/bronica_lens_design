#!/usr/bin/env python3
"""Compare 2-4 lens designs side by side."""

import argparse
import sqlite3
import sys

from design_db import resolve_design_name, DesignNameError

DESIGNS_DB = "lens_designs.db"


def get_designs(names):
    """Look up designs by name (short prefixes allowed). Returns list of dicts."""
    con = sqlite3.connect(DESIGNS_DB)
    con.row_factory = sqlite3.Row
    designs = []
    for name in names:
        try:
            full = resolve_design_name(con, name)
        except DesignNameError as e:
            con.close()
            sys.exit(f"Error: {e}.")
        rows = con.execute(
            "SELECT * FROM designs WHERE name = ? ORDER BY id DESC LIMIT 1",
            (full,),
        ).fetchall()
        designs.append(dict(rows[0]))
    con.close()
    return designs


def get_design_elements(design_id):
    """Return the element list for a design."""
    con = sqlite3.connect(DESIGNS_DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT element_code, spacing_after, bfd_sensitivity, efl_sensitivity "
        "FROM design_elements "
        "WHERE design_id = ? ORDER BY position",
        (design_id,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def format_val(val, fmt):
    """Format a value, handling None/inf."""
    if val is None:
        return "-"
    return fmt.format(val)


def _inf_offset(d):
    return d.get("inf_offset") or 0.0


def applied(d, key):
    """Stored design-frame value with the design's inf_offset applied."""
    v = d.get(key)
    return None if v is None else v + _inf_offset(d)


def back_clearance_disp(d):
    """As-built back clearance; falls back to bfd for un-rebuilt rows."""
    bc = d.get("back_clearance")
    if bc is None:
        bc = d.get("bfd")
    return None if bc is None else bc + _inf_offset(d)


def main():
    parser = argparse.ArgumentParser(description="Compare 2-4 lens designs side by side.")
    parser.add_argument("names", nargs="+", metavar="design_name",
                        help="Names of designs to compare (2-4)")
    args = parser.parse_args()

    if len(args.names) < 2 or len(args.names) > 4:
        sys.exit("Error: provide 2 to 4 design names.")

    designs = get_designs(args.names)
    elem_lists = [get_design_elements(d["id"]) for d in designs]

    # Column widths
    label_w = 22
    col_w = max(18, max(len(n) for n in args.names) + 2)

    def row(label, values):
        cols = "".join(f"{v:>{col_w}}" for v in values)
        print(f"  {label:<{label_w}}{cols}")

    def sep():
        print(f"  {'-' * (label_w + col_w * len(designs))}")

    # Header
    print()
    row("", args.names)
    sep()

    if any(_inf_offset(d) for d in designs):
        print("  (back clearance, BFD, and margins include each design's "
              "inf_offset where set)")
        print()

    # Stats rows
    row("EFL (mm)", [format_val(d["efl"], "{:.1f}") for d in designs])
    row("Back clearance (mm)",
        [format_val(back_clearance_disp(d), "{:.1f}") for d in designs])

    def speed_str(d):
        return format_val(d.get("f_number"), "f/{:.2f}")

    row("Speed", [speed_str(d) for d in designs])

    def vig_str(d):
        va = d.get("vignetting_abruptness")
        if va is None:
            return "-"
        if va < 0.7:
            label = "gradual"
        elif va <= 0.9:
            label = "moderate"
        else:
            label = "hard"
        return f"{va:.2f} ({label})"

    row("Vignetting", [vig_str(d) for d in designs])
    row("Petzval radius (mm)", [format_val(d["petzval_radius"], "{:.1f}") for d in designs])
    row("Swirl dir (mm, +=swirl)", [format_val(d.get("swirl_astig_mm"), "{:+.2f}") for d in designs])
    row("Plateau radius (mm)", [format_val(d.get("plateau_radius_mm"), "{:.1f}") for d in designs])

    def sharp_str(d):
        sr = d.get("sharp_radius_mm")
        sp = d.get("sharp_radius_pct")
        if sr is None or sp is None:
            return "-"
        return f"{sr:.1f}mm ({sp:.0f}%)"

    row("Sharp center radius", [sharp_str(d) for d in designs])
    row("Corner field CoC (mm)",
        [format_val(d.get("corner_field_coc_mm"), "{:.2f}") for d in designs])
    def lca_str(d):
        lca = d.get("lca")
        if lca is None:
            return "-"
        a = abs(lca)
        if a < 0.5:
            label = "minimal"
        elif a < 1.5:
            label = "low"
        elif a < 3.0:
            label = "moderate"
        else:
            label = "severe"
        return f"{lca:+.2f} ({label})"

    row("Axial CA F-C (mm)", [lca_str(d) for d in designs])
    row("Clearance compliant", ["PASS" if d["bfd_compliant"] else "FAIL" for d in designs])
    row("Rear glass z (mm)", [format_val(applied(d, "throat_margin"), "{:+.1f}") for d in designs])
    row("Total length (mm)", [format_val(d.get("total_length"), "{:.1f}") for d in designs])
    row("BFD (rear vertex) mm", [format_val(applied(d, "bfd"), "{:.1f}") for d in designs])
    row("inf_offset (mm)", [format_val(d.get("inf_offset"), "{:+.1f}") for d in designs])

    row("Max |BFD sens| mm/mm",
        [format_val(d.get("max_bc_sensitivity"), "{:.2f}") for d in designs])
    sep()

    # Elements
    max_elems = max(len(el) for el in elem_lists)
    for i in range(max_elems):
        codes = []
        for el in elem_lists:
            if i < len(el):
                codes.append(el[i]["element_code"])
            else:
                codes.append("-")
        row(f"Element {i + 1}", codes)

        spacings = []
        for el in elem_lists:
            if i < len(el) and el[i]["spacing_after"] is not None:
                spacings.append(f"{el[i]['spacing_after']:.1f}")
            else:
                spacings.append("")
        if any(spacings):
            row(f"  spacing (mm)", spacings)

        bsens = []
        esens = []
        for el in elem_lists:
            if i < len(el) and el[i].get("bfd_sensitivity") is not None:
                bsens.append(f"{el[i]['bfd_sensitivity']:+.2f}")
                esens.append(f"{el[i]['efl_sensitivity']:+.2f}")
            else:
                bsens.append("")
                esens.append("")
        if any(bsens):
            row(f"   dBFD/dS", bsens)
            row(f"   dEFL/dS", esens)

    sep()

    row("Stop after elem", [str(d["stop_after"]) if d["stop_after"] else "-" for d in designs])
    row("Stop spacing (mm)", [format_val(d["stop_spacing"], "{:.1f}") for d in designs])
    print()


if __name__ == "__main__":
    main()
