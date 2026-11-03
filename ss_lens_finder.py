#!/usr/bin/env python3
"""Query Surplus Shed's Lens Finder (search_lenses.php).

Normal mode: search by type + optional diameter / focal-length filters.
Result rows print to stdout as an aligned table; count summary to stderr.

--buy-link CODE mode: given a stock code, scan type listings until the
element is found and print a narrowly-scoped search URL that returns
just that element. Intended for sharing a direct link with a human.

Background quirks encoded here so callers don't have to remember them:

1. Surplus Shed's form uses an inverted from/to convention -- `from`
   must be numerically LARGER than `to`, backward from standard
   BETWEEN semantics. Rather than fight that for normal searches,
   this tool fetches the unfiltered `?type=X` listing and filters
   client-side. The inverted convention is only used when building
   --buy-link URLs, since those do need to hit the form.

2. Biconcave stock lives under type=DCV, not BCV -- the form has both
   as separate types but `type=BCV` returns essentially nothing while
   `type=DCV` returns the populated catalog (~130 rows). This tool
   auto-merges BCV and DCV whenever either is requested, so "give me
   biconcaves" Just Works regardless of which label you use.

3. Full type listings are a few hundred KB each and rarely change;
   they're cached in ~/.cache/ss_lens_finder/ with a 24h TTL. Use
   --no-cache to bypass.

4. Most stock items are priced at $4.50 -- it's essentially the
   catalog default. Prices outside that (typically $5.50, $7.50,
   occasionally higher) are worth noting: they often flag larger,
   higher-quality, or coated items that the shop considers premium.
   When surveying a type, sort by `price` to see which items are
   priced above the default.

5. Sanity-check row counts. If a bare `type=X` query returns zero or
   surprisingly few results for a major lens type, don't trust the
   result -- investigate. The two likely causes are: (a) the form
   naming has split the category under a different label (e.g. BCV
   vs DCV, handled above by expand_types), or (b) the site HTML has
   drifted and ROW_RE no longer matches. Compare against a known-
   populated type first before concluding a category is actually
   empty. The live NMN listing has ~400 rows, ACH ~150, DCV ~130;
   numbers an order of magnitude below those are a red flag.
"""

import argparse
import os
import re
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://www.surplusshed.com/search_lenses.php"
CACHE_DIR = os.path.expanduser("~/.cache/ss_lens_finder")
CACHE_TTL = 24 * 3600  # 24 hours

# Valid values for the form's `type` param.
TYPES = ["ACH", "ASP", "BCV", "BCX", "DCV", "DCX", "NMN", "PCV", "PCX",
         "PMN", "TRP"]

# Per-row extractor. Each result is a <tr> with cells in order:
# stock_code, type, diameter "N.N mm", focal "±N.N mm", coated, $price.
ROW_RE = re.compile(
    r'<tr>\s*<td>\s*(L\d{3,}[A-Z]*)\s*</td>'
    r'\s*<td>\s*(\w+)\s*</td>'
    r'\s*<td>\s*([-\d.]+)\s*mm\s*</td>'
    r'\s*<td>\s*([-\d.]+)\s*mm\s*</td>'
    r'\s*<td>\s*(\w+)\s*</td>'
    r'\s*<td>\s*\$?([\d.]+)',
    re.DOTALL,
)


def expand_types(types):
    """Expand a requested type list, merging BCV<->DCV in both directions.

    Either 'BCV' or 'DCV' in the input pulls both. Order is preserved
    and duplicates are removed.
    """
    out = []
    for t in types:
        t = t.strip().upper()
        if t in ("BCV", "DCV"):
            for x in ("BCV", "DCV"):
                if x not in out:
                    out.append(x)
        elif t not in out:
            out.append(t)
    return out


def fetch_type_listing(typ, *, use_cache=True):
    """Fetch the full `?type=X` listing with simple on-disk caching."""
    cache_path = None
    if use_cache:
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache_path = os.path.join(CACHE_DIR, f"{typ.lower()}.html")
        if os.path.exists(cache_path):
            age = time.time() - os.path.getmtime(cache_path)
            if age < CACHE_TTL:
                with open(cache_path) as f:
                    return f.read()

    url = BASE + "?" + urlencode({"type": typ})
    req = Request(url, headers={"User-Agent": "ss-lens-finder/1.0"})
    with urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    if cache_path:
        with open(cache_path, "w") as f:
            f.write(html)
    return html


def parse_rows(html):
    rows = []
    for m in ROW_RE.finditer(html):
        code, typ, dia, fl, coated, price = m.groups()
        rows.append({
            "code": code,
            "type": typ,
            "diameter": float(dia),
            "focal_length": float(fl),
            "coated": coated,
            "price": float(price),
        })
    return rows


def filter_rows(rows, *, dia_min=None, dia_max=None, fl_min=None, fl_max=None):
    def keep(r):
        if dia_min is not None and r["diameter"] < dia_min:
            return False
        if dia_max is not None and r["diameter"] > dia_max:
            return False
        if fl_min is not None and r["focal_length"] < fl_min:
            return False
        if fl_max is not None and r["focal_length"] > fl_max:
            return False
        return True
    return [r for r in rows if keep(r)]


def sort_rows(rows, key="fl", descending=False):
    keyfn = {
        "fl":    lambda r: r["focal_length"],
        "dia":   lambda r: r["diameter"],
        "type":  lambda r: (r["type"], r["focal_length"]),
        "price": lambda r: r["price"],
    }[key]
    return sorted(rows, key=keyfn, reverse=descending)


def format_table(rows):
    if not rows:
        return "(no results)\n"
    lines = [
        f"{'code':<10}  {'type':<4}  {'dia':>7}  {'fl':>8}  "
        f"{'coat':<5}  {'price':>7}",
        "-" * 52,
    ]
    for r in rows:
        lines.append(
            f"{r['code']:<10}  {r['type']:<4}  "
            f"{r['diameter']:>5.1f}mm  {r['focal_length']:>+6.1f}mm  "
            f"{r['coated']:<5}  ${r['price']:>5.2f}"
        )
    return "\n".join(lines) + "\n"


def build_buy_link(row):
    """Narrow search URL returning only this element.

    Uses the form's inverted from>to convention for both diameter and
    focal length. A +/-0.5mm diameter bracket and +/-1mm FL bracket is
    tight enough to isolate a single stock code in the vast majority
    of cases.
    """
    dia = row["diameter"]
    fl = row["focal_length"]
    params = {
        "type": row["type"],
        "diameter_from":     f"{dia + 0.5:g}",  # larger (form convention)
        "diameter_to":       f"{dia - 0.5:g}",  # smaller
        "focal_length_from": f"{fl + 1:g}",
        "focal_length_to":   f"{fl - 1:g}",
    }
    return BASE + "?" + urlencode(params)


def find_element(code, *, type_hint=None, use_cache=True):
    """Scan type listings for a stock code. Returns the row or None."""
    code_up = code.upper()
    if type_hint:
        types_to_scan = expand_types([type_hint])
    else:
        types_to_scan = TYPES
    for typ in types_to_scan:
        html = fetch_type_listing(typ, use_cache=use_cache)
        for r in parse_rows(html):
            if r["code"].upper() == code_up:
                return r
    return None


def main():
    p = argparse.ArgumentParser(
        description="Query Surplus Shed's Lens Finder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ss_lens_finder.py -t NMN --dia-min 45 --dia-max 55 "
            "--fl-min -130 --fl-max -70\n"
            "  ss_lens_finder.py -t ACH --dia-min 40 --sort dia\n"
            "  ss_lens_finder.py --buy-link L5063\n"
        ),
    )
    p.add_argument("-t", "--type",
                   help="Lens type (ACH, NMN, DCV, BCX, DCX, PCX, PCV, "
                        "PMN, TRP, ASP). Comma-separated for multiple. "
                        "BCV and DCV are auto-merged (surplusshed splits "
                        "biconcaves across both labels).")
    p.add_argument("--dia-min", type=float, help="Minimum diameter (mm)")
    p.add_argument("--dia-max", type=float, help="Maximum diameter (mm)")
    p.add_argument("--fl-min", type=float,
                   help="Minimum focal length (mm; can be negative)")
    p.add_argument("--fl-max", type=float,
                   help="Maximum focal length (mm; can be negative)")
    p.add_argument("--sort", choices=["fl", "dia", "type", "price"],
                   default="fl", help="Sort key (default: fl)")
    p.add_argument("--desc", action="store_true", help="Sort descending")
    p.add_argument("--no-cache", action="store_true",
                   help="Bypass local cache")
    p.add_argument("--buy-link", metavar="CODE",
                   help="Print a narrow search URL returning only this "
                        "stock code. If --type is also given, only that "
                        "listing is scanned (faster); otherwise all "
                        "types are scanned until found.")
    args = p.parse_args()

    use_cache = not args.no_cache

    if args.buy_link:
        row = find_element(args.buy_link, type_hint=args.type,
                           use_cache=use_cache)
        if row is None:
            scope = f" (in {args.type})" if args.type else ""
            sys.exit(f"error: stock code {args.buy_link} not found"
                     f"{scope}")
        print(build_buy_link(row))
        return

    if not args.type:
        p.error("--type is required for a search (or pass --buy-link CODE)")

    requested = [t.strip().upper() for t in args.type.split(",")]
    for t in requested:
        if t not in TYPES:
            sys.exit(f"error: unknown type '{t}' "
                     f"(valid: {', '.join(TYPES)})")
    types_to_query = expand_types(requested)

    rows = []
    for typ in types_to_query:
        rows.extend(parse_rows(fetch_type_listing(typ, use_cache=use_cache)))

    rows = filter_rows(
        rows,
        dia_min=args.dia_min, dia_max=args.dia_max,
        fl_min=args.fl_min, fl_max=args.fl_max,
    )
    rows = sort_rows(rows, key=args.sort, descending=args.desc)

    print(format_table(rows), end="")
    print(f"{len(rows)} result(s) "
          f"[types: {','.join(types_to_query)}]", file=sys.stderr)


if __name__ == "__main__":
    main()
