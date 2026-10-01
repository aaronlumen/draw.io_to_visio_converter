"""
Builds a Visio VSDX file from the intermediate model (models.py).

VSDX is a ZIP/OPC archive containing XML files.  This module constructs all
required parts in-memory and writes the final archive to disk.

Coordinate conversion
---------------------
draw.io   : pixels, origin at top-left
Visio     : inches, origin at bottom-left  (1 in = 96 px)

  visio_x = drawio_x / PX_PER_INCH
  visio_y = page_height_in - drawio_y / PX_PER_INCH

For shapes the Visio "pin" is the shape centre:
  pin_x = (drawio_x + width/2)  / PX_PER_INCH
  pin_y = page_height_in - (drawio_y + height/2) / PX_PER_INCH
"""

from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from models import Connector, Diagram, Page, Shape
from shape_geometry import connector_geometry, get_geometry
from stencil import MasterBundle, REL_MASTERS, StencilConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PX_PER_INCH = 96.0

# Visio XML namespace
_NS = "http://schemas.microsoft.com/office/visio/2012/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"

# Relationship type bases
_REL_DOC = "http://schemas.microsoft.com/visio/2010/relationships/document"
_REL_PAGES = "http://schemas.microsoft.com/visio/2010/relationships/pages"
_REL_PAGE = "http://schemas.microsoft.com/visio/2010/relationships/page"
_REL_WINDOWS = "http://schemas.microsoft.com/visio/2010/relationships/windows"
_REL_APP = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties"
_REL_CORE = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"

# Default page size (A4 landscape) in draw.io pixels when the diagram omits it
_DEFAULT_PAGE_W_PX = 1169.0
_DEFAULT_PAGE_H_PX = 827.0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_vsdx(
    diagram: Diagram,
    output_path: str | Path,
    stencils: StencilConfig | None = None,
) -> None:
    """Convert *diagram* to a VSDX file and write it to *output_path*.

    If *stencils* is given, shapes matched by its rules are emitted as
    instances of the stencil masters instead of inline geometry.
    """
    output_path = Path(output_path)
    bundle = MasterBundle.from_diagram(diagram, stencils) if stencils else None
    if not bundle:
        bundle = None
    master_ids = bundle.ids if bundle else {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        _write_content_types(zf, len(diagram.pages), bundle)
        _write_root_rels(zf)
        _write_app_xml(zf)
        _write_core_xml(zf)
        _write_document_xml(zf, bundle)
        _write_document_rels(zf, bundle)
        if bundle:
            bundle.write_parts(zf)
        _write_windows_xml(zf)
        _write_pages_xml(zf, diagram.pages)
        _write_pages_rels(zf, len(diagram.pages))
        for idx, page in enumerate(diagram.pages, start=1):
            _write_page_xml(zf, page, idx, master_ids)

    output_path.write_bytes(buf.getvalue())


# ---------------------------------------------------------------------------
# OPC boilerplate parts
# ---------------------------------------------------------------------------

def _write_content_types(zf: zipfile.ZipFile, num_pages: int,
                         bundle: MasterBundle | None = None) -> None:
    ET.register_namespace("", _NS_TYPES)
    root = ET.Element(f"{{{_NS_TYPES}}}Types")

    def _t(tag: str, **attribs) -> ET.Element:
        return ET.SubElement(root, f"{{{_NS_TYPES}}}{tag}", **attribs)

    _t("Default", Extension="rels",
       ContentType="application/vnd.openxmlformats-package.relationships+xml")
    _t("Default", Extension="xml",
       ContentType="application/xml")
    _t("Override",
       PartName="/visio/document.xml",
       ContentType="application/vnd.ms-visio.drawing.main+xml")
    _t("Override",
       PartName="/visio/windows.xml",
       ContentType="application/vnd.ms-visio.windows+xml")
    _t("Override",
       PartName="/visio/pages/pages.xml",
       ContentType="application/vnd.ms-visio.pages+xml")
    for i in range(1, num_pages + 1):
        _t("Override",
           PartName=f"/visio/pages/page{i}.xml",
           ContentType="application/vnd.ms-visio.page+xml")
    if bundle:
        for part, ctype in bundle.content_type_overrides():
            _t("Override", PartName=part, ContentType=ctype)
    _t("Override",
       PartName="/docProps/app.xml",
       ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml")
    _t("Override",
       PartName="/docProps/core.xml",
       ContentType="application/vnd.openxmlformats-package.core-properties+xml")
    zf.writestr("[Content_Types].xml", _to_xml(root))


def _write_root_rels(zf: zipfile.ZipFile) -> None:
    ET.register_namespace("", _NS_PKG_REL)
    root = ET.Element(f"{{{_NS_PKG_REL}}}Relationships")
    for rid, rtype, target in [
        ("rId1", _REL_DOC,  "visio/document.xml"),
        ("rId2", _REL_APP,  "docProps/app.xml"),
        ("rId3", _REL_CORE, "docProps/core.xml"),
    ]:
        ET.SubElement(root, f"{{{_NS_PKG_REL}}}Relationship",
                      Id=rid, Type=rtype, Target=target)
    zf.writestr("_rels/.rels", _to_xml(root))


def _write_app_xml(zf: zipfile.ZipFile) -> None:
    ns = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
    root = ET.Element("Properties", xmlns=ns)
    _sub(root, "Application").text = "draw.io to VSDX Converter"
    _sub(root, "AppVersion").text = "1.0"
    zf.writestr("docProps/app.xml", _to_xml(root))


def _write_core_xml(zf: zipfile.ZipFile) -> None:
    ns_cp = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
    ns_dc = "http://purl.org/dc/elements/1.1/"
    ns_dcterms = "http://purl.org/dc/terms/"
    ns_xsi = "http://www.w3.org/2001/XMLSchema-instance"

    ET.register_namespace("cp", ns_cp)
    ET.register_namespace("dc", ns_dc)
    ET.register_namespace("dcterms", ns_dcterms)
    ET.register_namespace("xsi", ns_xsi)

    root = ET.Element(f"{{{ns_cp}}}coreProperties")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    created = ET.SubElement(root, f"{{{ns_dcterms}}}created")
    created.set(f"{{{ns_xsi}}}type", "dcterms:W3CDTF")
    created.text = now
    modified = ET.SubElement(root, f"{{{ns_dcterms}}}modified")
    modified.set(f"{{{ns_xsi}}}type", "dcterms:W3CDTF")
    modified.text = now
    zf.writestr("docProps/core.xml", _to_xml(root))


def _write_document_xml(zf: zipfile.ZipFile,
                        bundle: MasterBundle | None = None) -> None:
    ET.register_namespace("", _NS)
    root = ET.Element(f"{{{_NS}}}VisioDocument",
                      attrib={"xml:space": "preserve"})
    settings = _sub(root, "DocumentSettings")
    _sub(settings, "GlueSettings").text = "9"
    _sub(settings, "SnapSettings").text = "65847"

    face_names = _sub(root, "FaceNames")
    _sub(face_names, "FaceName", ID="0", Name="Calibri",
         UnicodeRanges="00000003 00000000 00000000 00000000",
         CharSets="00000001", Panos="020B0502020204030204",
         Flags="0")
    _sub(face_names, "FaceName", ID="1", Name="Arial",
         Flags="0")

    style_sheets = _sub(root, "StyleSheets")
    ss = _sub(style_sheets, "StyleSheet", ID="0", Name="No Style",
              FillStyle="0", LineStyle="0", TextStyle="0")
    # Minimal default cells
    line_section = _sub(ss, "Line")
    _sub(line_section, "Cell", N="LineWeight", V="0.010416666666666666")
    _sub(line_section, "Cell", N="LineColor", V="#000000")
    _sub(line_section, "Cell", N="LinePattern", V="1")

    fill_section = _sub(ss, "Fill")
    _sub(fill_section, "Cell", N="FillForegnd", V="#ffffff")
    _sub(fill_section, "Cell", N="FillPattern", V="1")

    char_section = _sub(ss, "Character")
    row = _sub(char_section, "Row", IX="0")
    _sub(row, "Cell", N="Font", V="0")
    _sub(row, "Cell", N="Color", V="#000000")
    _sub(row, "Cell", N="Size", V="0.15277777777777776")

    if bundle:
        for extra in bundle.style_sheets():
            style_sheets.append(extra)

    zf.writestr("visio/document.xml", _to_xml(root))


def _write_document_rels(zf: zipfile.ZipFile,
                         bundle: MasterBundle | None = None) -> None:
    ET.register_namespace("", _NS_PKG_REL)
    root = ET.Element(f"{{{_NS_PKG_REL}}}Relationships")
    for rid, rtype, target in [
        ("rId1", _REL_PAGES,   "pages/pages.xml"),
        ("rId2", _REL_WINDOWS, "windows.xml"),
        *([("rId3", REL_MASTERS, "masters/masters.xml")] if bundle else []),
    ]:
        ET.SubElement(root, f"{{{_NS_PKG_REL}}}Relationship",
                      Id=rid, Type=rtype, Target=target)
    zf.writestr("visio/_rels/document.xml.rels", _to_xml(root))


def _write_windows_xml(zf: zipfile.ZipFile) -> None:
    root = ET.Element(f"{{{_NS}}}Windows",
                      attrib={"ClientWidth": "1680", "ClientHeight": "985"})
    win = _sub(root, "Window", ID="0", WindowType="Drawing",
               WindowState="1", WindowLeft="0", WindowTop="0",
               WindowWidth="1680", WindowHeight="985",
               Page="0", ViewScale="-1", ViewCenterX="0", ViewCenterY="0")
    _sub(win, "ShowGrid").text = "0"
    _sub(win, "ShowGuides").text = "1"
    _sub(win, "ShowConnectionPoints").text = "0"
    zf.writestr("visio/windows.xml", _to_xml(root))


def _write_pages_xml(zf: zipfile.ZipFile, pages: list[Page]) -> None:
    root = ET.Element(f"{{{_NS}}}Pages",
                      attrib={"xml:space": "preserve"})
    for idx, page in enumerate(pages):
        pw_in = page.width / PX_PER_INCH
        ph_in = page.height / PX_PER_INCH
        p_el = _sub(root, "Page", ID=str(idx), Name=page.name,
                    NameU=page.name, Background="0")
        sheet = _sub(p_el, "PageSheet", FillStyle="0", LineStyle="0",
                     TextStyle="0")
        _sub(sheet, "Cell", N="PageWidth",  V=_fmt(pw_in))
        _sub(sheet, "Cell", N="PageHeight", V=_fmt(ph_in))
        _sub(sheet, "Cell", N="PageScale",  V="1")
        _sub(sheet, "Cell", N="DrawingScale", V="1")
        _sub(sheet, "Cell", N="DrawingSizeType", V="4")
        rel = _sub(p_el, "Rel")
        rel.set("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id",
                f"rId{idx + 1}")
    zf.writestr("visio/pages/pages.xml", _to_xml(root))


def _write_pages_rels(zf: zipfile.ZipFile, num_pages: int) -> None:
    ET.register_namespace("", _NS_PKG_REL)
    root = ET.Element(f"{{{_NS_PKG_REL}}}Relationships")
    for i in range(1, num_pages + 1):
        ET.SubElement(root, f"{{{_NS_PKG_REL}}}Relationship",
                      Id=f"rId{i}", Type=_REL_PAGE, Target=f"page{i}.xml")
    zf.writestr("visio/pages/_rels/pages.xml.rels", _to_xml(root))


# ---------------------------------------------------------------------------
# Per-page content
# ---------------------------------------------------------------------------

def _write_page_xml(zf: zipfile.ZipFile, page: Page, page_num: int,
                    master_ids: dict | None = None) -> None:
    root = ET.Element(f"{{{_NS}}}PageContents",
                      attrib={"xml:space": "preserve"})
    shapes_el = _sub(root, "Shapes")
    connects_el = _sub(root, "Connects")

    ph_in = page.height / PX_PER_INCH

    # Assign monotonically increasing Visio integer IDs.
    # Keep a map: draw.io string id → visio integer id
    id_map: dict[str, int] = {}
    next_id = 1

    shape_by_id: dict[str, "Shape"] = {}
    for shape in page.shapes:
        vid = next_id
        next_id += 1
        id_map[shape.id] = vid
        shape_by_id[shape.id] = shape
        _add_shape_element(shapes_el, shape, vid, ph_in,
                           (master_ids or {}).get(shape.master_ref))

    for connector in page.connectors:
        vid = next_id
        next_id += 1
        id_map[connector.id] = vid

        # A connector with source_id/target_id (the normal draw.io case - an
        # edge glued to two vertices rather than free-floating points) has no
        # explicit <mxPoint> in its own geometry, so the parser leaves
        # start_x/y and end_x/y at their 0.0 default. Left as-is, every such
        # connector becomes a literal zero-length line at (0,0): the Connects
        # glue metadata is still correct, but there is no visible line -
        # exactly the "no connectors between the boxes" symptom. Resolve the
        # real endpoint from each referenced shape's center instead.
        start_override = end_override = None
        src_shape = shape_by_id.get(connector.source_id) if connector.source_id else None
        tgt_shape = shape_by_id.get(connector.target_id) if connector.target_id else None
        if src_shape is not None:
            start_override = (src_shape.x + src_shape.width / 2, src_shape.y + src_shape.height / 2)
        if tgt_shape is not None:
            end_override = (tgt_shape.x + tgt_shape.width / 2, tgt_shape.y + tgt_shape.height / 2)

        _add_connector_element(shapes_el, connector, vid, ph_in,
                                start_override=start_override, end_override=end_override)

        # Glue connects
        src_vid = id_map.get(connector.source_id) if connector.source_id else None
        tgt_vid = id_map.get(connector.target_id) if connector.target_id else None

        if src_vid is not None:
            conn_el = _sub(connects_el, "Connect",
                           FromSheet=str(vid), FromCell="BeginX",
                           FromPart="9",
                           ToSheet=str(src_vid), ToCell="PinX", ToPart="3")
        if tgt_vid is not None:
            conn_el = _sub(connects_el, "Connect",
                           FromSheet=str(vid), FromCell="EndX",
                           FromPart="12",
                           ToSheet=str(tgt_vid), ToCell="PinX", ToPart="3")

    # Remove Connects element if empty (Visio tolerates it, but cleaner)
    if len(connects_el) == 0:
        root.remove(connects_el)

    zf.writestr(f"visio/pages/page{page_num}.xml", _to_xml(root))


# ---------------------------------------------------------------------------
# Shape element builder
# ---------------------------------------------------------------------------

def _add_shape_element(
    parent: ET.Element,
    shape: Shape,
    vid: int,
    page_height_in: float,
    master_id: int | None = None,
) -> None:
    """Append a <Shape> element for a vertex shape.

    With *master_id* the shape is an instance of a stencil master: only the
    position, size and text are written; geometry and styling are inherited.
    """
    w_in = shape.width  / PX_PER_INCH
    h_in = shape.height / PX_PER_INCH
    pin_x = (shape.x + shape.width  / 2) / PX_PER_INCH
    pin_y = page_height_in - (shape.y + shape.height / 2) / PX_PER_INCH
    loc_pin_x = w_in / 2
    loc_pin_y = h_in / 2

    if master_id is not None:
        s = _sub(parent, "Shape", ID=str(vid), Type="Shape",
                 Master=str(master_id))
        _cell(s, "PinX",   pin_x)
        _cell(s, "PinY",   pin_y)
        _cell(s, "Width",  w_in)
        _cell(s, "Height", h_in)
        if shape.label:
            _sub(s, "Text").text = shape.label
        return

    s = _sub(parent, "Shape", ID=str(vid), Type="Shape",
             LineStyle="0", FillStyle="0", TextStyle="0")

    # Transform
    _cell(s, "PinX",    pin_x)
    _cell(s, "PinY",    pin_y)
    _cell(s, "Width",   w_in)
    _cell(s, "Height",  h_in)
    _cell(s, "LocPinX", loc_pin_x)
    _cell(s, "LocPinY", loc_pin_y)
    _cell(s, "Angle",   0)

    # Fill
    if shape.fill_color:
        _cell(s, "FillForegnd", shape.fill_color)
    _cell(s, "FillPattern", shape.fill_pattern)
    if shape.opacity < 1.0:
        _cell(s, "FillForegndTrans", 1.0 - shape.opacity)

    # Line
    _cell(s, "LineColor",   shape.stroke_color)
    _cell(s, "LineWeight",  shape.stroke_width / PX_PER_INCH)
    _cell(s, "LinePattern", 2 if shape.stroke_dashed else 1)
    if shape.rounded:
        _cell(s, "Rounding", min(w_in, h_in) * 0.1)

    # Text
    if shape.label:
        _add_text_section(s, shape)

    # Geometry
    rows = get_geometry(shape.shape_type, w_in, h_in)
    _add_geometry_section(s, rows)


def _add_text_section(shape_el: ET.Element, shape: Shape) -> None:
    """Add Character / Paragraph sections and <Text> element."""
    char_section = _sub(shape_el, "Section", N="Character")
    row = _sub(char_section, "Row", IX="0")
    _sub(row, "Cell", N="Font",  V="0")
    _sub(row, "Cell", N="Color", V=shape.font_color)
    _sub(row, "Cell", N="Size",  V=_fmt(shape.font_size))
    style_bits = (1 if shape.font_bold else 0) | (2 if shape.font_italic else 0)
    if style_bits:
        _sub(row, "Cell", N="Style", V=str(style_bits))

    para_section = _sub(shape_el, "Section", N="Paragraph")
    p_row = _sub(para_section, "Row", IX="0")
    _sub(p_row, "Cell", N="HorzAlign", V="1")  # centred

    text_el = _sub(shape_el, "Text")
    text_el.text = shape.label


def _add_geometry_section(shape_el: ET.Element, rows: list[dict]) -> None:
    geom = _sub(shape_el, "Section", N="Geometry", IX="0")
    _sub(geom, "Cell", N="NoFill",   V="0")
    _sub(geom, "Cell", N="NoLine",   V="0")

    for ix, row in enumerate(rows, start=1):
        row_type = row["type"]
        r_el = _sub(geom, "Row", T=row_type, IX=str(ix))
        _sub(r_el, "Cell", N="X", V=_fmt(row["x"]))
        _sub(r_el, "Cell", N="Y", V=_fmt(row["y"]))
        if row_type == "ArcTo":
            _sub(r_el, "Cell", N="A", V=_fmt(row.get("a", 0)))
        elif row_type == "Ellipse":
            _sub(r_el, "Cell", N="A", V=_fmt(row.get("a", 0)))
            _sub(r_el, "Cell", N="B", V=_fmt(row.get("b", 0)))
            _sub(r_el, "Cell", N="C", V=_fmt(row.get("c", 0)))
            _sub(r_el, "Cell", N="D", V=_fmt(row.get("d", 0)))


# ---------------------------------------------------------------------------
# Connector element builder
# ---------------------------------------------------------------------------

def _add_connector_element(
    parent: ET.Element,
    connector: Connector,
    vid: int,
    page_height_in: float,
    start_override: tuple[float, float] | None = None,
    end_override: tuple[float, float] | None = None,
) -> None:
    """Append a <Shape> element for a connector/edge.

    start_override/end_override (draw.io pixel coordinates) take precedence
    over connector.start_x/y and end_x/y when given - see the call site in
    _write_page_xml for why: an edge glued via source_id/target_id (the
    normal case) has no explicit <mxPoint> of its own, so those attributes
    are just the parser's 0.0 default and must not be used directly.
    """
    def _tx(px: float) -> float:
        return px / PX_PER_INCH

    def _ty(py: float) -> float:
        return page_height_in - py / PX_PER_INCH

    start_px = start_override if start_override is not None else (connector.start_x, connector.start_y)
    end_px = end_override if end_override is not None else (connector.end_x, connector.end_y)

    bx = _tx(start_px[0])
    by = _ty(start_px[1])
    ex = _tx(end_px[0])
    ey = _ty(end_px[1])
    waypoints = [(_tx(wp.x), _ty(wp.y)) for wp in connector.waypoints]

    s = _sub(parent, "Shape", ID=str(vid), Type="Shape",
             LineStyle="0", FillStyle="0", TextStyle="0")

    _cell(s, "BeginX", bx)
    _cell(s, "BeginY", by)
    _cell(s, "EndX",   ex)
    _cell(s, "EndY",   ey)

    # Arrow heads
    from style_mapper import map_arrow
    _cell(s, "BeginArrow", map_arrow(connector.start_arrow))
    _cell(s, "EndArrow",   map_arrow(connector.end_arrow))
    _cell(s, "BeginArrowSize", 4)
    _cell(s, "EndArrowSize",   4)

    # Line style
    _cell(s, "LineColor",   connector.stroke_color)
    _cell(s, "LineWeight",  connector.stroke_width / PX_PER_INCH)
    _cell(s, "LinePattern", 2 if connector.stroke_dashed else 1)

    # Fill: connectors have no fill
    _cell(s, "FillPattern", 0)

    # Label
    if connector.label:
        char_section = _sub(s, "Section", N="Character")
        row = _sub(char_section, "Row", IX="0")
        _sub(row, "Cell", N="Font",  V="0")
        _sub(row, "Cell", N="Color", V="#000000")
        _sub(row, "Cell", N="Size",  V=_fmt(11.0 / 72.0))
        text_el = _sub(s, "Text")
        text_el.text = connector.label

    # Geometry path
    rows = connector_geometry(bx, by, ex, ey, waypoints)
    _add_geometry_section(s, rows)


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _sub(parent: ET.Element, tag: str, **attribs: str) -> ET.Element:
    """Create a sub-element under *parent* using the Visio namespace."""
    # If the tag already has an explicit namespace, use it as-is.
    if "{" in tag:
        el = ET.SubElement(parent, tag, **attribs)
    else:
        el = ET.SubElement(parent, f"{{{_NS}}}{tag}", **attribs)
    return el


def _cell(parent: ET.Element, name: str, value) -> ET.Element:
    """Append a <Cell N="..." V="..."/> element."""
    return ET.SubElement(parent, f"{{{_NS}}}Cell", N=name, V=_fmt(value))


def _fmt(v) -> str:
    """Format a numeric or string value for a Visio XML attribute."""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        # Avoid scientific notation; enough precision for CAD-level accuracy
        return f"{v:.10g}"
    return str(v)


def _to_xml(root: ET.Element) -> str:
    """Serialise an ElementTree to a UTF-8 XML string with declaration."""
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + \
           ET.tostring(root, encoding="unicode", xml_declaration=False)
