"""
Intermediate data model for the draw.io → VSDX converter.

All coordinates and dimensions are stored in the original draw.io pixel units.
Conversion to Visio inches happens in vsdx_builder.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Waypoint:
    """An explicit bend-point on a connector path."""

    x: float
    y: float


@dataclass
class Shape:
    """Represents a draw.io vertex (rectangle, ellipse, diamond, …)."""

    id: str
    label: str = ""

    # Position and size (pixels, top-left origin)
    x: float = 0.0
    y: float = 0.0
    width: float = 120.0
    height: float = 60.0

    # Shape type resolved from style string
    shape_type: str = "rectangle"  # rectangle | ellipse | diamond | parallelogram
                                   # triangle | cylinder | hexagon

    # Fill
    fill_color: Optional[str] = "#ffffff"  # hex "#RRGGBB" or None for transparent
    fill_pattern: int = 1  # 1 = solid, 0 = none

    # Stroke
    stroke_color: str = "#000000"
    stroke_width: float = 1.0  # pixels
    stroke_dashed: bool = False
    rounded: bool = False

    # Text / font
    font_size: float = 11.0  # pt
    font_color: str = "#000000"
    font_bold: bool = False
    font_italic: bool = False

    # Opacity (0.0–1.0, applied to fill)
    opacity: float = 1.0

    # Parsed raw style dict (kept for debugging / future extensions)
    style: dict = field(default_factory=dict)

    # Child shape IDs (for groups; children store their own coords relative to page)
    children: list[str] = field(default_factory=list)

    # Parent shape ID (None if top-level)
    parent_id: Optional[str] = None


@dataclass
class Connector:
    """Represents a draw.io edge (connector / arrow)."""

    id: str
    label: str = ""

    # Connected shape IDs (None if floating / not glued)
    source_id: Optional[str] = None
    target_id: Optional[str] = None

    # Absolute endpoints (pixels).  Populated from mxGeometry sourcePoint/targetPoint
    # or computed from connected shape centres when source/target are set.
    start_x: float = 0.0
    start_y: float = 0.0
    end_x: float = 100.0
    end_y: float = 0.0

    # Explicit bend-points
    waypoints: list[Waypoint] = field(default_factory=list)

    # Arrow heads
    start_arrow: str = "none"   # none | classic | open | block
    end_arrow: str = "classic"  # none | classic | open | block

    # Line style
    stroke_color: str = "#000000"
    stroke_width: float = 1.0  # pixels
    stroke_dashed: bool = False

    # Parsed raw style dict
    style: dict = field(default_factory=dict)


@dataclass
class Page:
    """One tab / diagram page."""

    name: str = "Page-1"

    # Page size in pixels (draw.io defaults: 1169 × 827 for A4 landscape)
    width: float = 1169.0
    height: float = 827.0

    shapes: list[Shape] = field(default_factory=list)
    connectors: list[Connector] = field(default_factory=list)


@dataclass
class Diagram:
    """Top-level container parsed from one .drawio file."""

    source_path: str = ""
    pages: list[Page] = field(default_factory=list)
