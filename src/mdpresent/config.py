"""Theme loading, validation, fonts, and path resolution."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

import yaml
from reportlab.lib.colors import Color, toColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont, TTFError


PACKAGE_THEME = Path(__file__).with_name("default-theme.yml")


class ConfigError(ValueError):
    """Raised when a theme file contains invalid values."""


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Could not read theme {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"Theme {path} must contain a YAML mapping at its root")
    return value


@dataclass(frozen=True)
class FontNames:
    regular: str
    bold: str
    italic: str
    bold_italic: str
    heading_regular: str
    heading_bold: str
    monospace: str
    fallback_regular: str
    fallback_bold: str


@dataclass
class Theme:
    data: dict[str, Any]
    source_path: Path | None
    fonts: FontNames

    def color(self, name: str) -> Color:
        try:
            return toColor(self.data["colors"][name])
        except (KeyError, ValueError) as exc:
            raise ConfigError(f"Invalid or missing color: colors.{name}") from exc

    def color_hex(self, name: str) -> str:
        color = self.color(name)
        return f"#{round(color.red * 255):02X}{round(color.green * 255):02X}{round(color.blue * 255):02X}"

    def resolve_path(self, value: str | None, fallback_base: Path) -> Path | None:
        if not value:
            return None
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        base = self.source_path.parent if self.source_path else fallback_base
        return (base / path).resolve()


def load_theme(path: str | Path | None = None) -> Theme:
    defaults = _read_yaml(PACKAGE_THEME)
    source_path = Path(path).expanduser().resolve() if path else None
    values = _deep_merge(defaults, _read_yaml(source_path)) if source_path else defaults
    _validate(values)
    font_base = source_path.parent if source_path else PACKAGE_THEME.parent
    return Theme(values, source_path, _register_fonts(values["fonts"], font_base))


def write_default_theme(path: str | Path) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(PACKAGE_THEME.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


def _validate(values: dict[str, Any]) -> None:
    required_sections = {"document", "fonts", "header", "colors", "spacing", "mermaid"}
    missing = sorted(required_sections - values.keys())
    if missing:
        raise ConfigError(f"Theme is missing sections: {', '.join(missing)}")

    for name, value in values["colors"].items():
        try:
            toColor(value)
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"colors.{name} is not a valid color: {value!r}") from exc

    positive_values = (
        ("document.base_font_size", values["document"]["base_font_size"]),
        ("document.line_height", values["document"]["line_height"]),
        ("header.height_mm", values["header"]["height_mm"]),
        ("mermaid.tile_scale", values["mermaid"]["tile_scale"]),
    )
    for label, value in positive_values:
        if not isinstance(value, (int, float)) or value <= 0:
            raise ConfigError(f"{label} must be a positive number")


def _register_fonts(config: dict[str, Any], base_dir: Path) -> FontNames:
    candidates = {
        "regular": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ],
        "bold": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ],
        "italic": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Italic.ttf",
        ],
        "bold_italic": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-BoldItalic.ttf",
        ],
        "heading_regular": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ],
        "heading_bold": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ],
        "monospace": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
        ],
        "fallback_regular": [
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ],
        "fallback_bold": [
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ],
    }
    builtins = {
        "regular": "Helvetica",
        "bold": "Helvetica-Bold",
        "italic": "Helvetica-Oblique",
        "bold_italic": "Helvetica-BoldOblique",
        "heading_regular": "Helvetica",
        "heading_bold": "Helvetica-Bold",
        "monospace": "Courier",
        "fallback_regular": "Helvetica",
        "fallback_bold": "Helvetica-Bold",
    }
    resolved: dict[str, str] = {}
    prefix = "MDPresent"

    for role, fallback_paths in candidates.items():
        configured = config.get(role)
        if configured:
            configured_path = Path(str(configured)).expanduser()
            if not configured_path.is_absolute():
                configured_path = base_dir / configured_path
            paths = [str(configured_path.resolve())]
        else:
            paths = fallback_paths
        resolved[role] = builtins[role]
        for path in (candidate for candidate in paths if Path(candidate).is_file()):
            digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]
            font_name = f"{prefix}-{role.replace('_', '-').title()}-{digest}"
            try:
                if font_name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(font_name, path))
            except TTFError as exc:
                if configured:
                    raise ConfigError(f"Could not load fonts.{role} from {path}: {exc}") from exc
                continue
            resolved[role] = font_name
            break

    pdfmetrics.registerFontFamily(
        "MDPresent",
        normal=resolved["regular"],
        bold=resolved["bold"],
        italic=resolved["italic"],
        boldItalic=resolved["bold_italic"],
    )
    pdfmetrics.registerFontFamily(
        "MDPresentFallback",
        normal=resolved["fallback_regular"],
        bold=resolved["fallback_bold"],
        italic=resolved["fallback_regular"],
        boldItalic=resolved["fallback_bold"],
    )
    return FontNames(**resolved)
