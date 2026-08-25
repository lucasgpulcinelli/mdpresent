"""Command-line interface for mDpresent."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import ConfigError, write_default_theme
from .mermaid import MermaidError
from .renderer import RenderError, render_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdpresent",
        description="Render a themed PDF from Markdown with vector Mermaid diagrams.",
    )
    parser.add_argument("input", nargs="?", help="Markdown source file")
    parser.add_argument("-o", "--output", help="Output PDF path; defaults to INPUT.pdf")
    parser.add_argument("-c", "--config", help="YAML theme file")
    parser.add_argument(
        "--write-default-config",
        metavar="PATH",
        help="Write the built-in theme to PATH and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.write_default_config:
        path = write_default_theme(args.write_default_config)
        print(f"Wrote {path}")
        return 0
    if not args.input:
        parser.error("INPUT is required unless --write-default-config is used")

    try:
        output = render_markdown(args.input, args.output, args.config)
    except (ConfigError, MermaidError, RenderError, OSError, ValueError) as exc:
        print(f"mdpresent: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

