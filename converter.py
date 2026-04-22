"""
CLI entry point for the draw.io → VSDX converter.

Usage:
    python converter.py input.drawio
    python converter.py input.drawio -o output.vsdx
    python converter.py input.drawio --verbose
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from drawio_parser import parse_file
from vsdx_builder import build_vsdx


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("input_file", metavar="INPUT", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", "output_file",
              default=None,
              type=click.Path(dir_okay=False, writable=True, path_type=Path),
              help="Output .vsdx path.  Defaults to INPUT with .vsdx extension.")
@click.option("-v", "--verbose", is_flag=True, default=False,
              help="Print conversion details.")
def convert_cmd(input_file: Path, output_file: Path | None, verbose: bool) -> None:
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

    try:
        build_vsdx(diagram, output_file)
    except Exception as exc:
        click.echo(f"Error building {output_file}: {exc}", err=True)
        sys.exit(1)

    click.echo(f"Written {output_file}")


if __name__ == "__main__":
    convert_cmd()
