#!/usr/bin/env python3
"""Shared design-name resolution for the lens design tools.

Lets every CLI accept a short name (a unique prefix such as ``v13f``) in place
of the full stored name (``v13f-balanced-150mm``). Exact matches always win;
otherwise the name is treated as a prefix and must resolve to exactly one
distinct stored design name, else resolution raises.
"""

DESIGNS_DB = "lens_designs.db"


class DesignNameError(ValueError):
    """Raised when a design name cannot be resolved to a single design."""


def _like_prefix(text):
    """Escape LIKE wildcards in `text` and append `%` for a prefix match."""
    escaped = (text.replace("\\", "\\\\")
                   .replace("%", "\\%")
                   .replace("_", "\\_"))
    return escaped + "%"


def resolve_design_name(con, name):
    """Resolve a possibly-abbreviated design name to a full stored name.

    `con` is an open sqlite3 connection to the designs database. Returns the
    canonical stored name. Raises DesignNameError if nothing matches, or if a
    prefix is ambiguous across more than one distinct design name. Rows that
    merely repeat the same name (a design re-saved several times) are not
    ambiguous -- the caller's own ``ORDER BY id DESC LIMIT 1`` still picks the
    newest.
    """
    if con.execute("SELECT 1 FROM designs WHERE name = ? LIMIT 1",
                   (name,)).fetchone():
        return name
    matches = [r[0] for r in con.execute(
        "SELECT DISTINCT name FROM designs WHERE name LIKE ? ESCAPE '\\' "
        "ORDER BY name", (_like_prefix(name),))]
    if not matches:
        raise DesignNameError(f"no design matching '{name}'")
    if len(matches) > 1:
        shown = ", ".join(matches[:8]) + (" ..." if len(matches) > 8 else "")
        raise DesignNameError(
            f"design name '{name}' is ambiguous; matches "
            f"{len(matches)}: {shown}")
    return matches[0]
