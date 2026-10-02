from pathlib import Path

from PIL import Image as PillowImage
from pypdf import PdfReader
import pytest

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
    assert "Render contract" in text
    assert "One source, several presentation layers" in text
    assert "Figure 1 — Source, renderer, and PDF output" in text
    assert "Document rendering path" in text
    assert "Notes" in text


def test_skipped_heading_levels_render_with_nested_pdf_outline(tmp_path: Path) -> None:
    source = tmp_path / "skipped-headings.md"
    source.write_text(
        "## First section\n\n#### Detail\n\n## Second section\n\n# Main title\n\n### Subsection\n",
        encoding="utf-8",
    )

    reader = PdfReader(render_markdown(source, tmp_path / "skipped-headings.pdf"))

    assert [entry.title for entry in reader.outline if not isinstance(entry, list)] == [
        "First section",
        "Second section",
        "Main title",
    ]
    assert [entry.title for entry in reader.outline[1]] == ["Detail"]
    assert [entry.title for entry in reader.outline[4]] == ["Subsection"]


def test_oversized_mermaid_gets_one_large_vector_page_then_returns_to_portrait(
    tmp_path: Path,
) -> None:
    output = render_markdown(
        ROOT / "examples" / "edge-cases.md",
        tmp_path / "edge-cases.pdf",
        ROOT / "examples" / "theme.yml",
    )
    reader = PdfReader(output)
    sizes = [(float(page.mediabox.width), float(page.mediabox.height)) for page in reader.pages]
    configured_width, configured_height = sizes[0]
    diagram_pages = [
        page
        for page in reader.pages
        if "End-to-end platform migration with review gates" in (page.extract_text() or "")
    ]
    assert len(diagram_pages) == 1
    diagram_width = float(diagram_pages[0].mediabox.width)
    diagram_height = float(diagram_pages[0].mediabox.height)
    assert diagram_width > configured_width
    assert diagram_height >= configured_height
    assert sizes[-1][0] < sizes[-1][1]
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "part 1 of" not in text
    assert "Content after the oversized page" in text

    for page in diagram_pages:
        resources = page.get("/Resources", {})
        xobjects = resources.get("/XObject", {}) if resources else {}
        for reference in xobjects.values():
            assert reference.get_object().get("/Subtype") != "/Image"


def test_extended_markdown_and_labeled_raster_image_render(tmp_path: Path) -> None:
    image_path = tmp_path / "chart.png"
    PillowImage.new("RGB", (800, 320), "#8DBAC4").save(image_path)
    source = tmp_path / "extended.md"
    source.write_text(
        """# Extended report

Release gate
: The condition that must pass before **publishing**.

!!! note "Review note"
    Water is H~2~O and the sample contains 2^10^ records.

![Accessible chart description](chart.png "Fallback caption"){#fig-results width="50%" label="Figure 1: Labeled results"}

See [Figure 1](#fig-results).
""",
        encoding="utf-8",
    )

    output = render_markdown(source, tmp_path / "extended.pdf")
    reader = PdfReader(output)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Release gate" in text
    assert "condition that must pass" in text
    assert "Review note" in text
    assert "Figure 1: Labeled results" in text
    assert "Fallback caption" not in text
    assert any(
        reference.get_object().get("/Subtype") == "/Image"
        for page in reader.pages
        for reference in (page.get("/Resources", {}).get("/XObject", {}) or {}).values()
    )


@pytest.mark.parametrize("content_kind", ["paragraphs", "single_paragraph", "list", "nested_quote"])
def test_long_blockquote_splits_across_pages_without_losing_content(
    tmp_path: Path, content_kind: str,
) -> None:
    markers = [f"QUOTE{index:03d}" for index in range(80)]
    sentences = [
        f"{marker} Quoted material must remain readable and complete across page boundaries."
        for marker in markers
    ]
    if content_kind == "single_paragraph":
        quoted = "> " + " ".join(sentences)
    elif content_kind == "list":
        quoted = "> Introduction to the quoted list.\n>\n" + "\n".join(
            f"> {index}. {sentence}" for index, sentence in enumerate(sentences, start=1)
        )
    else:
        prefix = "> > " if content_kind == "nested_quote" else "> "
        separator = "\n> >\n" if content_kind == "nested_quote" else "\n>\n"
        quoted = separator.join(prefix + sentence for sentence in sentences)
    source = tmp_path / "long-quote.md"
    source.write_text(f"Before the quote.\n\n{quoted}\n\nAfter the quote.\n", encoding="utf-8")
    theme = tmp_path / "theme.yml"
    theme.write_text(
        "document:\n  base_font_size: 10.5\n  line_height: 1.4\n",
        encoding="utf-8",
    )

    reader = PdfReader(render_markdown(source, tmp_path / "long-quote.pdf", theme))

    assert len(reader.pages) >= 2
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    expected = ["Before the quote.", *markers, "After the quote."]
    assert all(text.count(marker) == 1 for marker in expected)
    positions = [text.index(marker) for marker in expected]
    assert positions == sorted(positions)
