"""
Visio stencil support.

Lets the user pick which draw.io shapes are rendered as masters taken from a
Visio stencil (.vssx / .vsdx / .vssm / .vstx) instead of the built-in inline
geometry.  Shapes are matched by rules supplied by the user (JSON file or CLI
``--map`` options); the first matching rule wins.  Unmatched shapes are left
untouched and keep the default inline rendering.

Rule match keys (all given keys must match):
    style       substring of any ``key=value`` entry in the draw.io style
    label       regular expression searched in the shape label
    id          exact draw.io cell id
    shape_type  exact converter shape type (rectangle, ellipse, ...)

Known limitations: master-level relationships (embedded images/OLE) and font
tables of the source stencil are not carried over.
"""

from __future__ import annotations

import copy
import json
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from models import Diagram, Shape

_NS = "http://schemas.microsoft.com/office/visio/2012/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

REL_MASTERS = "http://schemas.microsoft.com/visio/2010/relationships/masters"
REL_MASTER = "http://schemas.microsoft.com/visio/2010/relationships/master"
CT_MASTERS = "application/vnd.ms-visio.masters+xml"
CT_MASTER = "application/vnd.ms-visio.master+xml"

_STYLE_ATTRS = ("LineStyle", "FillStyle", "TextStyle")
_STYLE_ID_STRIDE = 1000  # style-id offset per stencil, avoids clashes


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class StencilError(Exception):
    """Raised for unreadable stencils or invalid rule files."""


# ---------------------------------------------------------------------------
# Stencil loading
# ---------------------------------------------------------------------------

@dataclass
class MasterInfo:
    name: str
    name_u: str
    element: ET.Element          # the <Master> element from masters.xml
    content: ET.Element          # parsed masterN.xml (<MasterContents>)


@dataclass
class Stencil:
    path: Path
    masters: list[MasterInfo] = field(default_factory=list)
    style_sheets: list[ET.Element] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "Stencil":
        path = Path(path)
        try:
            zf = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise StencilError(f"Cannot open stencil {path}: {exc}") from exc

        with zf:
            names = set(zf.namelist())
            masters_part = "visio/masters/masters.xml"
            if masters_part not in names:
                raise StencilError(f"{path} contains no masters")

            rels: dict[str, str] = {}
            rels_part = "visio/masters/_rels/masters.xml.rels"
            if rels_part in names:
                for rel in ET.fromstring(zf.read(rels_part)):
                    rels[rel.get("Id", "")] = rel.get("Target", "")

            st = cls(path=path)
            for m_el in ET.fromstring(zf.read(masters_part)):
                if _local(m_el.tag) != "Master":
                    continue
                rel_el = next((c for c in m_el if _local(c.tag) == "Rel"), None)
                rid = rel_el.get(f"{{{_NS_R}}}id") if rel_el is not None else None
                target = rels.get(rid or "")
                if not target:
                    continue
                part = posixpath.normpath(
                    posixpath.join("visio/masters", target))
                if part not in names:
                    continue
                st.masters.append(MasterInfo(
                    name=m_el.get("Name", ""),
                    name_u=m_el.get("NameU", m_el.get("Name", "")),
                    element=m_el,
                    content=ET.fromstring(zf.read(part)),
                ))

            if "visio/document.xml" in names:
                doc = ET.fromstring(zf.read("visio/document.xml"))
                for sheets in doc:
                    if _local(sheets.tag) == "StyleSheets":
                        st.style_sheets = [s for s in sheets
                                           if _local(s.tag) == "StyleSheet"]
        return st

    def find_master(self, name: str) -> Optional[MasterInfo]:
        key = name.casefold()
        for m in self.masters:
            if m.name.casefold() == key or m.name_u.casefold() == key:
                return m
        return None

    def master_names(self) -> list[str]:
        return [m.name or m.name_u for m in self.masters]


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    master: str
    style: Optional[str] = None
    label: Optional[re.Pattern] = None
    id: Optional[str] = None
    shape_type: Optional[str] = None
    stencil: Optional[str] = None      # stencil key; None = search all

    def matches(self, shape: Shape) -> bool:
        if self.id is not None and shape.id != self.id:
            return False
        if self.shape_type is not None and shape.shape_type != self.shape_type:
            return False
        if self.label is not None and not self.label.search(shape.label or ""):
            return False
        if self.style is not None and not any(
                self.style in f"{k}={v}" for k, v in shape.style.items()):
            return False
        return True


def rule_from_dict(d: dict) -> Rule:
    match = d.get("match") or {}
    master = d.get("master")
    if not master:
        raise StencilError(f"Rule is missing 'master': {d}")
    unknown = set(match) - {"style", "label", "id", "shape_type"}
    if unknown:
        raise StencilError(f"Unknown match keys {sorted(unknown)} in {d}")
    if not match:
        raise StencilError(f"Rule has an empty 'match': {d}")
    try:
        label = re.compile(match["label"]) if "label" in match else None
    except re.error as exc:
        raise StencilError(f"Bad label regex in {d}: {exc}") from exc
    return Rule(master=master, style=match.get("style"), label=label,
                id=match.get("id"), shape_type=match.get("shape_type"),
                stencil=d.get("stencil"))


def parse_map_option(text: str) -> Rule:
    """Parse a CLI ``KIND:PATTERN=MASTER`` string into a Rule."""
    if ":" not in text or "=" not in text.split(":", 1)[1]:
        raise StencilError(
            f"Bad --map {text!r}; expected KIND:PATTERN=MASTER "
            "(KIND is style, label, id or shape_type)")
    kind, rest = text.split(":", 1)
    pattern, master = rest.rsplit("=", 1)
    return rule_from_dict({"match": {kind.strip(): pattern}, "master": master})


# ---------------------------------------------------------------------------
# Config: stencils + rules
# ---------------------------------------------------------------------------

@dataclass
class StencilConfig:
    stencils: dict[str, Stencil] = field(default_factory=dict)
    rules: list[Rule] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_stencil(self, path: str | Path, key: Optional[str] = None) -> None:
        key = key or Path(path).stem
        self.stencils[key] = Stencil.load(path)

    def load_rules_file(self, path: str | Path) -> None:
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StencilError(f"Cannot read rules file {path}: {exc}") from exc
        for key, rel in (data.get("stencils") or {}).items():
            p = Path(rel)
            self.add_stencil(p if p.is_absolute() else path.parent / p, key)
        for r in data.get("rules") or []:
            self.rules.append(rule_from_dict(r))

    def _resolve(self, rule: Rule) -> Optional[tuple[str, MasterInfo]]:
        if rule.stencil is not None:
            candidates: Iterable[str] = [rule.stencil]
            if rule.stencil not in self.stencils:
                self.warnings.append(
                    f"Rule references unknown stencil {rule.stencil!r}")
                return None
        else:
            candidates = self.stencils.keys()
        for key in candidates:
            m = self.stencils[key].find_master(rule.master)
            if m is not None:
                return key, m
        return None

    def assign(self, diagram: Diagram) -> int:
        """Set ``shape.master_ref`` on every shape matched by a rule.

        Returns the number of shapes assigned.  Rules whose master cannot be
        found produce a warning (once) and are skipped.
        """
        resolved: dict[int, Optional[tuple[str, MasterInfo]]] = {}
        warned: set[int] = set()
        count = 0
        for page in diagram.pages:
            for shape in page.shapes:
                for i, rule in enumerate(self.rules):
                    if not rule.matches(shape):
                        continue
                    if i not in resolved:
                        resolved[i] = self._resolve(rule)
                    hit = resolved[i]
                    if hit is None:
                        if i not in warned:
                            warned.add(i)
                            self.warnings.append(
                                f"Master {rule.master!r} not found in "
                                f"{'stencil ' + rule.stencil if rule.stencil else 'any loaded stencil'}")
                        continue
                    shape.master_ref = (hit[0], hit[1].name or hit[1].name_u)
                    count += 1
                    break
        return count


# ---------------------------------------------------------------------------
# Output side: collect used masters and write the OPC parts
# ---------------------------------------------------------------------------

class MasterBundle:
    """Masters actually used by a diagram, with their output IDs."""

    def __init__(self, config: StencilConfig):
        self.config = config
        self.ids: dict[tuple[str, str], int] = {}
        self._order: list[tuple[str, MasterInfo]] = []

    @classmethod
    def from_diagram(cls, diagram: Diagram,
                     config: StencilConfig) -> "MasterBundle":
        config.assign(diagram)
        bundle = cls(config)
        for page in diagram.pages:
            for shape in page.shapes:
                if shape.master_ref:
                    bundle._register(shape.master_ref)
        return bundle

    def _register(self, ref: tuple[str, str]) -> int:
        if ref not in self.ids:
            key, name = ref
            master = self.config.stencils[key].find_master(name)
            self._order.append((key, master))  # type: ignore[arg-type]
            self.ids[ref] = len(self._order)
        return self.ids[ref]

    def __bool__(self) -> bool:
        return bool(self._order)

    def _offset(self, key: str) -> int:
        return _STYLE_ID_STRIDE * (list(self.config.stencils).index(key) + 1)

    @staticmethod
    def _remap(el: ET.Element, offset: int) -> None:
        for node in el.iter():
            for attr in _STYLE_ATTRS:
                v = node.get(attr)
                if v is not None and v.lstrip("-").isdigit():
                    node.set(attr, str(int(v) + offset))

    def style_sheets(self) -> list[ET.Element]:
        """Stylesheets of the used stencils, with IDs shifted past ours."""
        out: list[ET.Element] = []
        seen: set[str] = set()
        for key, _m in self._order:
            if key in seen:
                continue
            seen.add(key)
            off = self._offset(key)
            for ss in self.config.stencils[key].style_sheets:
                clone = copy.deepcopy(ss)
                self._remap(clone, off)
                clone.set("ID", str(int(ss.get("ID", "0")) + off))
                out.append(clone)
        return out

    def write_parts(self, zf: zipfile.ZipFile) -> None:
        ET.register_namespace("", _NS)
        ET.register_namespace("r", _NS_R)
        masters_root = ET.Element(f"{{{_NS}}}Masters")
        rels_root = ET.Element(f"{{{_NS_PKG_REL}}}Relationships")

        for out_id, (key, m) in enumerate(self._order, start=1):
            off = self._offset(key)
            m_el = copy.deepcopy(m.element)
            self._remap(m_el, off)
            m_el.set("ID", str(out_id))
            for child in m_el:
                if _local(child.tag) == "Rel":
                    child.set(f"{{{_NS_R}}}id", f"rId{out_id}")
            masters_root.append(m_el)
            ET.SubElement(rels_root, f"{{{_NS_PKG_REL}}}Relationship",
                          Id=f"rId{out_id}", Type=REL_MASTER,
                          Target=f"master{out_id}.xml")
            content = copy.deepcopy(m.content)
            self._remap(content, off)
            zf.writestr(f"visio/masters/master{out_id}.xml", _xml(content))

        zf.writestr("visio/masters/masters.xml", _xml(masters_root))
        zf.writestr("visio/masters/_rels/masters.xml.rels", _xml(rels_root))

    def content_type_overrides(self) -> list[tuple[str, str]]:
        parts = [("/visio/masters/masters.xml", CT_MASTERS)]
        parts += [(f"/visio/masters/master{i}.xml", CT_MASTER)
                  for i in range(1, len(self._order) + 1)]
        return parts


def _xml(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            + ET.tostring(root, encoding="unicode"))
