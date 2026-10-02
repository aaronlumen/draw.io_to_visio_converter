"""Tests for stencil (master) support."""
import json
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from converter import convert_cmd
from drawio_parser import parse_file
from stencil import Stencil, StencilConfig, StencilError, parse_map_option
from vsdx_builder import build_vsdx

FIXTURES = Path(__file__).parent / "fixtures"
NS = "http://schemas.microsoft.com/office/visio/2012/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def make_stencil(path: Path, names=("Router", "Database")) -> Path:
    masters = "".join(
        f'<Master ID="{i}" NameU="{n}" Name="{n}" LineStyle="1">'
        f'<Rel xmlns:r="{R}" r:id="rId{i}"/></Master>'
        for i, n in enumerate(names, 1))
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="x" Target="master{i}.xml"/>'
        for i in range(1, len(names) + 1))
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("visio/document.xml",
                    f'<VisioDocument xmlns="{NS}"><StyleSheets>'
                    '<StyleSheet ID="0" Name="No Style"/>'
                    '<StyleSheet ID="1" Name="S" LineStyle="0"/>'
                    '</StyleSheets></VisioDocument>')
        zf.writestr("visio/masters/masters.xml",
                    f'<Masters xmlns="{NS}">{masters}</Masters>')
        zf.writestr("visio/masters/_rels/masters.xml.rels",
                    f'<Relationships xmlns="http://schemas.openxmlformats.org/'
                    f'package/2006/relationships">{rels}</Relationships>')
        for i in range(1, len(names) + 1):
            zf.writestr(f"visio/masters/master{i}.xml",
                        f'<MasterContents xmlns="{NS}"><Shapes>'
                        f'<Shape ID="1" LineStyle="1"/></Shapes></MasterContents>')
        zf.writestr("visio/media/image1.emf", b"EMFDATA")
        zf.writestr("visio/masters/_rels/master1.xml.rels",
                    '<Relationships xmlns="http://schemas.openxmlformats.org/'
                    'package/2006/relationships"><Relationship Id="rId1" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/'
                    '2006/relationships/image" Target="../media/image1.emf"/>'
                    '</Relationships>')
    return path


@pytest.fixture
def stencil_path(tmp_path):
    return make_stencil(tmp_path / "net.vssx")


@pytest.fixture
def diagram():
    return parse_file(FIXTURES / "simple.drawio")


def test_load_and_find(stencil_path):
    st = Stencil.load(stencil_path)
    assert st.master_names() == ["Router", "Database"]
    assert st.find_master("router") is not None
    assert st.find_master("nope") is None


def test_load_bad_file(tmp_path):
    bad = tmp_path / "bad.vssx"
    bad.write_text("not a zip")
    with pytest.raises(StencilError):
        Stencil.load(bad)


def test_parse_map_option():
    r = parse_map_option("label:^DB-=Database")
    assert r.master == "Database" and r.label.search("DB-1")
    with pytest.raises(StencilError):
        parse_map_option("garbage")


def test_assign_by_id_and_missing_master(stencil_path, diagram):
    shape = diagram.pages[0].shapes[0]
    cfg = StencilConfig()
    cfg.add_stencil(stencil_path)
    cfg.rules.append(parse_map_option(f"id:{shape.id}=Router"))
    cfg.rules.append(parse_map_option("shape_type:rectangle=Missing"))
    assert cfg.assign(diagram) == 1
    assert shape.master_ref == ("net", "Router")
    assert any("Missing" in w for w in cfg.warnings)


def _build(diagram, cfg, tmp_path):
    out = tmp_path / "out.vsdx"
    build_vsdx(diagram, out, cfg)
    return zipfile.ZipFile(out)


def test_build_with_master(stencil_path, diagram, tmp_path):
    shape = diagram.pages[0].shapes[0]
    cfg = StencilConfig()
    cfg.add_stencil(stencil_path)
    cfg.rules.append(parse_map_option(f"id:{shape.id}=Router"))
    zf = _build(diagram, cfg, tmp_path)
    names = zf.namelist()
    assert "visio/masters/masters.xml" in names
    assert "visio/masters/master1.xml" in names
    assert "visio/masters/master2.xml" not in names  # only used masters
    ct = zf.read("[Content_Types].xml").decode()
    assert "/visio/masters/master1.xml" in ct
    assert "masters/masters.xml" in zf.read("visio/_rels/document.xml.rels").decode()
    page = ET.fromstring(zf.read("visio/pages/page1.xml"))
    inst = [s for s in page.iter(f"{{{NS}}}Shape") if s.get("Master") == "1"]
    assert len(inst) == 1
    assert not list(inst[0].iter(f"{{{NS}}}Section"))  # no inline geometry
    # Style IDs remapped past the built-in stylesheet
    doc = ET.fromstring(zf.read("visio/document.xml"))
    ids = [s.get("ID") for s in doc.iter(f"{{{NS}}}StyleSheet")]
    assert len(ids) == len(set(ids)) == 3
    m = ET.fromstring(zf.read("visio/masters/master1.xml"))
    assert next(m.iter(f"{{{NS}}}Shape")).get("LineStyle") == "1001"
    for n in names:
        if n.endswith((".xml", ".rels")):
            ET.fromstring(zf.read(n))


def test_no_rules_matches_adds_no_masters(stencil_path, diagram, tmp_path):
    cfg = StencilConfig()
    cfg.add_stencil(stencil_path)
    zf = _build(diagram, cfg, tmp_path)
    assert not any(n.startswith("visio/masters") for n in zf.namelist())


def test_cli_map_and_list(stencil_path, tmp_path):
    runner = CliRunner()
    res = runner.invoke(convert_cmd, ["--list-masters", str(stencil_path)])
    assert res.exit_code == 0 and "Router" in res.output
    out = tmp_path / "o.vsdx"
    res = runner.invoke(convert_cmd, [
        str(FIXTURES / "simple.drawio"), "-o", str(out),
        "--stencil", str(stencil_path), "--map", "shape_type:rectangle=Database"])
    assert res.exit_code == 0, res.output
    assert "visio/masters/master1.xml" in zipfile.ZipFile(out).namelist()


def test_rules_file(stencil_path, diagram, tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({
        "stencils": {"n": stencil_path.name},
        "rules": [{"match": {"shape_type": "rectangle"},
                   "stencil": "n", "master": "Router"}]}))
    cfg = StencilConfig()
    cfg.load_rules_file(rules)
    assert cfg.assign(diagram) >= 1


def test_master_media_copied(stencil_path, diagram, tmp_path):
    shape = diagram.pages[0].shapes[0]
    cfg = StencilConfig()
    cfg.add_stencil(stencil_path)
    cfg.rules.append(parse_map_option(f"id:{shape.id}=Router"))
    zf = _build(diagram, cfg, tmp_path)
    assert zf.read("visio/media/m1_image1.emf") == b"EMFDATA"
    rels = zf.read("visio/masters/_rels/master1.xml.rels").decode()
    assert "../media/m1_image1.emf" in rels
    assert 'Extension="emf"' in zf.read("[Content_Types].xml").decode()
