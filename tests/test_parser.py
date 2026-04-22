"""Tests for drawio_parser.py and style_mapper.py."""

from __future__ import annotations

import base64
import zlib
import urllib.parse
from pathlib import Path

import pytest

# Ensure the package root is importable when run from tests/ or from root
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from drawio_parser import parse_file, parse_string, _clean_label
from style_mapper import (
    get_shape_type,
    map_fill_color,
    map_font_bold,
    map_font_color,
    map_font_italic,
    map_font_size,
    map_line_pattern,
    map_opacity,
    map_rounded,
    map_stroke_color,
    map_stroke_dashed,
    map_stroke_width,
    parse_style,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# style_mapper tests
# ---------------------------------------------------------------------------

class TestParseStyle:
    def test_empty(self):
        assert parse_style("") == {}

    def test_key_value(self):
        d = parse_style("fillColor=#ff0000;strokeColor=#00ff00;")
        assert d["fillColor"] == "#ff0000"
        assert d["strokeColor"] == "#00ff00"

    def test_bare_flag(self):
        d = parse_style("ellipse;rounded=1;")
        assert d["ellipse"] == "1"
        assert d["rounded"] == "1"

    def test_trailing_semicolon(self):
        d = parse_style("a=1;")
        assert d == {"a": "1"}

    def test_whitespace_tolerant(self):
        d = parse_style(" fontSize = 14 ; ")
        assert d["fontSize"] == "14"


class TestGetShapeType:
    def test_default_rectangle(self):
        assert get_shape_type({}) == "rectangle"

    def test_ellipse_bare_flag(self):
        assert get_shape_type({"ellipse": "1"}) == "ellipse"

    def test_ellipse_via_shape_key(self):
        assert get_shape_type({"shape": "ellipse"}) == "ellipse"

    def test_diamond_rhombus(self):
        assert get_shape_type({"rhombus": "1"}) == "diamond"
        assert get_shape_type({"shape": "rhombus"}) == "diamond"

    def test_parallelogram(self):
        assert get_shape_type({"shape": "parallelogram"}) == "parallelogram"

    def test_triangle(self):
        assert get_shape_type({"triangle": "1"}) == "triangle"

    def test_cylinder(self):
        assert get_shape_type({"shape": "cylinder3"}) == "cylinder"

    def test_hexagon(self):
        assert get_shape_type({"shape": "hexagon"}) == "hexagon"

    def test_unknown_falls_back(self):
        assert get_shape_type({"shape": "supershape_unknown"}) == "rectangle"


class TestMapColors:
    def test_fill_solid(self):
        color, pattern = map_fill_color({"fillColor": "#d5e8d4"})
        assert color == "#D5E8D4"
        assert pattern == 1

    def test_fill_none(self):
        color, pattern = map_fill_color({"fillColor": "none"})
        assert color is None
        assert pattern == 0

    def test_stroke_color(self):
        assert map_stroke_color({"strokeColor": "#82b366"}) == "#82B366"

    def test_stroke_width(self):
        assert map_stroke_width({"strokeWidth": "3"}) == 3.0

    def test_stroke_dashed(self):
        assert map_stroke_dashed({"dashed": "1"}) is True
        assert map_stroke_dashed({"dashed": "0"}) is False
        assert map_stroke_dashed({}) is False

    def test_line_pattern(self):
        assert map_line_pattern({"dashed": "1"}) == 2
        assert map_line_pattern({}) == 1


class TestMapFont:
    def test_font_size_inches(self):
        size_in = map_font_size({"fontSize": "72"})
        assert abs(size_in - 1.0) < 1e-6

    def test_font_color(self):
        assert map_font_color({"fontColor": "#ff0000"}) == "#FF0000"

    def test_bold(self):
        assert map_font_bold({"fontStyle": "1"}) is True
        assert map_font_bold({"fontStyle": "2"}) is False

    def test_italic(self):
        assert map_font_italic({"fontStyle": "2"}) is True
        assert map_font_italic({"fontStyle": "1"}) is False

    def test_bold_and_italic(self):
        assert map_font_bold({"fontStyle": "3"}) is True
        assert map_font_italic({"fontStyle": "3"}) is True


class TestMapMisc:
    def test_rounded(self):
        assert map_rounded({"rounded": "1"}) is True
        assert map_rounded({}) is False

    def test_opacity_100_scale(self):
        assert map_opacity({"opacity": "50"}) == pytest.approx(0.5)

    def test_opacity_fraction(self):
        assert map_opacity({"opacity": "0.5"}) == pytest.approx(0.5)

    def test_opacity_default(self):
        assert map_opacity({}) == 1.0


# ---------------------------------------------------------------------------
# Parser tests — simple fixture
# ---------------------------------------------------------------------------

class TestParseSimple:
    @pytest.fixture(scope="class")
    def diagram(self):
        return parse_file(FIXTURES / "simple.drawio")

    def test_one_page(self, diagram):
        assert len(diagram.pages) == 1

    def test_page_name(self, diagram):
        assert diagram.pages[0].name == "Page-1"

    def test_two_shapes(self, diagram):
        page = diagram.pages[0]
        assert len(page.shapes) == 2

    def test_one_connector(self, diagram):
        page = diagram.pages[0]
        assert len(page.connectors) == 1

    def test_shape_labels(self, diagram):
        labels = {s.label for s in diagram.pages[0].shapes}
        assert labels == {"Box A", "Box B"}

    def test_shape_colors(self, diagram):
        shape_a = next(s for s in diagram.pages[0].shapes if s.label == "Box A")
        assert shape_a.fill_color == "#DAE8FC"

    def test_connector_source_target(self, diagram):
        conn = diagram.pages[0].connectors[0]
        shapes = {s.id: s for s in diagram.pages[0].shapes}
        assert conn.source_id in shapes
        assert conn.target_id in shapes

    def test_connector_label(self, diagram):
        conn = diagram.pages[0].connectors[0]
        assert conn.label == "connects"

    def test_shape_positions(self, diagram):
        shape_a = next(s for s in diagram.pages[0].shapes if s.label == "Box A")
        assert shape_a.x == pytest.approx(100)
        assert shape_a.y == pytest.approx(100)


# ---------------------------------------------------------------------------
# Parser tests — extended fixture
# ---------------------------------------------------------------------------

class TestParseExtended:
    @pytest.fixture(scope="class")
    def diagram(self):
        return parse_file(FIXTURES / "extended.drawio")

    def test_two_pages(self, diagram):
        assert len(diagram.pages) == 2

    def test_page_names(self, diagram):
        names = [p.name for p in diagram.pages]
        assert "Shapes" in names
        assert "Flowchart" in names

    def test_shape_types(self, diagram):
        page = diagram.pages[0]
        types = {s.shape_type for s in page.shapes}
        assert "ellipse"      in types
        assert "diamond"      in types
        assert "parallelogram" in types
        assert "triangle"     in types
        assert "cylinder"     in types
        assert "hexagon"      in types

    def test_dashed_connector(self, diagram):
        page = diagram.pages[0]
        dashed = [c for c in page.connectors if c.stroke_dashed]
        assert len(dashed) >= 1

    def test_connector_with_waypoint(self, diagram):
        page = diagram.pages[0]
        with_wps = [c for c in page.connectors if c.waypoints]
        assert len(with_wps) >= 1
        assert with_wps[0].waypoints[0].x == pytest.approx(400)

    def test_group_children_have_absolute_coords(self, diagram):
        page = diagram.pages[0]
        # Child A is at (20, 30) relative to group (50, 250) → absolute (70, 280)
        child = next(
            (s for s in page.shapes if s.label == "Child A"), None
        )
        assert child is not None
        assert child.x == pytest.approx(70)
        assert child.y == pytest.approx(280)

    def test_font_bold_italic(self, diagram):
        page = diagram.pages[0]
        bold_italic = next((s for s in page.shapes if s.label == "Bold Italic"), None)
        assert bold_italic is not None
        assert bold_italic.font_bold is True
        assert bold_italic.font_italic is True

    def test_page2_flowchart_shapes(self, diagram):
        page2 = next(p for p in diagram.pages if p.name == "Flowchart")
        assert len(page2.shapes) == 3
        assert len(page2.connectors) == 2


# ---------------------------------------------------------------------------
# Compressed diagram test
# ---------------------------------------------------------------------------

class TestCompressedParsing:
    def _make_compressed(self, xml_snippet: str) -> str:
        """Return a full mxfile XML string with a compressed <diagram>."""
        percent_encoded = urllib.parse.quote(xml_snippet, safe="")
        deflated = zlib.compress(percent_encoded.encode("utf-8"))[2:-4]  # strip zlib header/crc
        b64 = base64.b64encode(deflated).decode("ascii")
        return f'<mxfile><diagram name="Compressed">{b64}</diagram></mxfile>'

    def test_decompresses_correctly(self):
        inner_xml = (
            '<mxGraphModel pageWidth="800" pageHeight="600">'
            "<root>"
            '<mxCell id="0"/>'
            '<mxCell id="1" parent="0"/>'
            '<mxCell id="2" value="Compressed Shape" style="" vertex="1" parent="1">'
            '<mxGeometry x="10" y="10" width="100" height="50" as="geometry"/>'
            "</mxCell>"
            "</root>"
            "</mxGraphModel>"
        )
        mxfile_xml = self._make_compressed(inner_xml)
        diagram = parse_string(mxfile_xml, source_path="<test>")
        assert len(diagram.pages) == 1
        assert len(diagram.pages[0].shapes) == 1
        assert diagram.pages[0].shapes[0].label == "Compressed Shape"


# ---------------------------------------------------------------------------
# Label cleaning
# ---------------------------------------------------------------------------

class TestCleanLabel:
    def test_strips_br(self):
        assert _clean_label("line1<br/>line2") == "line1\nline2"

    def test_strips_html_tags(self):
        assert _clean_label("<b>bold</b>") == "bold"

    def test_decodes_entities(self):
        assert _clean_label("A &amp; B") == "A & B"
        assert _clean_label("&lt;tag&gt;") == "<tag>"

    def test_empty(self):
        assert _clean_label("") == ""

    def test_whitespace_stripped(self):
        assert _clean_label("  hello  ") == "hello"
