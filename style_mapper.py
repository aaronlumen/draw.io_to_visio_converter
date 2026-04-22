"""
Maps draw.io style strings to VSDX-ready values.

A draw.io style string looks like:
    "rounded=1;fillColor=#d5e8d4;strokeColor=#82b366;fontSize=11;endArrow=classic;"

parse_style() converts this to a plain dict.
All other functions consume that dict and return scalar VSDX values.
"""

from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Style string parser
# ---------------------------------------------------------------------------

def parse_style(style_str: str) -> dict[str, str]:
    """Parse a draw.io semicolon-separated style string into a dict.

    For bare flags like "ellipse" or "rounded" (no '=' sign) the value is set
    to "1" so they can be checked uniformly.
    """
    result: dict[str, str] = {}
    if not style_str:
        return result
    for token in style_str.split(";"):
        token = token.strip()
        if not token:
            continue
        if "=" in token:
            key, _, value = token.partition("=")
            result[key.strip()] = value.strip()
        else:
            # bare flag, e.g. "ellipse", "rounded", "swimlane"
            result[token] = "1"
    return result


# ---------------------------------------------------------------------------
# Shape type detection
# ---------------------------------------------------------------------------

#: Maps draw.io style 'shape' values (and bare flags) to internal type names.
_SHAPE_TYPE_MAP: dict[str, str] = {
    # Rectangles / defaults
    "rectangle": "rectangle",
    # Ellipses
    "ellipse": "ellipse",
    "mxgraph.flowchart.start_2": "ellipse",
    # Diamonds / rhombuses
    "rhombus": "diamond",
    "diamond": "diamond",
    "mxgraph.flowchart.decision": "diamond",
    # Parallelograms
    "parallelogram": "parallelogram",
    "mxgraph.basic.parallelogram": "parallelogram",
    "mxgraph.flowchart.input_output": "parallelogram",
    # Triangles
    "triangle": "triangle",
    "mxgraph.basic.acute_triangle": "triangle",
    "mxgraph.flowchart.extract": "triangle",
    # Cylinders
    "cylinder": "cylinder",
    "cylinder3": "cylinder",
    "mxgraph.basic.cylinder": "cylinder",
    "mxgraph.flowchart.stored_data": "cylinder",
    # Hexagons
    "hexagon": "hexagon",
    "mxgraph.basic.hexagon": "hexagon",
    "mxgraph.flowchart.preparation": "hexagon",
}


def get_shape_type(style: dict[str, str]) -> str:
    """Return the internal shape type name for a parsed style dict."""
    # Bare flags take priority over a 'shape=' key
    for bare_flag in ("ellipse", "rhombus", "triangle", "hexagon"):
        if style.get(bare_flag) == "1":
            return _SHAPE_TYPE_MAP.get(bare_flag, "rectangle")

    shape_val = style.get("shape", "").lower()
    if shape_val:
        return _SHAPE_TYPE_MAP.get(shape_val, "rectangle")

    return "rectangle"


# ---------------------------------------------------------------------------
# Fill
# ---------------------------------------------------------------------------

def map_fill_color(style: dict[str, str]) -> tuple[Optional[str], int]:
    """Return (hex_color_or_None, fill_pattern).

    fill_pattern: 0 = no fill, 1 = solid.
    """
    raw = style.get("fillColor", "#ffffff")
    if raw.lower() in ("none", "transparent", ""):
        return None, 0
    return _normalize_color(raw), 1


# ---------------------------------------------------------------------------
# Stroke / line
# ---------------------------------------------------------------------------

def map_stroke_color(style: dict[str, str]) -> str:
    return _normalize_color(style.get("strokeColor", "#000000"))


def map_stroke_width(style: dict[str, str]) -> float:
    """Return stroke width in pixels."""
    try:
        return float(style.get("strokeWidth", "1"))
    except ValueError:
        return 1.0


def map_stroke_dashed(style: dict[str, str]) -> bool:
    return style.get("dashed", "0") == "1"


def map_line_pattern(style: dict[str, str]) -> int:
    """1 = solid, 2 = dashed (Visio LinePattern values)."""
    return 2 if map_stroke_dashed(style) else 1


# ---------------------------------------------------------------------------
# Arrows
# ---------------------------------------------------------------------------

#: Maps draw.io arrow names to Visio EndArrow / BeginArrow numeric codes.
#  0 = none, 4 = open arrow, 5 = filled triangle (classic)
_ARROW_MAP: dict[str, int] = {
    "none": 0,
    "": 0,
    "open": 4,
    "openThin": 4,
    "classic": 5,
    "classicThin": 5,
    "block": 5,
    "blockThin": 5,
    "oval": 5,
    "diamond": 5,
    "halfCircle": 4,
    "ERzeroToOne": 0,
    "ERmandOne": 0,
}


def map_arrow(arrow_name: str) -> int:
    """Map a draw.io arrow name to a Visio arrow code."""
    return _ARROW_MAP.get(arrow_name, 5)  # default: filled triangle


def map_end_arrow(style: dict[str, str]) -> int:
    return map_arrow(style.get("endArrow", "classic"))


def map_start_arrow(style: dict[str, str]) -> int:
    return map_arrow(style.get("startArrow", "none"))


# ---------------------------------------------------------------------------
# Font / text
# ---------------------------------------------------------------------------

_POINTS_TO_INCHES = 1.0 / 72.0


def map_font_size(style: dict[str, str]) -> float:
    """Return font size in inches (Visio Character/Size)."""
    try:
        pts = float(style.get("fontSize", "11"))
    except ValueError:
        pts = 11.0
    return pts * _POINTS_TO_INCHES


def map_font_color(style: dict[str, str]) -> str:
    return _normalize_color(style.get("fontColor", "#000000"))


def map_font_bold(style: dict[str, str]) -> bool:
    """draw.io fontStyle is a bitmask: 1=bold, 2=italic, 4=underline."""
    try:
        fs = int(style.get("fontStyle", "0"))
    except ValueError:
        fs = 0
    return bool(fs & 1)


def map_font_italic(style: dict[str, str]) -> bool:
    try:
        fs = int(style.get("fontStyle", "0"))
    except ValueError:
        fs = 0
    return bool(fs & 2)


def map_rounded(style: dict[str, str]) -> bool:
    return style.get("rounded", "0") == "1"


def map_opacity(style: dict[str, str]) -> float:
    """Return opacity as 0.0–1.0. draw.io stores it as 0–100."""
    try:
        raw = float(style.get("opacity", "100"))
        # Some diagrams use 0.0–1.0 directly; normalise.
        if raw > 1.0:
            raw = raw / 100.0
        return max(0.0, min(1.0, raw))
    except ValueError:
        return 1.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_color(raw: str) -> str:
    """Ensure the color is a valid 6-digit upper-case hex string (#RRGGBB)."""
    raw = raw.strip()
    if not raw or raw.lower() in ("none", "default"):
        return "#000000"
    if not raw.startswith("#"):
        raw = "#" + raw
    # Expand 3-digit shorthand (#RGB → #RRGGBB)
    if len(raw) == 4:
        raw = "#" + raw[1] * 2 + raw[2] * 2 + raw[3] * 2
    return raw.upper()
