"""
Parses a .drawio XML file into the intermediate data model (models.py).

Handles both:
  - Plain XML <diagram> content
  - Compressed <diagram> content (base64 → raw-deflate → percent-decode)
"""

from __future__ import annotations

import base64
import html
import re
import urllib.parse
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path

from models import Connector, Diagram, Page, Shape, Waypoint
from style_mapper import (
    get_shape_type,
    map_end_arrow,
    map_fill_color,
    map_font_bold,
    map_font_color,
    map_font_italic,
    map_font_size,
    map_opacity,
    map_rounded,
    map_start_arrow,
    map_stroke_color,
    map_stroke_dashed,
    map_stroke_width,
    parse_style,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_file(path: str | Path) -> Diagram:
    """Parse a .drawio file and return a :class:`~models.Diagram`."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_string(text, source_path=str(path))


def parse_string(xml_text: str, source_path: str = "") -> Diagram:
    """Parse draw.io XML from a string."""
    root = ET.fromstring(xml_text)
    # <mxfile> is the typical root; tolerate a bare <mxGraphModel> too.
    if root.tag == "mxGraphModel":
        # Wrap it as a single un-named page
        diagram = Diagram(source_path=source_path)
        page = _parse_graph_model(root, "Page-1")
        diagram.pages.append(page)
        return diagram

    diagram = Diagram(source_path=source_path)
    for idx, diag_el in enumerate(root.iter("diagram")):
        name = diag_el.get("name", f"Page-{idx + 1}")
        model_el = _get_graph_model(diag_el)
        if model_el is None:
            continue
        page = _parse_graph_model(model_el, name)
        diagram.pages.append(page)

    if not diagram.pages:
        raise ValueError(f"No parseable <diagram> elements found in {source_path!r}")

    return diagram


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_graph_model(diag_el: ET.Element) -> ET.Element | None:
    """Return the <mxGraphModel> for a <diagram> element.

    The content may be:
      a) a nested <mxGraphModel> element already in the tree, or
      b) compressed text (base64 → deflate → percent-decode → XML).
    """
    # Case a: already parsed as child element
    model_el = diag_el.find("mxGraphModel")
    if model_el is not None:
        return model_el

    # Case b: compressed text node
    raw_text = (diag_el.text or "").strip()
    if not raw_text:
        return None
    try:
        xml_str = _decompress_diagram(raw_text)
        return ET.fromstring(xml_str)
    except Exception:
        # Last resort: try treating the text as raw XML
        try:
            return ET.fromstring(raw_text)
        except ET.ParseError:
            return None


def _decompress_diagram(encoded: str) -> str:
    """Decompress a draw.io base64+deflate+percent-encoded diagram payload."""
    # 1. base64-decode
    decoded_bytes = base64.b64decode(encoded)
    # 2. raw inflate (no zlib header, wbits=-15)
    inflated = zlib.decompress(decoded_bytes, -15)
    # 3. percent-decode UTF-8
    return urllib.parse.unquote(inflated.decode("utf-8", errors="replace"))


def _parse_graph_model(model_el: ET.Element, page_name: str) -> Page:
    """Convert an <mxGraphModel> element into a :class:`~models.Page`."""
    page_w = float(model_el.get("pageWidth", 1169))
    page_h = float(model_el.get("pageHeight", 827))
    page = Page(name=page_name, width=page_w, height=page_h)

    # First pass: collect all cells by id (needed to resolve parent chains)
    cells: dict[str, ET.Element] = {}
    for cell in model_el.iter("mxCell"):
        cid = cell.get("id")
        if cid:
            cells[cid] = cell

    # Second pass: build shapes and connectors
    for cell in model_el.iter("mxCell"):
        cid = cell.get("id", "")
        # Skip the two implicit root cells (id="0" and id="1")
        if cid in ("0", "1"):
            continue

        if cell.get("vertex") == "1":
            shape = _parse_shape(cell, cells)
            if shape is not None:
                page.shapes.append(shape)
        elif cell.get("edge") == "1":
            connector = _parse_connector(cell)
            if connector is not None:
                page.connectors.append(connector)

    return page


# ---------------------------------------------------------------------------
# Shape parsing
# ---------------------------------------------------------------------------

def _parse_shape(cell: ET.Element, all_cells: dict[str, ET.Element]) -> Shape | None:
    """Build a :class:`~models.Shape` from an mxCell with vertex="1"."""
    cid = cell.get("id", "")
    geom = cell.find("mxGeometry")
    if geom is None:
        return None

    x = float(geom.get("x", 0))
    y = float(geom.get("y", 0))
    w = float(geom.get("width", 120))
    h = float(geom.get("height", 60))

    # Resolve absolute position from parent chain
    parent_id = cell.get("parent", "1")
    if parent_id not in ("0", "1"):
        px, py = _get_parent_offset(parent_id, all_cells)
        x += px
        y += py

    raw_label = cell.get("value", "")
    label = _clean_label(raw_label)

    style_str = cell.get("style", "")
    style = parse_style(style_str)

    fill_color, fill_pattern = map_fill_color(style)

    shape = Shape(
        id=cid,
        label=label,
        x=x, y=y, width=w, height=h,
        shape_type=get_shape_type(style),
        fill_color=fill_color,
        fill_pattern=fill_pattern,
        stroke_color=map_stroke_color(style),
        stroke_width=map_stroke_width(style),
        stroke_dashed=map_stroke_dashed(style),
        rounded=map_rounded(style),
        font_size=map_font_size(style),          # already in inches
        font_color=map_font_color(style),
        font_bold=map_font_bold(style),
        font_italic=map_font_italic(style),
        opacity=map_opacity(style),
        style=style,
        parent_id=parent_id if parent_id not in ("0", "1") else None,
    )
    return shape


def _get_parent_offset(
    parent_id: str,
    all_cells: dict[str, ET.Element],
    _seen: frozenset[str] | None = None,
) -> tuple[float, float]:
    """Recursively resolve absolute offset of a group parent cell."""
    if _seen is None:
        _seen = frozenset()
    if parent_id in ("0", "1") or parent_id in _seen:
        return 0.0, 0.0

    parent_cell = all_cells.get(parent_id)
    if parent_cell is None:
        return 0.0, 0.0

    geom = parent_cell.find("mxGeometry")
    if geom is None:
        return 0.0, 0.0

    px = float(geom.get("x", 0))
    py = float(geom.get("y", 0))

    grandparent_id = parent_cell.get("parent", "1")
    if grandparent_id not in ("0", "1"):
        gpx, gpy = _get_parent_offset(grandparent_id, all_cells, _seen | {parent_id})
        px += gpx
        py += gpy

    return px, py


# ---------------------------------------------------------------------------
# Connector parsing
# ---------------------------------------------------------------------------

def _parse_connector(cell: ET.Element) -> Connector | None:
    """Build a :class:`~models.Connector` from an mxCell with edge="1"."""
    cid = cell.get("id", "")
    raw_label = cell.get("value", "")
    label = _clean_label(raw_label)

    source_id = cell.get("source") or None
    target_id = cell.get("target") or None

    style_str = cell.get("style", "")
    style = parse_style(style_str)

    geom = cell.find("mxGeometry")
    start_x = start_y = end_x = end_y = 0.0
    waypoints: list[Waypoint] = []

    if geom is not None:
        sp = geom.find("mxPoint[@as='sourcePoint']")
        tp = geom.find("mxPoint[@as='targetPoint']")
        if sp is not None:
            start_x = float(sp.get("x", 0))
            start_y = float(sp.get("y", 0))
        if tp is not None:
            end_x = float(tp.get("x", 0))
            end_y = float(tp.get("y", 0))

        arr_el = geom.find("Array[@as='points']")
        if arr_el is not None:
            for pt in arr_el.findall("mxPoint"):
                waypoints.append(Waypoint(
                    x=float(pt.get("x", 0)),
                    y=float(pt.get("y", 0)),
                ))

    return Connector(
        id=cid,
        label=label,
        source_id=source_id,
        target_id=target_id,
        start_x=start_x,
        start_y=start_y,
        end_x=end_x,
        end_y=end_y,
        waypoints=waypoints,
        start_arrow=style.get("startArrow", "none"),
        end_arrow=style.get("endArrow", "classic"),
        stroke_color=map_stroke_color(style),
        stroke_width=map_stroke_width(style),
        stroke_dashed=map_stroke_dashed(style),
        style=style,
    )


# ---------------------------------------------------------------------------
# Label cleaning
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_label(raw: str) -> str:
    """Strip HTML tags and decode HTML entities from a draw.io label."""
    if not raw:
        return ""
    # Replace <br> variants with newline before stripping tags
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    return text.strip()
