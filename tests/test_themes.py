from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics
import pytest
import yaml

from mdpresent.config import load_theme
from mdpresent.renderer import render_markdown


ROOT = Path(__file__).resolve().parents[1]
THEMES = ROOT / "themes"


def _family_name(font_name: str) -> str:
    family = pdfmetrics.getFont(font_name).face.familyName
    return family.decode("utf-8") if isinstance(family, bytes) else str(family)


def test_brand_themes_define_every_renderer_color() -> None:
    defaults = yaml.safe_load((ROOT / "src" / "mdpresent" / "default-theme.yml").read_text())
    required_colors = set(defaults["colors"])
    for filename in ("icmc.yml",):
        values = yaml.safe_load((THEMES / filename).read_text())
        assert set(values["colors"]) == required_colors


def test_icmc_theme_uses_manual_palette_logo_and_droid_sans() -> None:
    theme = load_theme(THEMES / "icmc.yml")
    palette = set(theme.data["colors"].values())
    assert {"#F6AA41", "#72849D", "#C6CEDA", "#727376", "#DCD6CD"} <= palette
    assert _family_name(theme.fonts.regular) == "Droid Sans"
    assert theme.resolve_path(theme.data["header"]["logo"], ROOT).is_file()


@pytest.mark.parametrize("theme_name", ["icmc"])
def test_brand_theme_renders_a_pdf(theme_name: str, tmp_path: Path) -> None:
    source = tmp_path / "sample.md"
    source.write_text("# Branded report\n\nBody text with **emphasis**.\n", encoding="utf-8")
    output = render_markdown(source, tmp_path / f"{theme_name}.pdf", THEMES / f"{theme_name}.yml")
    assert "Branded report" in (PdfReader(output).pages[0].extract_text() or "")

