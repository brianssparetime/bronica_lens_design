#!/usr/bin/env python3
"""Emit a small optical diagram (cross-section) for a saved lens design.

Reads a design from lens_designs.db and the element geometry from
inventory.db, then draws a to-scale side view of the optical train: each
element as a filled lens cross-section, the aperture stop, and the optical
axis. Element diameters, element center-to-center spacings, and the stop
spacing are all rendered to a single common scale so they are correct
relative to one another.

Element shapes are informed by the thicknesses we have (max_thickness as the
center thickness, edge_thickness as the rim) when those carry curvature
information. Where they do not -- biconcave, meniscus, plano-concave, whose
center thickness is not recorded -- a generic representative shape for that
element type is used instead. The result is schematic, not a ray-trace, but
proportionally faithful.

Conventions (shared with spacing_diagram.py):
  - spacing_after on a design element is the center-to-center distance to the
    next element, working from the film plane outward.
  - stop_after = 0    -> stop sits in front of element 1
    stop_after = N>=1 -> stop sits in the gap behind element N (toward film)
    stop_spacing      -> element-N rear face to stop front face (or stop rear
                         face to element-1 front face when N = 0)

Output is an SVG of filled, outlined paths by default; --format png or jpg
rasterizes the same geometry with Pillow.

Light is drawn entering from the left, so the front element is on the left and
the film plane is on the right.
"""

import argparse
import math
import sqlite3
import sys

from design_db import resolve_design_name, DesignNameError

DESIGNS_DB = "lens_designs.db"
INVENTORY_DB = "inventory.db"

# Geometry constants, matching spacing_diagram.py / evaluate.py.
STOP_THICKNESS = 2.0       # mm; physical thickness of the stop blade in air
HEAVY_LINE_MM = STOP_THICKNESS / 2.0   # printable weight for the axis + cement line
FLANGE_DISTANCE = 101.7    # mm from film plane (flange plane, z = 0)

DEFAULT_CENTER_THK = 10.0  # fallback center thickness when max_thickness NULL
DEFAULT_EDGE_THK = 2.0     # fallback rim thickness when edge_thickness NULL

# Concave / convex shape families by element type code.
CONVEX_TYPES = {"DCX", "BCX", "PCX", "ACH", "PMN"}
CONCAVE_TYPES = {"DCV", "BCV", "PCV"}
MENISCUS_TYPES = {"NMN", "PMN"}

# Colors.
GLASS_FILL = "#bcd6e6"
GLASS_STROKE = "#2c5d7c"
STOP_FILL = "#222222"
AXIS_COLOR = "#cc4444"
REF_COLOR = "#888888"
TEXT_COLOR = "#222222"
SUBTEXT_COLOR = "#555555"

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica.ttf",
    "/Library/Fonts/Arial.ttf",
]


# --------------------------------------------------------------------------
# Database access
# --------------------------------------------------------------------------

def get_design(name):
    """Look up a design by name (short prefixes allowed), newest first."""
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
        sys.exit(f"Error: no design matching '{name}' found.")
    design = dict(row)
    elems = con.execute(
        "SELECT element_code, spacing_after, orientation FROM design_elements "
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


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def surface_sags(etype, r, tc, te, cx_front, fl=0.0):
    """Per-surface sag (sf, sb) and drawn edge thickness for one element.

    sf > 0 means the front (left) surface is convex (its vertex sits left of
    the rim); sf < 0 means concave. Likewise sb for the back (right) surface,
    positive bulging right. The drawn center thickness works out to
    te + sf + sb, so for convex glass (where max/edge thickness carry the
    curvature) the silhouette matches the catalog numbers; for the families
    whose center thickness is not recorded a generic representative sag is
    used instead.

    cx_front chooses which way the curved/convex/crown surface faces: True
    toward the object (front), False toward the camera (image). This comes
    from design_elements.orientation; when that is NULL the caller defaults to
    True, matching the curved-side-front convention evaluate.py commits to for
    PCX/PCV (q = +1, the natural orientation for a camera lens at infinity).
    For meniscus and achromat elements the design model leaves bending
    undetermined (q = 0), so the orientation is the recorded build intent
    rather than something derivable from (f, n, t).

    fl is the element focal length; its sign selects a converging or diverging
    silhouette for an achromat, so a negative achromat is drawn diverging
    rather than as a fat positive doublet.
    """
    delta = tc - te                 # center-minus-edge; meaningful for convex
    vmin = 0.05 * r                 # minimum visible bulge
    cm = min(0.12 * r, 8.0)         # generic meniscus crescent curvature
    # generic concave dip, kept from eating the whole element
    gd = min(max(0.16 * r, 1.5), max(te - 0.8, 0.8))

    if etype in MENISCUS_TYPES:
        # Crescent. Drawn center thickness stays te + cm + (delta - cm) = tc
        # whichever way it faces.
        if cx_front:
            return cm, delta - cm, te          # convex front, concave back
        return delta - cm, cm, te              # concave front, convex back

    if etype in CONCAVE_TYPES:
        if etype == "PCV":          # plano-concave: concave face / flat face
            return (-gd, 0.0, te) if cx_front else (0.0, -gd, te)
        return -gd / 2.0, -gd / 2.0, te    # biconcave (symmetric)

    # Convex families.
    if etype == "ACH":
        flat = 0.04 * r
        if fl < 0.0:
            # Negative (diverging) achromat: its net power sits in the internal
            # cement surface, not the outer envelope, so the catalog center
            # thickness does not reveal it (it can even read center-thick).
            # Draw a generic diverging silhouette -- strong concave on the
            # cx_front side, near-flat flint on the other -- so the doublet
            # reads as diverging rather than as a fat positive lens.
            return (-gd, flat, te) if cx_front else (flat, -gd, te)
        # Positive (converging) achromat: thick, strongly curved crown on the
        # cx_front side, thin nearly flat flint on the other.
        crown = max(delta - flat, 0.16 * r)
        return (crown, flat, te) if cx_front else (flat, crown, te)
    if etype == "PCX":              # plano-convex: convex face / flat face
        s = max(delta, vmin)
        return (s, 0.0, te) if cx_front else (0.0, s, te)
    # DCX / BCX: symmetric biconvex outline.
    s = delta / 2.0 if delta > 2 * vmin else vmin
    return s, s, te


def _bezier(p0, pc, p1, n=44):
    """Sample a quadratic bezier p0 -> p1 with control pc into n+1 points."""
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        pts.append((mt * mt * p0[0] + 2 * mt * t * pc[0] + t * t * p1[0],
                    mt * mt * p0[1] + 2 * mt * t * pc[1] + t * t * p1[1]))
    return pts


def lens_surfaces(xc, r, te, sf, sb, n=44):
    """Front and back surface point lists for one element centered at xc.

    Front rim at xc - te/2, back rim at xc + te/2; surface vertices offset by
    the sags. Each surface is a quadratic bezier through (rim, vertex, rim).
    front runs top -> bottom, back runs bottom -> top, so front + back is a
    closed outline traversed in order.
    """
    x_fe = xc - te / 2.0            # front rim x
    x_be = xc + te / 2.0            # back rim x
    x_fv = x_fe - sf               # front vertex x
    x_bv = x_be + sb               # back vertex x
    front = _bezier((x_fe, r), (2 * x_fv - x_fe, 0.0), (x_fe, -r), n)
    back = _bezier((x_be, -r), (2 * x_bv - x_be, 0.0), (x_be, r), n)
    return front, back


def lens_outline(xc, r, tc, te, sf, sb, n=44):
    """Closed polygon (list of (x, y)) for one element centered at xc."""
    front, back = lens_surfaces(xc, r, te, sf, sb, n)
    return front + back


def achromat_split(xc, r, te, sf, sb, gap, n=44):
    """Crown and flint halves of a cemented doublet, split by a straight
    vertical cement plane with a thin GAP between them, the plane offset toward
    the less-curved (camera-facing) side.

    The gap is UNFILLED so the cement reads as a line in a downstream tool that
    turns each filled path into a solid cutout: a colored line drawn over a
    solid element merges into the cutout and vanishes, but two solid halves
    separated by a gap leave the cement plane as visible empty space.
    """
    front, back = lens_surfaces(xc, r, te, sf, sb, n)
    off = 0.25 * te * (1.0 if abs(sf) >= abs(sb) else -1.0)
    xcem = xc + off
    xl, xr = xcem - gap / 2.0, xcem + gap / 2.0
    crown = front + [(xl, -r), (xl, r)]     # front surface + cement edge (b->t)
    flint = [(xr, r), (xr, -r)] + back      # cement edge (t->b) + back surface
    return crown, flint


def build_geometry(design, elem_info):
    """Compute world-space (mm, y-up) positions for every drawn feature.

    Returns a dict with element records, optional stop record, and overall
    z-range. World x is set to -z so that the front element (largest z, i.e.
    farthest from the film) lands on the left and the film plane on the right.
    """
    codes = [e["element_code"] for e in design["elements"]]
    orientations = [e.get("orientation") for e in design["elements"]]
    spacings = [
        e["spacing_after"] for e in design["elements"]
        if e["spacing_after"] is not None
    ]
    n = len(codes)

    # Center thickness used for face math (matches spacing_diagram.py).
    tc_face = []
    for code in codes:
        t = elem_info[code]["max_thickness"]
        tc_face.append(t if t is not None else DEFAULT_CENTER_THK)

    # Element center z, from the film plane outward.  bfd is rear-vertex-to-
    # film, so the rear element's center sits half a thickness farther out;
    # inf_offset shifts the whole core relative to the film (+ away, - toward).
    bfd = design["bfd"]
    inf_offset = design.get("inf_offset") or 0.0
    z = [0.0] * n
    z[n - 1] = bfd + tc_face[n - 1] / 2.0 + inf_offset
    for k in range(n - 2, -1, -1):
        z[k] = z[k + 1] + spacings[k]

    elements = []
    for k, code in enumerate(codes):
        info = elem_info[code]
        d = info["diameter"]
        r = d / 2.0
        te = info["edge_thickness"]
        tc = info["max_thickness"]
        te = te if te is not None else DEFAULT_EDGE_THK
        tc = tc if tc is not None else te + DEFAULT_CENTER_THK
        etype = info["type"]
        # Curved side faces the object unless the design records CX_REAR.
        cx_front = orientations[k] != "CX_REAR"
        sf, sb, te_draw = surface_sags(etype, r, tc, te, cx_front,
                                       info["focal_length"])
        xc = -z[k]
        elements.append({
            "code": code,
            "type": etype,
            "fl": info["focal_length"],
            "diameter": d,
            "r": r,
            "tc": tc,
            "te": te_draw,
            "sf": sf,
            "sb": sb,
            "xc": xc,
            "z": z[k],
        })

    # Stop placement (replicates spacing_diagram.py).
    stop = None
    stop_after = design.get("stop_after")
    stop_spacing = design.get("stop_spacing")
    if stop_after is not None and stop_spacing is not None:
        if stop_after == 0:
            elem1_front = z[0] + tc_face[0] / 2.0
            stop_rear = elem1_front + stop_spacing
            stop_front = stop_rear + STOP_THICKNESS
            neighbors = [elements[0]["r"]]
        else:
            i = stop_after - 1
            elemN_rear = z[i] - tc_face[i] / 2.0
            stop_front = elemN_rear - stop_spacing
            stop_rear = stop_front - STOP_THICKNESS
            neighbors = [elements[i]["r"]]
            if i + 1 < n:
                neighbors.append(elements[i + 1]["r"])
        stop_center_z = (stop_front + stop_rear) / 2.0
        stop = {
            "z": stop_center_z,
            "xc": -stop_center_z,
            "min_neighbor_r": min(neighbors),
        }

    return {
        "codes": codes,
        "elements": elements,
        "stop": stop,
        "z_front": z[0] + elements[0]["tc"] / 2.0,
        "z_film": 0.0,
    }


# --------------------------------------------------------------------------
# Drawing primitives (world coords, mm, y-up)
# --------------------------------------------------------------------------

def assemble_primitives(geom, design, args):
    """Build the backend-agnostic primitive list and the world bounding box."""
    elements = geom["elements"]
    stop = geom["stop"]
    max_r = max(e["r"] for e in elements)
    env = max_r * 1.12 + 2.0       # stop bars / axis vertical envelope

    simple = args.simple
    if simple:
        # Clean line-art: white-filled, heavier uniform black outlines, no
        # text. White fill keeps every element a real outlined+filled path
        # (per the SVG spec) while reading as line art on the white ground.
        glass_fill, glass_stroke, glass_sw = "white", "#000000", 0.9
        axis_color, axis_sw, axis_dash = "#000000", HEAVY_LINE_MM, None
        show_labels = False
    else:
        glass_fill, glass_stroke, glass_sw = GLASS_FILL, GLASS_STROKE, 0.45
        axis_color, axis_sw, axis_dash = AXIS_COLOR, HEAVY_LINE_MM, [4, 2, 1, 2]
        show_labels = not args.no_labels

    prims = []

    # Optical axis (drawn first, behind the glass). Light travels left to
    # right (object to camera), so in --simple the right end carries an arrow.
    xs = [e["xc"] - e["te"] for e in elements] + [e["xc"] + e["te"] for e in elements]
    if stop:
        xs.append(stop["xc"])
    if args.refs:
        xs += [-FLANGE_DISTANCE, -geom["z_film"]]
    axis_x0 = min(xs) - 6
    axis_x1 = max(xs) + 6
    prims.append({"kind": "line", "p1": (axis_x0, 0.0), "p2": (axis_x1, 0.0),
                  "stroke": axis_color, "sw": axis_sw, "dash": axis_dash})
    if simple:
        a, b = 5.0, 3.0           # arrowhead reach and half-width
        prims.append({"kind": "poly",
                      "pts": [(axis_x1, 0.0), (axis_x1 - a, b), (axis_x1 - a, -b)],
                      "fill": axis_color, "stroke": axis_color, "sw": 0.0})

    # Optional reference planes (film, flange) -- physically far to the right.
    if args.refs:
        for zref, label in [(0.0, "film plane"),
                            (FLANGE_DISTANCE, "flange")]:
            xr = -zref
            prims.append({"kind": "line", "p1": (xr, env), "p2": (xr, -env),
                          "stroke": REF_COLOR, "sw": 0.4, "dash": [3, 3]})
            if show_labels:
                prims.append({"kind": "text", "x": xr, "y": -env - 1,
                              "s": label, "size": 2.6, "h": "middle",
                              "v": "top", "fill": SUBTEXT_COLOR})

    # Elements.
    for e in elements:
        if e["type"] == "ACH":
            # Cemented doublet: two solid halves split by a straight cement
            # plane with a printable-width gap, so the cement line survives a
            # path-to-solid-cutout importer (see achromat_split).
            for half in achromat_split(e["xc"], e["r"], e["te"],
                                       e["sf"], e["sb"], HEAVY_LINE_MM):
                prims.append({"kind": "poly", "pts": half, "fill": glass_fill,
                              "stroke": glass_stroke, "sw": glass_sw})
        else:
            pts = lens_outline(e["xc"], e["r"], e["tc"], e["te"],
                               e["sf"], e["sb"])
            prims.append({"kind": "poly", "pts": pts, "fill": glass_fill,
                          "stroke": glass_stroke, "sw": glass_sw})

        if show_labels:
            ytxt = -(e["r"] + 2.0)
            prims.append({"kind": "text", "x": e["xc"], "y": ytxt,
                          "s": e["code"], "size": 3.0, "h": "middle",
                          "v": "top", "fill": TEXT_COLOR})
            sub = f"{e['type']}  f={e['fl']:+.0f}"
            prims.append({"kind": "text", "x": e["xc"], "y": ytxt - 3.6,
                          "s": sub, "size": 2.5, "h": "middle",
                          "v": "top", "fill": SUBTEXT_COLOR})
            prims.append({"kind": "text", "x": e["xc"], "y": e["r"] + 1.0,
                          "s": f"d={e['diameter']:.0f}", "size": 2.4,
                          "h": "middle", "v": "bottom", "fill": SUBTEXT_COLOR})

    # Aperture stop. Filled blades by default; short ticks in --simple.
    if stop:
        if args.aperture is not None:
            ap_r = args.aperture / 2.0
        else:
            ap_r = 0.6 * stop["min_neighbor_r"]
        xc = stop["xc"]
        if simple:
            tick = max(6.0, 0.45 * max_r)
            hw = glass_sw / 2.0      # thin filled tick, drawn as a path
            for y0, y1 in [(ap_r, ap_r + tick), (-(ap_r + tick), -ap_r)]:
                prims.append({"kind": "poly",
                              "pts": [(xc - hw, y0), (xc + hw, y0),
                                      (xc + hw, y1), (xc - hw, y1)],
                              "fill": glass_stroke, "stroke": glass_stroke,
                              "sw": 0.0})
        else:
            hw = STOP_THICKNESS / 2.0
            for y0, y1 in [(ap_r, env), (-env, -ap_r)]:
                prims.append({"kind": "poly",
                              "pts": [(xc - hw, y0), (xc + hw, y0),
                                      (xc + hw, y1), (xc - hw, y1)],
                              "fill": STOP_FILL, "stroke": STOP_FILL, "sw": 0.0})
        if show_labels:
            prims.append({"kind": "text", "x": xc, "y": env + 1.0,
                          "s": "stop", "size": 2.6, "h": "middle",
                          "v": "bottom", "fill": TEXT_COLOR})

    # No title/caption is drawn: the output carries only the optical plan
    # diagram (glass, stop, axis, and per-element labels).  A caption header
    # would also balloon the canvas well past the drawing; the design name and
    # specs live in the console summary and the filename.

    # Bounding box over the inked PATH geometry only -- glass, stop, axis.
    # The SVG's consumer is a path-filling importer that ignores <text>, so the
    # element labels are kept for on-screen viewing but must NOT drive the
    # canvas: sizing the canvas to the geometry the importer actually consumes
    # keeps the declared bounds equal to the imported drawing.
    xmin = xmax = ymin = ymax = None
    for p in prims:
        if p["kind"] in ("poly", "polyline"):
            coords = p["pts"]
        elif p["kind"] == "line":
            coords = [p["p1"], p["p2"]]
        else:
            continue
        for (x, y) in coords:
            xmin = x if xmin is None else min(xmin, x)
            xmax = x if xmax is None else max(xmax, x)
            ymin = y if ymin is None else min(ymin, y)
            ymax = y if ymax is None else max(ymax, y)

    return prims, (xmin, ymin, xmax, ymax)


def dash_segments(p1, p2, pattern):
    """Split a straight line into solid dash segments for raster backends."""
    (x1, y1), (x2, y2) = p1, p2
    length = math.hypot(x2 - x1, y2 - y1)
    if length == 0 or not pattern:
        return [(p1, p2)]
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    segs = []
    pos = 0.0
    i = 0
    draw = True
    while pos < length:
        seg = pattern[i % len(pattern)]
        end = min(pos + seg, length)
        if draw:
            segs.append(((x1 + ux * pos, y1 + uy * pos),
                         (x1 + ux * end, y1 + uy * end)))
        pos = end
        draw = not draw
        i += 1
    return segs


# --------------------------------------------------------------------------
# Stroke-to-path helpers (for filled-geometry SVG)
# --------------------------------------------------------------------------

def _norm(dx, dy):
    """Left-hand unit normal of a direction vector; (0,0) -> (0,0)."""
    length = math.hypot(dx, dy)
    if length == 0:
        return 0.0, 0.0
    return -dy / length, dx / length


def offset_closed(pts, width):
    """Outer and inner rings of a thin band centered on a closed contour.

    Returns (outer, inner) point lists offset by +/- width/2 along the local
    normal at each vertex (cyclic). Emitting the two rings with opposite
    winding leaves the contour's interior as a hole under both the even-odd
    and nonzero rules -- this is "stroke to path" for a closed outline.
    """
    m = len(pts)
    h = width / 2.0
    outer, inner = [], []
    for i in range(m):
        x0, y0 = pts[(i - 1) % m]
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % m]
        nx, ny = _norm(x2 - x0, y2 - y0)
        outer.append((x1 + h * nx, y1 + h * ny))
        inner.append((x1 - h * nx, y1 - h * ny))
    return outer, inner


def ribbon_open(pts, width):
    """A closed contour outlining a thin band along an open polyline.

    The two offset sides are joined end to end into one filled region -- the
    filled-geometry equivalent of stroking the polyline.
    """
    m = len(pts)
    h = width / 2.0
    left, right = [], []
    for i in range(m):
        if i == 0:
            dx, dy = pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]
        elif i == m - 1:
            dx, dy = pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1]
        else:
            dx, dy = pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]
        nx, ny = _norm(dx, dy)
        left.append((pts[i][0] + h * nx, pts[i][1] + h * ny))
        right.append((pts[i][0] - h * nx, pts[i][1] - h * ny))
    return left + list(reversed(right))


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------

def render_svg(prims, bbox, scale):
    """Render filled-geometry SVG for import into a 2D-solids pipeline.

    The consumer fills closed paths by winding rule and ignores strokes, fill
    color, and opacity, so this emits ONLY closed filled paths: solid regions
    stay solid, every outline/line is converted to a filled ribbon (stroke to
    path), interiors become opposite-wound holes, and there is no background
    rectangle (it would import as a covering slab). Text labels are kept as
    <text> for on-screen viewing; a path-filling importer ignores them.
    """
    xmin, ymin, xmax, ymax = bbox
    # Hug the drawing: ~3% of the long side per edge (<=6% total), so the
    # canvas bounds stay close to the inked content for downstream sizing.
    margin = max(0.5, 0.03 * max(xmax - xmin, ymax - ymin))
    w = (xmax - xmin) + 2 * margin
    h = (ymax - ymin) + 2 * margin

    def c(x, y):                    # world -> canvas (mm, y-down)
        return (round(x - xmin + margin, 3), round(ymax - y + margin, 3))

    def ring_d(ring):
        return "M " + " L ".join(f"{c(x, y)[0]},{c(x, y)[1]}" for x, y in ring) + " Z"

    def fill_path(rings, color):
        d = " ".join(ring_d(r) for r in rings)
        out.append(f'<path d="{d}" fill="{color}" fill-rule="evenodd"/>')

    out = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{w * scale:.0f}" height="{h * scale:.0f}" '
        f'viewBox="0 0 {w:.2f} {h:.2f}" '
        f'font-family="Arial, Helvetica, sans-serif">')

    for p in prims:
        if p["kind"] == "poly":
            fill = p.get("fill")
            if fill and fill not in ("none", "white"):
                fill_path([p["pts"]], fill)          # solid region
            else:                                    # outline -> filled ribbon
                outer, inner = offset_closed(p["pts"], p["sw"] or 0.3)
                fill_path([outer, list(reversed(inner))],
                          p.get("stroke") or "#000000")
        elif p["kind"] == "polyline":
            fill_path([ribbon_open(p["pts"], p["sw"] or 0.3)],
                      p.get("stroke") or "#000000")
        elif p["kind"] == "line":
            ink = p.get("stroke") or "#000000"
            for a, b in dash_segments(p["p1"], p["p2"], p.get("dash")):
                fill_path([ribbon_open([a, b], p["sw"] or 0.3)], ink)
        elif p["kind"] == "text":
            x, y = c(p["x"], p["y"])
            anchor = {"start": "start", "middle": "middle", "end": "end"}[p["h"]]
            baseline = {"top": "hanging", "middle": "middle", "bottom": "alphabetic"}[p["v"]]
            s = (p["s"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            out.append(f'<text x="{x}" y="{y}" font-size="{p["size"]}" '
                       f'text-anchor="{anchor}" dominant-baseline="{baseline}" '
                       f'fill="{p["fill"]}">{s}</text>')
    out.append("</svg>")
    return "\n".join(out)


def render_raster(prims, bbox, scale, fmt):
    from PIL import Image, ImageDraw, ImageFont

    xmin, ymin, xmax, ymax = bbox
    margin = max(0.5, 0.03 * max(xmax - xmin, ymax - ymin))
    w_mm = (xmax - xmin) + 2 * margin
    h_mm = (ymax - ymin) + 2 * margin
    W = max(1, int(round(w_mm * scale)))
    H = max(1, int(round(h_mm * scale)))

    bg = "white" if fmt == "png" else (255, 255, 255)
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)

    def c(x, y):                    # world -> pixels
        return ((x - xmin + margin) * scale, (ymax - y + margin) * scale)

    font_path = next((p for p in FONT_CANDIDATES if _exists(p)), None)

    def font(size_mm):
        px = max(6, int(round(size_mm * scale)))
        if font_path:
            try:
                return ImageFont.truetype(font_path, px)
            except OSError:
                pass
        return ImageFont.load_default()

    for p in prims:
        if p["kind"] == "poly":
            xy = [c(x, y) for x, y in p["pts"]]
            if p["fill"] and p["fill"] != "none":
                draw.polygon(xy, fill=p["fill"])
            if p["sw"]:
                draw.line(xy + [xy[0]], fill=p["stroke"],
                          width=max(1, int(round(p["sw"] * scale))), joint="curve")
        elif p["kind"] == "polyline":
            xy = [c(x, y) for x, y in p["pts"]]
            draw.line(xy, fill=p["stroke"],
                      width=max(1, int(round(p["sw"] * scale))), joint="curve")
        elif p["kind"] == "line":
            wpx = max(1, int(round(p["sw"] * scale)))
            segs = dash_segments(p["p1"], p["p2"], p.get("dash"))
            for a, b in segs:
                draw.line([c(*a), c(*b)], fill=p["stroke"], width=wpx)
        elif p["kind"] == "text":
            x, y = c(p["x"], p["y"])
            anchor_h = {"start": "l", "middle": "m", "end": "r"}[p["h"]]
            anchor_v = {"top": "a", "middle": "m", "bottom": "s"}[p["v"]]
            draw.text((x, y), p["s"], font=font(p["size"]), fill=p["fill"],
                      anchor=anchor_h + anchor_v)

    return img


def _exists(path):
    import os
    return os.path.exists(path)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Emit a small, to-scale optical diagram for a saved lens "
                    "design (SVG by default; PNG/JPG via --format).",
    )
    parser.add_argument("-f", "--format", choices=["svg", "png", "jpg"],
                        default="svg", help="Output format (default: svg).")
    parser.add_argument("-o", "--output",
                        help="Output file path (default: <design>.<format>).")
    parser.add_argument("-s", "--scale", type=float, default=5.0,
                        help="Pixels per mm for raster output and for the SVG "
                             "pixel size (default: 5.0).")
    parser.add_argument("--aperture", type=float, default=None,
                        help="Stop aperture DIAMETER in mm (schematic; default "
                             "is derived from the neighboring elements).")
    parser.add_argument("--simple", action="store_true",
                        help="Clean line-art style: black outlines, no fills "
                             "or labels, a tick-mark stop, and an arrow on the "
                             "optical axis showing the direction of light "
                             "travel (toward the camera).")
    parser.add_argument("--refs", action="store_true",
                        help="Also draw the film plane and flange reference "
                             "planes to scale (makes the diagram much wider).")
    parser.add_argument("--no-labels", action="store_true",
                        help="Suppress all text labels.")
    parser.add_argument("name", help="Design name (exact, or a substring).")
    args = parser.parse_args()

    design = get_design(args.name)
    codes = [e["element_code"] for e in design["elements"]]
    if not codes:
        sys.exit(f"Error: design '{design['name']}' has no elements.")
    elem_info = get_element_info(codes)

    geom = build_geometry(design, elem_info)
    prims, bbox = assemble_primitives(geom, design, args)

    out_path = args.output or f"{design['name']}.{args.format}"

    if args.format == "svg":
        svg = render_svg(prims, bbox, args.scale)
        with open(out_path, "w") as fh:
            fh.write(svg)
    else:
        img = render_raster(prims, bbox, args.scale, args.format)
        img.save(out_path, "JPEG" if args.format == "jpg" else "PNG")

    train = " / ".join(codes)
    inf_offset = design.get("inf_offset") or 0.0
    inf_note = (f"  [inf_offset {inf_offset:+.1f}mm applied]"
                if abs(inf_offset) > 1e-9 else "")
    sys.stderr.write(
        f"Wrote {out_path}: {design['name']} [{train}]"
        f"  EFL {design['efl']:.1f}mm  f/{design['f_number']:.1f}{inf_note}\n")


if __name__ == "__main__":
    main()
