"""Mermaid CLI integration that keeps diagrams as vector drawings."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any
from xml.etree import ElementTree

from reportlab.graphics.shapes import Drawing
from svglib.svglib import svg2rlg

from .config import Theme


class MermaidError(RuntimeError):
    """Raised when Mermaid cannot render a diagram."""


@dataclass
class VectorAsset:
    drawing: Drawing
    width: float
    height: float
    source: Path


class MermaidRenderer:
    def __init__(self, theme: Theme, document_dir: Path, cache_dir: Path | None = None) -> None:
        self.theme = theme
        self.document_dir = document_dir
        self.settings = theme.data["mermaid"]
        self.cache_dir = cache_dir or Path.cwd() / ".cache" / "mdpresent"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cli = self._find_cli()
        self.chrome = self._find_chrome()

    def render(self, code: str) -> VectorAsset:
        config = self._mermaid_config()
        key_material = json.dumps(
            {"code": code, "config": config, "width": self.settings["render_width"]},
            sort_keys=True,
        )
        digest = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:24]
        svg_path = self.cache_dir / f"{digest}.svg"

        if not (self.settings.get("cache", True) and svg_path.is_file()):
            self._render_svg(code, config, svg_path)
        return load_svg_asset(svg_path)

    def _render_svg(self, code: str, config: dict[str, Any], destination: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="mdpresent-mermaid-") as raw_temp:
            temp_dir = Path(raw_temp)
            input_path = temp_dir / "diagram.mmd"
            output_path = temp_dir / "diagram.svg"
            config_path = temp_dir / "mermaid.json"
            puppeteer_path = temp_dir / "puppeteer.json"
            input_path.write_text(code + "\n", encoding="utf-8")
            config_path.write_text(json.dumps(config), encoding="utf-8")
            puppeteer_path.write_text(
                json.dumps(
                    {
                        "executablePath": str(self.chrome),
                        "args": ["--no-sandbox", "--disable-dev-shm-usage"],
                    }
                ),
                encoding="utf-8",
            )
            command = [
                str(self.cli),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--outputFormat",
                "svg",
                "--backgroundColor",
                "transparent",
                "--configFile",
                str(config_path),
                "--puppeteerConfigFile",
                str(puppeteer_path),
                "--width",
                str(self.settings["render_width"]),
                "--height",
                str(self.settings["render_height"]),
                "--quiet",
            ]
            result = subprocess.run(
                command,
                cwd=self.document_dir,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            if result.returncode != 0 or not output_path.is_file():
                detail = (result.stderr or result.stdout).strip()
                raise MermaidError(f"Mermaid rendering failed:\n{detail}")
            shutil.copyfile(output_path, destination)

    def _find_cli(self) -> Path:
        configured = self.settings.get("cli")
        if configured:
            path = self.theme.resolve_path(str(configured), self.document_dir)
            if path and path.is_file():
                return path
            raise MermaidError(f"Configured Mermaid CLI was not found: {path}")

        executable = shutil.which("mmdc")
        if executable:
            return Path(executable)

        candidates = [
            Path.cwd() / "node_modules" / ".bin" / "mmdc",
            Path(__file__).resolve().parents[2] / "node_modules" / ".bin" / "mmdc",
            self.document_dir / "node_modules" / ".bin" / "mmdc",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise MermaidError(
            "Mermaid CLI is not installed. Run `npm install` in the mDpresent project "
            "or set mermaid.cli in the theme."
        )

    def _find_chrome(self) -> Path:
        configured = self.settings.get("chrome")
        if configured:
            path = Path(str(configured)).expanduser()
            if path.is_file():
                return path
            raise MermaidError(f"Configured Chrome executable was not found: {path}")
        for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
            executable = shutil.which(name)
            if executable:
                return Path(executable)
        raise MermaidError("Chrome or Chromium is required to render Mermaid diagrams")

    def _mermaid_config(self) -> dict[str, Any]:
        color = self.theme.color_hex
        return {
            "theme": "base",
            "securityLevel": "strict",
            "fontFamily": "DejaVu Sans, Arial, sans-serif",
            "htmlLabels": False,
            "themeCSS": (
                f".arrowheadPath {{ fill: {color('diagram_line')} !important; }} "
                f"[data-look='neo'] {{ filter: none !important; }}"
            ),
            "flowchart": {"htmlLabels": False, "curve": "basis", "useMaxWidth": False},
            "sequence": {"useMaxWidth": False},
            "themeVariables": {
                "background": color("diagram_background"),
                "mainBkg": color("diagram_primary"),
                "primaryColor": color("diagram_primary"),
                "primaryTextColor": color("diagram_text"),
                "primaryBorderColor": color("accent_secondary"),
                "secondaryColor": color("diagram_secondary"),
                "secondaryTextColor": color("diagram_text"),
                "secondaryBorderColor": color("accent"),
                "tertiaryColor": color("diagram_tertiary"),
                "tertiaryTextColor": color("diagram_text"),
                "tertiaryBorderColor": color("accent_secondary"),
                "lineColor": color("diagram_line"),
                "textColor": color("diagram_text"),
                "nodeTextColor": color("diagram_text"),
                "actorBkg": color("diagram_primary"),
                "actorBorder": color("accent_secondary"),
                "actorTextColor": color("diagram_text"),
                "actorLineColor": color("diagram_line"),
                "signalColor": color("diagram_line"),
                "signalTextColor": color("diagram_text"),
                "labelBoxBkgColor": color("diagram_secondary"),
                "labelBoxBorderColor": color("accent"),
                "labelTextColor": color("diagram_text"),
                "loopTextColor": color("diagram_text"),
                "noteBkgColor": color("diagram_note"),
                "noteBorderColor": color("accent"),
                "noteTextColor": color("diagram_text"),
                "edgeLabelBackground": color("diagram_background"),
                "clusterBkg": color("diagram_tertiary"),
                "clusterBorder": color("accent_secondary"),
                "titleColor": color("heading_text"),
                "fontSize": f"{float(self.settings['font_size']):g}px",
            },
        }


def load_svg_asset(path: Path) -> VectorAsset:
    prepared = _prepare_svg_for_svglib(path)
    drawing = svg2rlg(str(prepared))
    if prepared != path:
        prepared.unlink(missing_ok=True)
    if drawing is None:
        raise MermaidError(f"Could not parse SVG output: {path}")
    width, height = _svg_viewbox_size(path)
    if width <= 0 or height <= 0:
        width = float(drawing.width)
        height = float(drawing.height)
    _sanitize_dash_arrays(drawing)
    return VectorAsset(drawing=drawing, width=width, height=height, source=path)


def _prepare_svg_for_svglib(path: Path) -> Path:
    """Flatten Mermaid's nested tspans and remove invalid solid dash arrays."""
    source = path.read_text(encoding="utf-8")
    source = re_sub_solid_dash(source)
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError:
        return path

    changed = False
    for element in root.iter():
        if _local_name(element.tag) != "tspan":
            continue
        classes = element.attrib.get("class", "").split()
        children = list(element)
        if "text-outer-tspan" not in classes or not children:
            continue
        combined = "".join(element.itertext())
        for child in children:
            element.remove(child)
        element.text = combined
        y = _svg_length(element.attrib.get("y"), 16.0)
        dy = _svg_length(element.attrib.get("dy"), 16.0)
        if y is not None or dy is not None:
            element.attrib["y"] = str((y or 0.0) + (dy or 0.0))
            element.attrib.pop("dy", None)
        changed = True
    if not changed and source == path.read_text(encoding="utf-8"):
        return path

    with tempfile.NamedTemporaryFile(
        suffix=".svg",
        prefix="mdpresent-svg-",
        delete=False,
        mode="wb",
    ) as stream:
        ElementTree.ElementTree(root).write(stream, encoding="utf-8", xml_declaration=True)
        return Path(stream.name)


def re_sub_solid_dash(source: str) -> str:
    import re

    return re.sub(r"stroke-dasharray\s*:\s*0(?:\.0+)?\s*;", "stroke-dasharray:none;", source)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _svg_length(value: str | None, font_size: float) -> float | None:
    if value is None:
        return None
    value = value.strip()
    try:
        if value.endswith("em"):
            return float(value[:-2]) * font_size
        if value.endswith("px"):
            return float(value[:-2])
        return float(value)
    except ValueError:
        return None


def _sanitize_dash_arrays(node: Any) -> None:
    """Discard zero-length SVG dash values that ReportLab rejects."""
    dash = getattr(node, "strokeDashArray", None)
    if isinstance(dash, (list, tuple)):
        cleaned = [float(value) for value in dash if float(value) > 0]
        node.strokeDashArray = cleaned or None
    get_contents = getattr(node, "getContents", None)
    if callable(get_contents):
        for child in get_contents():
            _sanitize_dash_arrays(child)


def _svg_viewbox_size(path: Path) -> tuple[float, float]:
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError):
        return 0.0, 0.0
    viewbox = root.attrib.get("viewBox", "").replace(",", " ").split()
    if len(viewbox) == 4:
        try:
            return float(viewbox[2]), float(viewbox[3])
        except ValueError:
            pass
    return _number(root.attrib.get("width")), _number(root.attrib.get("height"))


def _number(value: str | None) -> float:
    if not value:
        return 0.0
    filtered = "".join(char for char in value if char.isdigit() or char in ".-+")
    try:
        return float(filtered)
    except ValueError:
        return 0.0
