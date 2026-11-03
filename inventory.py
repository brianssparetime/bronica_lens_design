#!/usr/bin/env python3
"""Lens element inventory database."""

import argparse
import sqlite3
import sys

DB_PATH = "inventory.db"

SCHEMA = """\
CREATE TABLE IF NOT EXISTS elements (
    code             TEXT PRIMARY KEY,
    type             TEXT NOT NULL,
    focal_length     REAL NOT NULL,
    diameter         REAL NOT NULL,
    order_id         TEXT NOT NULL,
    refractive_index REAL NOT NULL DEFAULT 1.5,
    abbe_number      REAL NOT NULL DEFAULT 64,
    max_thickness    REAL,
    edge_thickness   REAL,
    mounted_od       REAL,
    qty              INTEGER NOT NULL DEFAULT 1,
    purchased        INTEGER NOT NULL DEFAULT 1
);
"""

# Tuple fields:
#   code, type, fl, diameter, order_id, n, V,
#   max_thickness, edge_thickness, mounted_od, qty
# V=64 for singlets (crown/BK7), V=1000 for achromats (corrected).
# max_thickness=None means unmeasured (tools assume DEFAULT_THICKNESS).
# mounted_od=None means use the bare diameter for clearance checks.
INITIAL_DATA = [
    ("big-BCX-120", "BCX", 120, 120, "amazon southst", 1.5168, 64, 30, 5, None, 1),
    ("L10938", "PCX", 216, 42, "SHED20250224045919", 1.5, 64, 5.2, 3.3, None, 1),
    ("L10971", "DCX", 125, 42.4, "SHED20250224045919", 1.5, 64, 4.9, 2.1, None, 1),
    ("L14929", "ACH", 150, 38.6, "SHED20250224045919", 1.5, 1000, 9, 6.3, None, 1),
    ("L8039", "PMN", 160, 44, "SHED20250224045919", 1.5, 64, 10.5, 6.4, None, 1),
    ("L4213", "DCX", 35, 41.6, "SHED20250224045919", 1.5, 64, 15.8, 5.8, None, 1),
    ("L14523", "ACH", 125, 35, "SHED20250220232129", 1.5, 1000, 12.6, 12.6, 44.4, 1),
    ("L13088", "BCX", 133, 42.5, "SHED20250220232129", 1.5, 64, 5.8, 2.2, None, 1),
    ("L11561", "ACH", 130, 38.5, "SHED20250220232129", 1.5, 1000, 10.1, 7.5, None, 1),
    ("L8098", "PCV", -83, 44, "SHED20250808004234", 1.5, 64, 8.7, 8.7, None, 1),
    ("L14801", "NMN", -148, 48.2, "SHED20250808004234", 1.5, 64, 7.5, 6.4, None, 1),
    ("L12007", "NMN", -133, 38.1, "SHED20250808004234", 1.5, 64, 6.5, 5.5, None, 1),
    ("L8259", "DCX", 58, 43, "SHED20250808153427", 1.5, 64, 9.5, 1.5, None, 1),
    ("L4258", "DCX", 103, 62.7, "SHED20250808153427", 1.5, 64, 11.5, 1.9, None, 1),
    ("L11850", "NMN", -85, 70.5, "SHED20250808153427", 1.5, 64, 21.5, 19.5, None, 1),
    ("L14909", "DCX", 300, 62, "SHED20250808153427", 1.5, 64, 6.5, 5, None, 1),
    ("L14825", "BCV", -150, 70, "SHED20250808153427", 1.5, 64, 10.8, 10.8, None, 1),
    ("L5116", "BCX", 162, 36, "SHED20250808173219", 1.5, 64, 3.8, 2.4, None, 1),
    ("big-PCX", "PCX", 185, 127, "ebay", 1.5, 64, 12, 4.7, None, 2),
    ("LCV5A5", "DCX", 130, 50, "amazon", 1.5, 64, 5.4, 1.2, None, 1),
    ("LIOOBO", "BCX", 100, 50, "amazon", 1.5, 64, 9.2, 4, None, 5),
    ("LCV706", "DCX", 150, 75, "amazon", 1.5, 64, 10.6, 2.7, None, 1),
    ("L4019", "NMN", -103, 52.7, "SHED20260408155927", 1.5, 64, 9.2, 9.2, None, 1),
    ("L14234", "NMN", -207, 56.4, "SHED20260408165410", 1.5, 64, 8.5, 7.7, None, 1),
    # Ed-31-795: N-BK7, n=1.5168, V=64.17
    ("Ed-31-795", "DCX", 147, 62, "E-2374828", 1.5168, 64.17, 9.1, 3.8, None, 1),
    # Ed-32-886: cemented doublet, crown N-BK7 (n=1.5168, V=64.17) + flint N-SF5 (n=1.6727, V=32.25)
    ("Ed-32-886", "ACH", 150, 50, "ebay", 1.5, 1000, 13.5, 9.5, None, 1),
    ("L7325", "NMN", -174, 52, "SHED2026040818400", 1.5, 64, 6.8, 6.2, None, 1),
    ("L11018", "NMN", -210, 40, "SHED2026040818400", 1.5, 64, 4.5, 4.3, None, 1),
    ("L5063", "DCV", -126, 55.5, "SHED20260410202712", 1.5, 64, 10.2, 10.2, None, 1),
    ("L5951", "NMN", -132, 53.5, "SHED20260410202712", 1.5, 64, 17.5, 12.7, None, 1),
    ("L8106", "NMN", -114, 48.5, "SHED20260410202712", 1.5, 64, 12, 8.5, 49.9, 1),
]


def init_db():
    """Create the database and populate it with the initial inventory."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript(SCHEMA)
    cur.executemany(
        "INSERT OR REPLACE INTO elements "
        "(code, type, focal_length, diameter, order_id, refractive_index, "
        " abbe_number, max_thickness, edge_thickness, mounted_od, qty) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        INITIAL_DATA,
    )
    con.commit()
    con.close()


def list_elements(type_filter=None, order_filter=None, include_non_purchased=False):
    """Print the inventory, optionally filtered."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    query = "SELECT * FROM elements"
    params = []
    clauses = []
    if not include_non_purchased:
        clauses.append("purchased = 1")
    if type_filter:
        clauses.append("type = ?")
        params.append(type_filter)
    if order_filter:
        clauses.append("order_id = ?")
        params.append(order_filter)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY type, focal_length"

    rows = cur.execute(query, params).fetchall()
    con.close()

    if not rows:
        print("No elements found.")
        return

    print(f"{'Code':<14} {'Type':<5} {'FL':>7} {'Diam':>6} {'mOD':>6} "
          f"{'maxT':>6} {'edgT':>6} {'qty':>3}  {'Order'}")
    print("-" * 90)
    for r in rows:
        mt = f"{r['max_thickness']:>6.1f}" if r['max_thickness'] is not None else "     -"
        et = f"{r['edge_thickness']:>6.1f}" if r['edge_thickness'] is not None else "     -"
        mo = f"{r['mounted_od']:>6.1f}" if r['mounted_od'] is not None else "     -"
        print(
            f"{r['code']:<14} {r['type']:<5} {r['focal_length']:>7.1f} "
            f"{r['diameter']:>6.1f} {mo} {mt} {et} {r['qty']:>3}  {r['order_id']}"
        )
    print(f"\n{len(rows)} element(s)")


def add_element(code, elem_type, focal_length, diameter, order_id,
                refractive_index=1.5, abbe_number=64,
                max_thickness=None, edge_thickness=None,
                mounted_od=None, qty=1):
    """Add a single element to the inventory."""
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO elements "
        "(code, type, focal_length, diameter, order_id, refractive_index, "
        " abbe_number, max_thickness, edge_thickness, mounted_od, qty) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (code, elem_type, focal_length, diameter, order_id, refractive_index,
         abbe_number, max_thickness, edge_thickness, mounted_od, qty),
    )
    con.commit()
    con.close()
    print(f"Added {code}")


def remove_element(code):
    """Remove an element by code."""
    con = sqlite3.connect(DB_PATH)
    cur = con.execute("DELETE FROM elements WHERE code = ?", (code,))
    con.commit()
    con.close()
    if cur.rowcount:
        print(f"Removed {code}")
    else:
        print(f"No element with code '{code}' found.")


def main():
    parser = argparse.ArgumentParser(description="Lens element inventory")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Create/reset the database with initial data")

    ls = sub.add_parser("list", help="List elements")
    ls.add_argument("-t", "--type", help="Filter by lens type (e.g. DCX, NMN)")
    ls.add_argument("-o", "--order", help="Filter by order ID")
    ls.add_argument("--include-non-purchased", action="store_true",
                    help="Include elements not yet purchased")

    add = sub.add_parser("add", help="Add an element")
    add.add_argument("code")
    add.add_argument("type")
    add.add_argument("focal_length", type=float)
    add.add_argument("diameter", type=float)
    add.add_argument("order_id")
    add.add_argument("-n", "--refractive-index", type=float, default=1.5,
                     help="Refractive index (default: 1.5)")
    add.add_argument("-v", "--abbe-number", type=float, default=64,
                     help="Abbe number (default: 64 for crown glass)")
    add.add_argument("--max-thickness", type=float, default=None,
                     help="Max thickness in mm (default: None)")
    add.add_argument("--edge-thickness", type=float, default=None,
                     help="Edge thickness in mm (default: None)")
    add.add_argument("--mounted-od", type=float, default=None,
                     help="Mounted outer diameter in mm; null = use diameter")
    add.add_argument("--qty", type=int, default=1,
                     help="Quantity of this element on hand (default: 1)")

    rm = sub.add_parser("rm", help="Remove an element by code")
    rm.add_argument("code")

    args = parser.parse_args()

    if args.command == "init":
        init_db()
        print(f"Database initialized with {len(INITIAL_DATA)} elements.")
    elif args.command == "list":
        list_elements(type_filter=args.type, order_filter=args.order,
                     include_non_purchased=args.include_non_purchased)
    elif args.command == "add":
        add_element(args.code, args.type, args.focal_length, args.diameter,
                   args.order_id, args.refractive_index, args.abbe_number,
                   args.max_thickness, args.edge_thickness,
                   args.mounted_od, args.qty)
    elif args.command == "rm":
        remove_element(args.code)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
