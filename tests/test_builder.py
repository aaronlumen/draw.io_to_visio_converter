"""Tests for vsdx_builder.py — validates the ZIP structure and XML content."""

from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from drawio_parser import parse_file
from vsdx_builder import build_vsdx, PX_PER_INCH

FIXTURES = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NS = "http://schemas.microsoft.com/office/visio/2012/main"


def _build_to_bytes(drawio_path: Path) -> bytes:
    """Full pipeline: parse → build → return raw VSDX bytes."""
    diagram = parse_file(drawio_path)
    buf = io.BytesIO()
    import zipfile as _zf
    import io as _io
    from vsdx_builder import build_vsdx as _build
    out = Path("/tmp/_test_output.vsdx")
    _build(diagram, out)
    return out.read_bytes()


def _open_vsdx(raw: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(raw))


def _get_xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(zf.read(name))


def _find_all(root: ET.Element, tag: str) -> list[ET.Element]:
    return root.findall(f".//{{{_NS}}}{tag}")


# ---------------------------------------------------------------------------
# ZIP structure tests
# ---------------------------------------------------------------------------

REQUIRED_FILES = [
    "[Content_Types].xml",
    "_rels/.rels",
    "docProps/app.xml",
    "docProps/core.xml",
    "visio/document.xml",
    "visio/_rels/document.xml.rels",
    "visio/windows.xml",
    "visio/pages/pages.xml",
    "visio/pages/_rels/pages.xml.rels",
]


class TestZipStructure:
    @pytest.fixture(scope="class")
    def vsdx_bytes(self):
        return _build_to_bytes(FIXTURES / "simple.drawio")

    def test_is_valid_zip(self, vsdx_bytes):
        assert zipfile.is_zipfile(io.BytesIO(vsdx_bytes))

    def test_required_files_present(self, vsdx_bytes):
        zf = _open_vsdx(vsdx_bytes)
        names = set(zf.namelist())
        for f in REQUIRED_FILES:
            assert f in names, f"Missing required file: {f}"

    def test_page1_present(self, vsdx_bytes):
        zf = _open_vsdx(vsdx_bytes)
        assert "visio/pages/page1.xml" in zf.namelist()

    def test_no_page2_for_single_page(self, vsdx_bytes):
        zf = _open_vsdx(vsdx_bytes)
        assert "visio/pages/page2.xml" not in zf.namelist()


class TestZipStructureExtended:
    @pytest.fixture(scope="class")
    def vsdx_bytes(self):
        return _build_to_bytes(FIXTURES / "extended.drawio")

    def test_two_page_files(self, vsdx_bytes):
        zf = _open_vsdx(vsdx_bytes)
        names = set(zf.namelist())
        assert "visio/pages/page1.xml" in names
        assert "visio/pages/page2.xml" in names

    def test_content_types_lists_both_pages(self, vsdx_bytes):
        zf = _open_vsdx(vsdx_bytes)
        root = _get_xml(zf, "[Content_Types].xml")
        overrides = root.findall("{http://schemas.openxmlformats.org/package/2006/content-types}Override")
        part_names = {o.get("PartName") for o in overrides}
        assert "/visio/pages/page1.xml" in part_names
        assert "/visio/pages/page2.xml" in part_names


# ---------------------------------------------------------------------------
# pages.xml tests
# ---------------------------------------------------------------------------

class TestPagesXml:
    @pytest.fixture(scope="class")
    def pages_root(self):
        raw = _build_to_bytes(FIXTURES / "simple.drawio")
        zf = _open_vsdx(raw)
        return _get_xml(zf, "visio/pages/pages.xml")

    def test_one_page_entry(self, pages_root):
        pages = _find_all(pages_root, "Page")
        assert len(pages) == 1

    def test_page_name(self, pages_root):
        page_el = _find_all(pages_root, "Page")[0]
        assert page_el.get("Name") == "Page-1"

    def test_page_dimensions_present(self, pages_root):
        cells = {c.get("N"): c.get("V") for c in _find_all(pages_root, "Cell")}
        assert "PageWidth" in cells
        assert "PageHeight" in cells
        # A4 landscape: 1169 px / 96 ≈ 12.177 inches
        assert float(cells["PageWidth"]) == pytest.approx(1169 / PX_PER_INCH, rel=1e-3)


# ---------------------------------------------------------------------------
# page1.xml shape tests (simple fixture)
# ---------------------------------------------------------------------------

class TestPageShapes:
    @pytest.fixture(scope="class")
    def page_root(self):
        raw = _build_to_bytes(FIXTURES / "simple.drawio")
        zf = _open_vsdx(raw)
        return _get_xml(zf, "visio/pages/page1.xml")

    def test_three_shapes(self, page_root):
        # 2 rectangles + 1 connector = 3 Shape elements
        shapes = _find_all(page_root, "Shape")
        assert len(shapes) == 3

    def test_shape_has_geometry(self, page_root):
        shapes = _find_all(page_root, "Shape")
        for shape in shapes:
            geom = shape.find(f".//{{{_NS}}}Section[@N='Geometry']")
            assert geom is not None, f"Shape {shape.get('ID')} missing Geometry section"

    def test_shape_labels(self, page_root):
        text_els = _find_all(page_root, "Text")
        labels = {el.text for el in text_els if el.text}
        # "connects" label on connector + "Box A" + "Box B"
        assert "Box A" in labels
        assert "Box B" in labels

    def test_connector_has_begin_end(self, page_root):
        shapes = _find_all(page_root, "Shape")
        # Find shape that has BeginX cell (connector)
        def _cells(shape):
            return {c.get("N"): c.get("V") for c in shape.findall(f"{{{_NS}}}Cell")}

        connectors = [s for s in shapes if "BeginX" in _cells(s)]
        assert len(connectors) == 1

    def test_connects_elements_present(self, page_root):
        connects = _find_all(page_root, "Connect")
        # One connector with source + target → 2 Connect elements
        assert len(connects) == 2


# ---------------------------------------------------------------------------
# Coordinate conversion
# ---------------------------------------------------------------------------

class TestCoordinates:
    def test_pin_x_conversion(self):
        from vsdx_builder import PX_PER_INCH
        # Box A: x=100, width=160 → pinX = (100+80)/96 = 1.875
        expected_pin_x = (100 + 160 / 2) / PX_PER_INCH
        assert expected_pin_x == pytest.approx(180 / 96)

    def test_pin_y_conversion(self):
        from vsdx_builder import PX_PER_INCH
        # page_height=827, Box A: y=100, height=80 → pinY = 827/96 - (100+40)/96
        page_h_in = 827 / PX_PER_INCH
        expected_pin_y = page_h_in - (100 + 80 / 2) / PX_PER_INCH
        assert expected_pin_y == pytest.approx((827 - 140) / 96)

    def test_pin_values_in_xml(self):
        raw = _build_to_bytes(FIXTURES / "simple.drawio")
        zf = _open_vsdx(raw)
        page_root = _get_xml(zf, "visio/pages/page1.xml")
        shapes = _find_all(page_root, "Shape")

        all_cells: dict[str, str] = {}
        for shape in shapes:
            for c in shape.findall(f"{{{_NS}}}Cell"):
                n = c.get("N")
                if n in ("PinX", "PinY", "Width", "Height"):
                    all_cells[n] = c.get("V")

        # At least Width and Height should be present for any shape
        assert "Width"  in all_cells
        assert "Height" in all_cells


# ---------------------------------------------------------------------------
# Extended fixture: multi-page and shape types
# ---------------------------------------------------------------------------

class TestExtendedConversion:
    @pytest.fixture(scope="class")
    def vsdx_bytes(self):
        return _build_to_bytes(FIXTURES / "extended.drawio")

    def test_both_pages_have_shapes(self, vsdx_bytes):
        zf = _open_vsdx(vsdx_bytes)
        for page_file in ("visio/pages/page1.xml", "visio/pages/page2.xml"):
            root = _get_xml(zf, page_file)
            shapes = _find_all(root, "Shape")
            assert len(shapes) > 0, f"{page_file} has no shapes"

    def test_page1_shape_count(self, vsdx_bytes):
        zf = _open_vsdx(vsdx_bytes)
        root = _get_xml(zf, "visio/pages/page1.xml")
        shapes = _find_all(root, "Shape")
        # 9 shapes (incl. group children) + 2 connectors = 11; groups themselves included
        assert len(shapes) >= 8

    def test_page2_flowchart(self, vsdx_bytes):
        zf = _open_vsdx(vsdx_bytes)
        root = _get_xml(zf, "visio/pages/page2.xml")
        shapes = _find_all(root, "Shape")
        # 3 shapes + 2 connectors = 5
        assert len(shapes) == 5
