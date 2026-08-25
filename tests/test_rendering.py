from pathlib import Path

from pypdf import PdfReader

from mdpresent.config import load_theme
from mdpresent.renderer import MarkdownPdfRenderer, render_markdown


ROOT = Path(__file__).resolve().parents[1]


def test_first_title_style_is_larger_than_other_h1(tmp_path: Path) -> None:
    source = ROOT / "examples" / "features.md"
    renderer = MarkdownPdfRenderer(source, tmp_path / "unused.pdf", load_theme(ROOT / "examples" / "theme.yml"))
    assert renderer.styles["first_title"].fontSize > renderer.styles["h1"].fontSize


def test_feature_document_renders_with_headers_and_text(tmp_path: Path) -> None:
    output = render_markdown(
        ROOT / "examples" / "features.md",
        tmp_path / "features.pdf",
        ROOT / "examples" / "theme.yml",
    )
    reader = PdfReader(output)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert len(reader.pages) >= 2
    assert "PAGE  01" in text
    assert "A practical field guide" in text
    assert "Document rendering path" in text
    assert "Notes" in text


def test_oversized_mermaid_is_vector_tiled_then_returns_to_portrait(tmp_path: Path) -> None:
    output = render_markdown(
        ROOT / "examples" / "edge-cases.md",
        tmp_path / "edge-cases.pdf",
        ROOT / "examples" / "theme.yml",
    )
    reader = PdfReader(output)
    sizes = [(float(page.mediabox.width), float(page.mediabox.height)) for page in reader.pages]
    assert any(width > height for width, height in sizes)
    assert sizes[-1][0] < sizes[-1][1]
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "part 1 of" in text
    assert "Content after landscape pages" in text

    diagram_pages = [page for page in reader.pages if "part " in (page.extract_text() or "")]
    assert diagram_pages
    for page in diagram_pages:
        resources = page.get("/Resources", {})
        xobjects = resources.get("/XObject", {}) if resources else {}
        for reference in xobjects.values():
            assert reference.get_object().get("/Subtype") != "/Image"

