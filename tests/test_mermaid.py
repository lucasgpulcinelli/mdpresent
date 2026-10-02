from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader
import pytest
from reportlab.graphics import renderPDF

from mdpresent.config import load_theme
from mdpresent.mermaid import MermaidRenderer


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("code", "width", "height", "size"),
    [
        ("flowchart LR\n    A[Parser] --> B[Renderer]", 960, 640, 960),
        ("sequenceDiagram\n    Parser->>Renderer: Render document", 640, 1280, 1280),
    ],
)
def test_cli_12_renders_fresh_svg_with_theme_dimensions_and_pdf_text(
    tmp_path: Path, code: str, width: int, height: int, size: int
) -> None:
    theme = load_theme()
    theme.data["mermaid"].update(
        cli=str(ROOT / "node_modules" / ".bin" / "mmdc"),
        render_width=width,
        render_height=height,
    )
    renderer = MermaidRenderer(theme, tmp_path, cache_dir=tmp_path / "cache")

    asset = renderer.render(code)

    svg = ElementTree.parse(asset.source).getroot()
    style = dict(
        declaration.strip().split(":", 1)
        for declaration in svg.attrib["style"].split(";")
        if declaration.strip()
    )
    assert style["max-width"].strip() == f"{size}px"
    assert style["max-height"].strip() == f"{size}px"
    assert not svg.findall(".//{http://www.w3.org/2000/svg}foreignObject")
    assert asset.width > 0 and asset.height > 0

    pdf = PdfReader(BytesIO(renderPDF.drawToString(asset.drawing)))
    text = pdf.pages[0].extract_text()
    assert "Parser" in text
    assert "Renderer" in text
