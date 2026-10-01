"""
CLI entry point for the draw.io → VSDX converter.

Usage:
    python converter.py input.drawio
    python converter.py input.drawio -o output.vsdx
    python converter.py input.drawio --verbose
    python converter.py input.drawio --stencil net.vssx --map 'label:^DB-=Database'
    python converter.py input.drawio --stencil-map rules.json
    python converter.py --list-masters net.vssx
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from drawio_parser import parse_file
from stencil import Stencil, StencilConfig, StencilError, parse_map_option
from vsdx_builder import build_vsdx


def _list_masters(ctx: click.Context, _param, value) -> None:
    if not value:
        return
    try:
        for name in Stencil.load(value).master_names():
            click.echo(name)
    except StencilError as exc:
        click.echo(f"Error: {exc}", err=True)
        ctx.exit(1)
    ctx.exit(0)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("input_file", metavar="INPUT", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", "output_file",
              default=None,
              type=click.Path(dir_okay=False, writable=True, path_type=Path),
              help="Output .vsdx path.  Defaults to INPUT with .vsdx extension.")
@click.option("-v", "--verbose", is_flag=True, default=False,
              help="Print conversion details.")
@click.option("--stencil", "stencil_files", multiple=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Visio stencil (.vssx/.vsdx) to draw masters from. Repeatable.")
@click.option("--map", "map_rules", multiple=True, metavar="KIND:PATTERN=MASTER",
              help="Render shapes matching KIND:PATTERN (style, label regex, id, "
                   "shape_type) as MASTER from the loaded stencils. Repeatable; "
                   "first match wins.")
@click.option("--stencil-map", "stencil_map", default=None,
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="JSON file with 'stencils' and 'rules' (see README).")
@click.option("--list-masters", metavar="STENCIL", is_eager=True, expose_value=False,
              callback=_list_masters,
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="List master names in a stencil and exit.")
def convert_cmd(input_file: Path, output_file: Path | None, verbose: bool,
                stencil_files: tuple[Path, ...], map_rules: tuple[str, ...],
                stencil_map: Path | None) -> None:
    """Convert a draw.io file (INPUT) to a Visio VSDX file."""
    if output_file is None:
        output_file = input_file.with_suffix(".vsdx")

    if verbose:
        click.echo(f"Parsing  {input_file}")

    try:
        diagram = parse_file(input_file)
    except Exception as exc:
        click.echo(f"Error parsing {input_file}: {exc}", err=True)
        sys.exit(1)

    if verbose:
        total_shapes = sum(len(p.shapes) for p in diagram.pages)
        total_conns  = sum(len(p.connectors) for p in diagram.pages)
        click.echo(
            f"  Pages : {len(diagram.pages)}\n"
            f"  Shapes: {total_shapes}\n"
            f"  Edges : {total_conns}"
        )

    config: StencilConfig | None = None
    if stencil_files or map_rules or stencil_map:
        config = StencilConfig()
        try:
            for f in stencil_files:
                config.add_stencil(f)
            if stencil_map:
                config.load_rules_file(stencil_map)
            config.rules.extend(parse_map_option(m) for m in map_rules)
        except StencilError as exc:
            click.echo(f"Stencil error: {exc}", err=True)
            sys.exit(1)

    try:
        build_vsdx(diagram, output_file, config)
    except Exception as exc:
        click.echo(f"Error building {output_file}: {exc}", err=True)
        sys.exit(1)

    if config:
        for w in config.warnings:
            click.echo(f"Warning: {w}", err=True)

    click.echo(f"Written {output_file}")


if __name__ == "__main__":
    convert_cmd()
