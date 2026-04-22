# draw.io to Visio Converter

Converts [draw.io](https://www.drawio.com/) `.drawio` files to Microsoft Visio `.vsdx` files.

## Features

- Converts all common shape types: rectangles, ellipses, diamonds, parallelograms, triangles, cylinders, hexagons
- Preserves fill colours, stroke colours, stroke width, and dashed lines
- Converts connectors with arrow heads and optional waypoints (bend-points)
- Maps text labels including bold and italic font styles
- Handles multi-page diagrams
- Handles group shapes (children are resolved to absolute page coordinates)
- Supports both plain and compressed `.drawio` files (as exported by the draw.io web editor)
- Single external dependency: [`click`](https://click.palletsprojects.com/)

## Requirements

- Python 3.10 or later

## Installation

```bash
git clone https://github.com/your-username/draw.io_to_visio_converter.git
cd draw.io_to_visio_converter

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

## Usage

### Basic conversion

```bash
python converter.py my_diagram.drawio
```

This creates `my_diagram.vsdx` in the same directory.

### Specify output path

```bash
python converter.py my_diagram.drawio -o path/to/output.vsdx
```

### Verbose mode

```bash
python converter.py my_diagram.drawio --verbose
```

Prints the number of pages, shapes, and connectors found in the source file.

### Help

```bash
python converter.py --help
```

## Supported draw.io elements

| draw.io element | Converted to VSDX |
|---|---|
| Rectangle (default shape) | Rectangle geometry |
| Ellipse (`ellipse=1` or `shape=ellipse`) | Ellipse geometry |
| Diamond / rhombus (`rhombus=1`) | Diamond geometry |
| Parallelogram (`shape=parallelogram`) | Parallelogram geometry |
| Triangle (`triangle=1`) | Triangle geometry |
| Cylinder (`shape=cylinder3`) | Cylinder geometry |
| Hexagon (`shape=hexagon`) | Hexagon geometry |
| Any unknown shape | Falls back to rectangle |
| Fill colour (`fillColor`) | `FillForegnd` cell |
| No fill (`fillColor=none`) | `FillPattern=0` |
| Stroke colour (`strokeColor`) | `LineColor` cell |
| Stroke width (`strokeWidth`) | `LineWeight` cell |
| Dashed stroke (`dashed=1`) | `LinePattern=2` |
| Rounded corners (`rounded=1`) | `Rounding` cell |
| Opacity (`opacity`) | `FillForegndTrans` cell |
| Text label (`value`) | `<Text>` element |
| Bold / italic (`fontStyle`) | `Character/Style` cell |
| Font size (`fontSize`) | `Character/Size` cell |
| Font colour (`fontColor`) | `Character/Color` cell |
| Connectors / edges | Shape with `BeginX`/`EndX` + `<Connects>` glue |
| Arrow heads (`endArrow`, `startArrow`) | `EndArrow` / `BeginArrow` cells |
| Waypoints on connectors | `LineTo` rows in Geometry section |
| Multiple pages | One `pageN.xml` per page |
| Groups (parent-child cells) | Children resolved to absolute coordinates |
| Compressed diagrams (web editor export) | Auto-detected and decompressed |

## Not yet supported

- Swimlanes
- Embedded images
- Visio masters / stencils
- Custom line-end styles beyond classic, open, and block arrows

## Running the tests

```bash
pip install pytest          # if not already installed
python -m pytest tests/ -v
```

## Project structure

```
draw.io_to_visio_converter/
├── converter.py        # CLI entry point
├── models.py           # Intermediate data model (Diagram, Page, Shape, Connector)
├── drawio_parser.py    # Parses .drawio XML → data model
├── style_mapper.py     # Maps draw.io style strings to VSDX cell values
├── shape_geometry.py   # Builds Visio Geometry sections per shape type
├── vsdx_builder.py     # Assembles and writes the VSDX ZIP archive
├── requirements.txt
└── tests/
    ├── fixtures/
    │   ├── simple.drawio     # 2 shapes + 1 connector
    │   └── extended.drawio   # All shape types, groups, multi-page
    ├── test_parser.py
    └── test_builder.py
```

## How it works

1. **Parse** — `drawio_parser.py` reads the `.drawio` XML (decompressing if needed) and converts it to plain Python dataclasses (`models.py`). All coordinates remain in draw.io pixel units at this stage.

2. **Map styles** — `style_mapper.py` parses each shape's semicolon-separated style string into a dict and extracts colours, line styles, font settings, and shape type.

3. **Build geometry** — `shape_geometry.py` generates the `MoveTo` / `LineTo` / `Ellipse` rows that describe each shape's outline in Visio's local coordinate system.

4. **Write VSDX** — `vsdx_builder.py` converts pixel coordinates to inches (`÷ 96`) and flips the Y-axis (draw.io: top-left origin → Visio: bottom-left origin), then assembles all required OPC/ZIP parts:
   - `[Content_Types].xml`, `_rels/.rels`, `docProps/` metadata
   - `visio/document.xml` (fonts, default styles)
   - `visio/pages/pages.xml` + one `visio/pages/pageN.xml` per page
   - `<Connects>` elements to glue connector endpoints to shapes

## License

MIT
