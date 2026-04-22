"""
Geometry section builders for each supported Visio shape type.

All functions receive (width, height) in **inches** and return a list of
row descriptors that vsdx_builder.py turns into <Row> XML elements inside
a <Section N="Geometry"> block.

Each row descriptor is a dict with:
    {"type": "MoveTo"|"LineTo"|"ArcTo"|"Ellipse"|"EllipticalArcTo",
     "x": ..., "y": ...,                 # always present
     "a": ..., "b": ...,                 # ArcTo bow; Ellipse rx, ry
     "c": ..., "d": ..., "e": ...}       # EllipticalArcTo fields
"""

from __future__ import annotations

import math
from typing import Any

Row = dict[str, Any]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_geometry(shape_type: str, w: float, h: float) -> list[Row]:
    """Return geometry rows for *shape_type* with dimensions (w × h) inches."""
    builders = {
        "rectangle": rect_geometry,
        "ellipse": ellipse_geometry,
        "diamond": diamond_geometry,
        "parallelogram": parallelogram_geometry,
        "triangle": triangle_geometry,
        "cylinder": cylinder_geometry,
        "hexagon": hexagon_geometry,
    }
    builder = builders.get(shape_type, rect_geometry)
    return builder(w, h)


# ---------------------------------------------------------------------------
# Shape builders
# ---------------------------------------------------------------------------

def rect_geometry(w: float, h: float) -> list[Row]:
    """Closed rectangle: bottom-left origin, Visio coordinate system."""
    return [
        {"type": "MoveTo", "x": 0.0, "y": 0.0},
        {"type": "LineTo", "x": w,   "y": 0.0},
        {"type": "LineTo", "x": w,   "y": h},
        {"type": "LineTo", "x": 0.0, "y": h},
        {"type": "LineTo", "x": 0.0, "y": 0.0},
    ]


def ellipse_geometry(w: float, h: float) -> list[Row]:
    """Full ellipse using Visio's Ellipse row type.

    Visio Ellipse row: (cx, cy, rx1, ry1)  — one point on the x-axis + one on y-axis.
    """
    cx, cy = w / 2, h / 2
    return [
        {"type": "MoveTo",  "x": cx, "y": 0.0},
        {"type": "Ellipse", "x": cx, "y": h,
         "a": w,   "b": cy,    # rightmost point X, centreY
         "c": 0.0, "d": cy},   # leftmost point X, centreY
    ]


def diamond_geometry(w: float, h: float) -> list[Row]:
    """4-pointed diamond (rhombus)."""
    mx, my = w / 2, h / 2
    return [
        {"type": "MoveTo", "x": mx,  "y": 0.0},
        {"type": "LineTo", "x": w,   "y": my},
        {"type": "LineTo", "x": mx,  "y": h},
        {"type": "LineTo", "x": 0.0, "y": my},
        {"type": "LineTo", "x": mx,  "y": 0.0},
    ]


def parallelogram_geometry(w: float, h: float) -> list[Row]:
    """Parallelogram with a 20 % horizontal offset at the top."""
    offset = w * 0.2
    return [
        {"type": "MoveTo", "x": offset,    "y": 0.0},
        {"type": "LineTo", "x": w,         "y": 0.0},
        {"type": "LineTo", "x": w - offset,"y": h},
        {"type": "LineTo", "x": 0.0,       "y": h},
        {"type": "LineTo", "x": offset,    "y": 0.0},
    ]


def triangle_geometry(w: float, h: float) -> list[Row]:
    """Isosceles triangle pointing up."""
    return [
        {"type": "MoveTo", "x": w / 2, "y": h},   # apex (top in Visio coords: y=h)
        {"type": "LineTo", "x": w,     "y": 0.0},
        {"type": "LineTo", "x": 0.0,   "y": 0.0},
        {"type": "LineTo", "x": w / 2, "y": h},
    ]


def cylinder_geometry(w: float, h: float) -> list[Row]:
    """Cylinder: rectangle body with elliptical top and bottom caps.

    Approximated with LineTo + ArcTo rows.
    cap_h is the height of the elliptical cap.
    """
    cap_h = min(h * 0.15, 0.15)  # cap height in inches, max 0.15"
    bow = cap_h / 2               # ArcTo bow = half the sagitta

    return [
        # Start at bottom-left of top cap
        {"type": "MoveTo", "x": 0.0, "y": h - cap_h},
        # Top cap (upper half-ellipse, drawn left→right)
        {"type": "ArcTo",  "x": w,   "y": h - cap_h, "a": -bow},
        # Right side down
        {"type": "LineTo", "x": w,   "y": cap_h},
        # Bottom cap (lower half-ellipse, drawn right→left)
        {"type": "ArcTo",  "x": 0.0, "y": cap_h,     "a": -bow},
        # Left side up (close)
        {"type": "LineTo", "x": 0.0, "y": h - cap_h},
        # Top cap ellipse (full, drawn as second geometry to show the rim)
        {"type": "MoveTo", "x": 0.0, "y": h - cap_h},
        {"type": "ArcTo",  "x": w,   "y": h - cap_h, "a": bow},
        {"type": "ArcTo",  "x": 0.0, "y": h - cap_h, "a": bow},
    ]


def hexagon_geometry(w: float, h: float) -> list[Row]:
    """Regular hexagon (flat-top orientation)."""
    # x offsets for flat-top hexagon
    qw = w / 4
    return [
        {"type": "MoveTo", "x": qw,       "y": 0.0},
        {"type": "LineTo", "x": w - qw,   "y": 0.0},
        {"type": "LineTo", "x": w,        "y": h / 2},
        {"type": "LineTo", "x": w - qw,   "y": h},
        {"type": "LineTo", "x": qw,       "y": h},
        {"type": "LineTo", "x": 0.0,      "y": h / 2},
        {"type": "LineTo", "x": qw,       "y": 0.0},
    ]


# ---------------------------------------------------------------------------
# Connector geometry helper
# ---------------------------------------------------------------------------

def connector_geometry(
    bx: float, by: float,
    ex: float, ey: float,
    waypoints: list[tuple[float, float]],
) -> list[Row]:
    """Return geometry rows for a connector (all coords already in inches,
    relative to the connector's own bounding box origin).

    For connectors, Visio uses absolute coordinates stored in BeginX/BeginY and
    EndX/EndY cells; the Geometry section defines the path as a sequence of
    LineTo rows using *page-level* coordinates.
    """
    rows: list[Row] = [{"type": "MoveTo", "x": bx, "y": by}]
    for wx, wy in waypoints:
        rows.append({"type": "LineTo", "x": wx, "y": wy})
    rows.append({"type": "LineTo", "x": ex, "y": ey})
    return rows
