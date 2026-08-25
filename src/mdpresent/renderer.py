"""ReportLab document builder for the parsed Markdown model."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import math
from pathlib import Path
import re
import tempfile
import textwrap
from typing import Any, Iterable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from markdown_it.token import Token
from pygments import lex
from pygments.lexers import get_lexer_by_name, TextLexer
from pygments.token import Comment, Keyword, Literal, Number, String
from reportlab.graphics import renderPDF
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, LEGAL, LETTER, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    Image,
    ListFlowable,
    ListItem,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    XPreformatted,
)
from reportlab.lib.utils import ImageReader

from .config import ConfigError, Theme, load_theme
from .markdown_parser import (
    Block,
    CodeBlock,
    FootnotesBlock,
    Heading,
    HtmlBlock,
    ListBlock,
    MermaidBlock,
    PageBreakBlock,
    Paragraph as MarkdownParagraph,
    ParsedDocument,
    QuoteBlock,
    Rule,
    TableBlock,
    inline_plain_text,
    parse_markdown,
)
from .mermaid import MermaidRenderer, VectorAsset, load_svg_asset


PAGE_SIZES = {"A4": A4, "LETTER": LETTER, "LEGAL": LEGAL}


class RenderError(RuntimeError):
    """Raised when a Markdown document cannot be rendered."""


class ThemedDocTemplate(BaseDocTemplate):
    def afterFlowable(self, flowable: Flowable) -> None:
        level = getattr(flowable, "_md_heading_level", None)
        title = getattr(flowable, "_md_heading_text", None)
        if level is None or not title:
            return
        key = f"heading-{self.page}-{self.seq.nextf('heading')}"
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(title, key, max(0, int(level) - 1), closed=False)


class VectorDrawingFlowable(Flowable):
    def __init__(
        self,
        asset: VectorAsset,
        width: float,
        height: float,
        caption: str | None,
        caption_style: ParagraphStyle,
    ) -> None:
        super().__init__()
        self.asset = asset
        self.draw_width = width
        self.draw_height = height
        self.caption = Paragraph(escape(caption), caption_style) if caption else None
        self.caption_gap = 2.2 * mm if caption else 0
        self.width = width
        self.height = height + (caption_style.leading + self.caption_gap if caption else 0)

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        self._availWidth = availWidth
        return min(self.width, availWidth), self.height

    def draw(self) -> None:
        x = max(0, (self._availWidth - self.draw_width) / 2)
        caption_height = 0.0
        if self.caption:
            _, caption_height = self.caption.wrap(self._availWidth, 100 * mm)
            self.caption.drawOn(self.canv, 0, 0)
            caption_height += self.caption_gap
        self.canv.saveState()
        self.canv.translate(x, caption_height)
        self.canv.scale(
            self.draw_width / float(self.asset.drawing.width),
            self.draw_height / float(self.asset.drawing.height),
        )
        renderPDF.draw(self.asset.drawing, self.canv, 0, 0)
        self.canv.restoreState()


class DiagramPageFlowable(Flowable):
    """A complete diagram page, optionally showing one crop of a larger SVG."""

    def __init__(
        self,
        asset: VectorAsset,
        page_width: float,
        page_height: float,
        label: str,
        label_style: ParagraphStyle,
        surface_color: colors.Color,
        border_color: colors.Color,
        scale: float,
        crop_x: float = 0,
        crop_y_top: float = 0,
        part: int = 1,
        part_count: int = 1,
    ) -> None:
        super().__init__()
        self.asset = asset
        self.width = page_width
        self.height = page_height - 0.5
        self.label = label
        self.label_style = label_style
        self.surface_color = surface_color
        self.border_color = border_color
        self.scale = scale
        self.crop_x = crop_x
        self.crop_y_top = crop_y_top
        self.part = part
        self.part_count = part_count
        self.label_height = label_style.leading + 3 * mm

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        self._frame_width = availWidth
        self._frame_height = availHeight
        return availWidth, min(self.height, availHeight - 0.5)

    def draw(self) -> None:
        frame_width = self._frame_width
        frame_height = min(self.height, self._frame_height - 0.5)
        viewport_height = frame_height - self.label_height
        label = self.label
        if self.part_count > 1:
            label = f"{label}  |  part {self.part} of {self.part_count}"
        paragraph = Paragraph(escape(label), self.label_style)
        _, label_h = paragraph.wrap(frame_width, self.label_height)

        self.canv.saveState()
        self.canv.setFillColor(self.surface_color)
        self.canv.setStrokeColor(self.border_color)
        self.canv.setLineWidth(0.45)
        self.canv.roundRect(0, 0, frame_width, frame_height, 2.5 * mm, fill=1, stroke=1)
        paragraph.drawOn(self.canv, 3 * mm, frame_height - label_h - 2 * mm)

        inset = 3 * mm
        view_x = inset
        view_y = inset
        view_width = frame_width - 2 * inset
        view_height = viewport_height - inset
        clip = self.canv.beginPath()
        clip.rect(view_x, view_y, view_width, view_height)
        self.canv.clipPath(clip, stroke=0, fill=0)

        scaled_width = self.asset.width * self.scale
        scaled_height = self.asset.height * self.scale
        if self.part_count == 1:
            x_offset = max(0, (view_width - scaled_width) / 2)
            y_offset = max(0, (view_height - scaled_height) / 2)
        else:
            x_offset = -self.crop_x
            if scaled_height <= view_height:
                y_offset = (view_height - scaled_height) / 2
            else:
                lower_crop = scaled_height - self.crop_y_top - view_height
                y_offset = -max(0, lower_crop)

        self.canv.translate(view_x + x_offset, view_y + y_offset)
        self.canv.scale(
            scaled_width / float(self.asset.drawing.width),
            scaled_height / float(self.asset.drawing.height),
        )
        renderPDF.draw(self.asset.drawing, self.canv, 0, 0)
        self.canv.restoreState()


@dataclass
class DiagramPlan:
    orientation: str
    pages: list[DiagramPageFlowable]
    inline: VectorDrawingFlowable | None = None


class HeaderPainter:
    def __init__(self, theme: Theme, title: str, document_dir: Path) -> None:
        self.theme = theme
        self.title = title
        self.document_dir = document_dir
        self.header = theme.data["header"]
        self.logo = self._load_logo()

    def __call__(self, canvas: Any, doc: BaseDocTemplate) -> None:
        page_width, page_height = canvas._pagesize
        header_height = float(self.header["height_mm"]) * mm
        header_y = page_height - header_height
        panel_start = page_width * 0.69
        accent_height = 2.1 * mm

        canvas.saveState()
        canvas.setFillColor(self.theme.color("background"))
        canvas.rect(0, 0, page_width, page_height, fill=1, stroke=0)
        self._draw_background_pattern(canvas, page_width, header_y)
        canvas.setFillColor(self.theme.color("header_background"))
        canvas.rect(0, header_y, page_width, header_height, fill=1, stroke=0)

        canvas.setFillColor(self.theme.color("header_panel"))
        panel = canvas.beginPath()
        panel.moveTo(panel_start + 8 * mm, page_height)
        panel.lineTo(panel_start, header_y)
        panel.lineTo(page_width, header_y)
        panel.lineTo(page_width, page_height)
        panel.close()
        canvas.drawPath(panel, fill=1, stroke=0)

        stripe_primary = str(self.header.get("stripe_primary", "accent"))
        stripe_secondary = str(self.header.get("stripe_secondary", "accent_secondary"))
        canvas.setFillColor(self.theme.color(stripe_primary))
        canvas.rect(0, header_y, page_width * 0.66, accent_height, fill=1, stroke=0)
        canvas.setFillColor(self.theme.color(stripe_secondary))
        canvas.rect(page_width * 0.66, header_y, page_width * 0.34, accent_height, fill=1, stroke=0)

        left = float(self.theme.data["document"]["margin_left_mm"]) * mm
        logo_right = left
        if self.logo:
            logo_right = self._draw_logo(canvas, left, header_y, header_height)

        title_font = self.theme.fonts.heading_bold
        title_size = 9.2
        title_x = logo_right + (4 * mm if self.logo else 0)
        page_box_width = 31 * mm
        max_title_width = max(20 * mm, panel_start - title_x - 6 * mm)
        visible_title = _ellipsize(self.title, title_font, title_size, max_title_width)
        canvas.setFont(title_font, title_size)
        canvas.setFillColor(self.theme.color("header_text"))
        canvas.drawString(title_x, header_y + header_height / 2 - 1.3 * mm, visible_title)

        page_label = str(self.header.get("page_label", "PAGE"))
        page_text = f"{page_label}  {canvas.getPageNumber():02d}"
        canvas.setFillColor(self.theme.color("header_muted"))
        canvas.setFont(self.theme.fonts.heading_bold, 8.6)
        canvas.drawRightString(page_width - left, header_y + header_height / 2 - 1.3 * mm, page_text)
        canvas.restoreState()

    def _draw_background_pattern(self, canvas: Any, page_width: float, top: float) -> None:
        document = self.theme.data["document"]
        if str(document.get("background_pattern", "none")).lower() != "dots":
            return
        spacing = float(document.get("background_pattern_spacing_mm", 14)) * mm
        radius = float(document.get("background_pattern_radius_pt", 0.45))
        canvas.setFillColor(self.theme.color("background_pattern"))
        y = spacing / 2
        row = 0
        while y < top:
            x = spacing / 2 + (spacing / 2 if row % 2 else 0)
            while x < page_width:
                canvas.circle(x, y, radius, fill=1, stroke=0)
                x += spacing
            y += spacing
            row += 1

    def _load_logo(self) -> tuple[str, Any] | None:
        logo_value = self.header.get("logo")
        if str(logo_value).lower() == "builtin":
            return "builtin", None
        path = self.theme.resolve_path(logo_value, self.document_dir)
        if path is None:
            return None
        if not path.is_file():
            raise RenderError(f"Logo file does not exist: {path}")
        if path.suffix.lower() == ".svg":
            svg_text = path.read_text(encoding="utf-8")
            replacements = {
                "{{ logo_primary }}": self.theme.color_hex("logo_primary"),
                "{{ logo_secondary }}": self.theme.color_hex("logo_secondary"),
                "{{logo_primary}}": self.theme.color_hex("logo_primary"),
                "{{logo_secondary}}": self.theme.color_hex("logo_secondary"),
            }
            for marker, value in replacements.items():
                svg_text = svg_text.replace(marker, value)
            with tempfile.NamedTemporaryFile(suffix=".svg", mode="w", encoding="utf-8") as stream:
                stream.write(svg_text)
                stream.flush()
                return "svg", load_svg_asset(Path(stream.name)).drawing
        return "raster", ImageReader(str(path))

    def _draw_logo(self, canvas: Any, x: float, header_y: float, header_height: float) -> float:
        assert self.logo is not None
        kind, asset = self.logo
        max_width = float(self.header["logo_width_mm"]) * mm
        max_height = header_height - 6 * mm
        if kind == "builtin":
            width = max_width
            height = min(max_height, 10 * mm)
            radius = 1.8 * mm
            y = header_y + (header_height - height) / 2
            canvas.saveState()
            canvas.setFillColor(self.theme.color("logo_secondary"))
            canvas.roundRect(x, y, width, height, radius, fill=1, stroke=0)
            primary_width = width * 0.34
            canvas.setFillColor(self.theme.color("logo_primary"))
            canvas.roundRect(x, y, primary_width + radius, height, radius, fill=1, stroke=0)
            canvas.rect(x + primary_width, y, radius, height, fill=1, stroke=0)
            canvas.setFillColor(self.theme.color("header_text"))
            canvas.setFont(self.theme.fonts.heading_bold, 8.6)
            canvas.drawCentredString(x + width * 0.63, y + height / 2 - 1.1 * mm, "MD")
            canvas.restoreState()
        elif kind == "svg":
            scale = min(max_width / asset.width, max_height / asset.height)
            width = asset.width * scale
            height = asset.height * scale
            canvas.saveState()
            canvas.translate(x, header_y + (header_height - height) / 2)
            canvas.scale(scale, scale)
            renderPDF.draw(asset, canvas, 0, 0)
            canvas.restoreState()
        else:
            source_width, source_height = asset.getSize()
            scale = min(max_width / source_width, max_height / source_height)
            width = source_width * scale
            height = source_height * scale
            canvas.drawImage(
                asset,
                x,
                header_y + (header_height - height) / 2,
                width=width,
                height=height,
                preserveAspectRatio=True,
                mask="auto",
            )
        return x + width


class MarkdownPdfRenderer:
    def __init__(self, source_path: Path, output_path: Path, theme: Theme) -> None:
        self.source_path = source_path
        self.output_path = output_path
        self.document_dir = source_path.parent
        self.theme = theme
        self.parsed: ParsedDocument | None = None
        self.diagram_renderer: MermaidRenderer | None = None
        self.styles = self._make_styles()
        self.diagram_count = 0
        self.first_h1_seen = False

        page_name = str(theme.data["document"]["page_size"]).upper()
        if page_name not in PAGE_SIZES:
            raise ConfigError(f"Unsupported page size: {page_name}. Use A4, LETTER, or LEGAL")
        self.portrait_size = PAGE_SIZES[page_name]
        self.landscape_size = landscape(self.portrait_size)
        self.left_margin = float(theme.data["document"]["margin_left_mm"]) * mm
        self.right_margin = float(theme.data["document"]["margin_right_mm"]) * mm
        self.bottom_margin = float(theme.data["document"]["margin_bottom_mm"]) * mm
        self.top_margin = (
            float(theme.data["header"]["height_mm"]) + float(theme.data["header"]["gap_after_mm"])
        ) * mm

    def render(self) -> None:
        source = self.source_path.read_text(encoding="utf-8")
        self.parsed = parse_markdown(source)
        configured_title = self.theme.data["header"].get("title")
        title = str(configured_title or self.parsed.title or self.source_path.stem.replace("_", " ").title())
        painter = HeaderPainter(self.theme, title, self.document_dir)

        portrait_frame, portrait_body = self._frame_for(self.portrait_size, "portrait-body")
        landscape_frame, landscape_body = self._frame_for(self.landscape_size, "landscape-body")
        templates = {
            "portrait": PageTemplate(
                id="portrait",
                pagesize=self.portrait_size,
                frames=[portrait_frame],
                onPage=painter,
            ),
            "landscape": PageTemplate(
                id="landscape",
                pagesize=self.landscape_size,
                frames=[landscape_frame],
                onPage=painter,
            ),
        }

        story, first_orientation = self._build_story(portrait_body, landscape_body)
        if not story:
            story = [Paragraph("", self.styles["body"])]
        ordered_templates = [templates[first_orientation], templates["landscape" if first_orientation == "portrait" else "portrait"]]
        doc = ThemedDocTemplate(
            str(self.output_path),
            pagesize=self.portrait_size if first_orientation == "portrait" else self.landscape_size,
            leftMargin=self.left_margin,
            rightMargin=self.right_margin,
            topMargin=self.top_margin,
            bottomMargin=self.bottom_margin,
            title=title,
            author=str(self.parsed.metadata.get("author", "")),
            subject=str(self.parsed.metadata.get("subject", "")),
            creator="mDpresent",
            pageTemplates=ordered_templates,
        )
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.build(story)

    def _frame_for(self, page_size: tuple[float, float], frame_id: str) -> tuple[Frame, tuple[float, float]]:
        width = page_size[0] - self.left_margin - self.right_margin
        height = page_size[1] - self.top_margin - self.bottom_margin
        frame = Frame(
            self.left_margin,
            self.bottom_margin,
            width,
            height,
            id=frame_id,
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
            showBoundary=0,
        )
        return frame, (width, height)

    def _build_story(
        self,
        portrait_body: tuple[float, float],
        landscape_body: tuple[float, float],
    ) -> tuple[list[Flowable], str]:
        assert self.parsed is not None
        story: list[Flowable] = []
        current_orientation = "portrait"
        first_orientation = "portrait"
        started = False
        last_was_dedicated = False

        for block in self.parsed.blocks:
            if isinstance(block, MermaidBlock):
                plan = self._plan_diagram(block, portrait_body, landscape_body)
                if plan.inline:
                    if last_was_dedicated or current_orientation != "portrait":
                        if current_orientation != "portrait":
                            story.append(NextPageTemplate("portrait"))
                        story.append(PageBreak())
                        current_orientation = "portrait"
                    story.append(plan.inline)
                    started = True
                    last_was_dedicated = False
                    continue

                if started:
                    if current_orientation != plan.orientation:
                        story.append(NextPageTemplate(plan.orientation))
                    story.append(PageBreak())
                else:
                    first_orientation = plan.orientation
                current_orientation = plan.orientation
                for page_index, page in enumerate(plan.pages):
                    if page_index:
                        story.append(PageBreak())
                    story.append(page)
                started = True
                last_was_dedicated = True
                continue

            if last_was_dedicated:
                if current_orientation != "portrait":
                    story.append(NextPageTemplate("portrait"))
                story.append(PageBreak())
                current_orientation = "portrait"
                last_was_dedicated = False
            flowables = self._block_flowables(block, portrait_body[0])
            if flowables:
                story.extend(flowables)
                started = True

        return story, first_orientation

    def _block_flowables(self, block: Block, available_width: float) -> list[Flowable]:
        if isinstance(block, Heading):
            return [self._heading(block)]
        if isinstance(block, MarkdownParagraph):
            image = _standalone_image(block.children)
            if image:
                return self._image_flowables(image, available_width)
            return [Paragraph(self._inline_markup(block.children), self.styles["body"])]
        if isinstance(block, CodeBlock):
            return [self._code_flowable(block)]
        if isinstance(block, ListBlock):
            return [self._list_flowable(block, available_width)]
        if isinstance(block, QuoteBlock):
            nested: list[Flowable] = []
            for child in block.blocks:
                if isinstance(child, MarkdownParagraph):
                    nested.append(Paragraph(self._inline_markup(child.children), self.styles["blockquote"]))
                else:
                    nested.extend(self._block_flowables(child, available_width - 10 * mm))
            table = Table([[nested]], colWidths=[available_width], hAlign="LEFT")
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), self.theme.color("blockquote_background")),
                        ("BOX", (0, 0), (-1, -1), 0, self.theme.color("blockquote_background")),
                        ("LINEBEFORE", (0, 0), (0, -1), 4, self.theme.color("blockquote_border")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
                        ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 1 * mm),
                    ]
                )
            )
            return [table, Spacer(1, 2.5 * mm)]
        if isinstance(block, TableBlock):
            return [self._table_flowable(block, available_width), Spacer(1, 3 * mm)]
        if isinstance(block, Rule):
            rule = Table([[""]], colWidths=[available_width], rowHeights=[1.2 * mm])
            rule.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), self.theme.color("rule")),
                        ("LINEBELOW", (0, 0), (-1, -1), 1.2 * mm, self.theme.color("accent")),
                    ]
                )
            )
            return [Spacer(1, 2 * mm), rule, Spacer(1, 3 * mm)]
        if isinstance(block, PageBreakBlock):
            return [PageBreak()]
        if isinstance(block, HtmlBlock):
            return [Paragraph(escape(block.text).replace("\n", "<br/>"), self.styles["body"])]
        if isinstance(block, FootnotesBlock):
            return self._footnotes(block, available_width)
        if isinstance(block, MermaidBlock):
            asset, caption = self._render_diagram(block)
            scale = min(available_width / asset.width, 1.0)
            return [
                VectorDrawingFlowable(
                    asset,
                    asset.width * scale,
                    asset.height * scale,
                    caption,
                    self.styles["caption"],
                )
            ]
        return []

    def _heading(self, block: Heading) -> Paragraph | Table:
        text = self._inline_markup(block.children)
        plain = inline_plain_text(block.children)
        if block.level == 1 and not self.first_h1_seen:
            self.first_h1_seen = True
            paragraph = Paragraph(text, self.styles["first_title"])
            setattr(paragraph, "_md_heading_level", block.level)
            setattr(paragraph, "_md_heading_text", plain)
            title_card = Table([[paragraph]], colWidths=[self._portrait_body_width()])
            title_card.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), self.theme.color("surface_alt")),
                        ("LINEBEFORE", (0, 0), (0, -1), 5, self.theme.color("accent")),
                        ("LINEAFTER", (0, 0), (0, -1), 1.5, self.theme.color("accent_secondary")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6 * mm),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6 * mm),
                        ("TOPPADDING", (0, 0), (-1, -1), 5 * mm),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5 * mm),
                    ]
                )
            )
            title_card.spaceAfter = 4 * mm
            setattr(title_card, "_md_heading_level", block.level)
            setattr(title_card, "_md_heading_text", plain)
            return title_card

        style = self.styles[f"h{min(block.level, 6)}"]
        paragraph = Paragraph(text, style)
        setattr(paragraph, "_md_heading_level", block.level)
        setattr(paragraph, "_md_heading_text", plain)
        return paragraph

    def _list_flowable(self, block: ListBlock, available_width: float) -> ListFlowable:
        items: list[ListItem] = []
        indent = float(self.theme.data["spacing"]["list_indent_mm"]) * mm
        for item_blocks in block.items:
            contents: list[Flowable] = []
            for child in item_blocks:
                contents.extend(self._block_flowables(child, available_width - indent))
            if not contents:
                contents.append(Paragraph("", self.styles["list_body"]))
            items.append(ListItem(contents, leftIndent=indent, value=None))
        return ListFlowable(
            items,
            bulletType="1" if block.ordered else "bullet",
            start=block.start if block.ordered else None,
            bulletFontName=self.theme.fonts.heading_bold,
            bulletFontSize=8.5,
            bulletColor=self.theme.color("accent"),
            leftIndent=indent,
            bulletOffsetY=1.5,
            spaceAfter=float(self.theme.data["spacing"]["list_gap_mm"]) * mm,
        )

    def _table_flowable(self, block: TableBlock, available_width: float) -> Table:
        column_count = max(1, len(block.header), *(len(row) for row in block.rows))
        font_size = 8.3 if column_count >= 6 else 9.0
        header_style = ParagraphStyle(
            "table-header",
            parent=self.styles["body"],
            fontName=self.theme.fonts.heading_bold,
            fontSize=font_size,
            leading=font_size * 1.25,
            textColor=self.theme.color("table_header_text"),
            spaceAfter=0,
        )
        cell_style = ParagraphStyle(
            "table-cell",
            parent=self.styles["body"],
            fontSize=font_size,
            leading=font_size * 1.32,
            spaceAfter=0,
        )

        header = list(block.header) + [[] for _ in range(column_count - len(block.header))]
        rows = [list(row) + [[] for _ in range(column_count - len(row))] for row in block.rows]
        data = [[Paragraph(self._inline_markup(cell), header_style) for cell in header]]
        data.extend([[Paragraph(self._inline_markup(cell), cell_style) for cell in row] for row in rows])
        widths = self._table_widths([header, *rows], available_width)
        table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1)
        commands: list[tuple[Any, ...]] = [
            ("BACKGROUND", (0, 0), (-1, 0), self.theme.color("table_header")),
            ("BACKGROUND", (0, 0), (0, 0), self.theme.color("table_header_accent")),
            ("TEXTCOLOR", (0, 0), (-1, 0), self.theme.color("table_header_text")),
            ("GRID", (0, 0), (-1, -1), 0.45, self.theme.color("table_grid")),
            ("LINEBELOW", (0, 0), (-1, 0), 2.2, self.theme.color("accent_secondary")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2.6 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2.6 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 2.1 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.1 * mm),
        ]
        for row_index in range(1, len(data)):
            color_name = "table_row_odd" if row_index % 2 else "table_row_even"
            commands.append(("BACKGROUND", (0, row_index), (-1, row_index), self.theme.color(color_name)))
        for column, alignment in enumerate(block.alignments):
            commands.append(("ALIGN", (column, 1), (column, -1), alignment or "LEFT"))
            commands.append(("ALIGN", (column, 0), (column, 0), alignment or "LEFT"))
        table.setStyle(TableStyle(commands))
        return table

    def _table_widths(self, rows: list[list[list[Token]]], available_width: float) -> list[float]:
        column_count = max(len(row) for row in rows)
        weights: list[float] = []
        for column in range(column_count):
            length = max(
                (len(inline_plain_text(row[column])) if column < len(row) else 0 for row in rows),
                default=1,
            )
            weights.append(max(1.0, math.sqrt(min(length, 120))))
        total = sum(weights)
        minimum = min(22 * mm, available_width / column_count)
        widths = [max(minimum, available_width * weight / total) for weight in weights]
        if sum(widths) > available_width:
            scale = available_width / sum(widths)
            widths = [width * scale for width in widths]
        return widths

    def _code_flowable(self, block: CodeBlock) -> XPreformatted:
        wrapped_lines: list[str] = []
        for line in block.code.splitlines() or [""]:
            indentation = re.match(r"\s*", line).group(0)
            wrapper = textwrap.TextWrapper(
                width=94,
                subsequent_indent=indentation + "  ",
                replace_whitespace=False,
                drop_whitespace=False,
                break_long_words=True,
                break_on_hyphens=False,
            )
            wrapped_lines.extend(wrapper.wrap(line) or [""])
        markup = self._highlight_code("\n".join(wrapped_lines), block.language)
        return XPreformatted(markup, self.styles["code"])

    def _highlight_code(self, code: str, language: str) -> str:
        try:
            lexer = get_lexer_by_name(language) if language else TextLexer()
        except Exception:
            lexer = TextLexer()
        parts: list[str] = []
        for token_type, value in lex(code, lexer):
            color_name = "code_text"
            if token_type in Comment:
                color_name = "code_comment"
            elif token_type in Keyword:
                color_name = "code_keyword"
            elif token_type in String:
                color_name = "code_string"
            elif token_type in Number or token_type in Literal:
                color_name = "code_number"
            safe = escape(value)
            if color_name == "code_text":
                parts.append(safe)
            else:
                parts.append(f'<font color="{self.theme.color_hex(color_name)}">{safe}</font>')
        return "".join(parts).rstrip("\n")

    def _footnotes(self, block: FootnotesBlock, available_width: float) -> list[Flowable]:
        flowables: list[Flowable] = [Paragraph("Notes", self.styles["h2"])]
        for number, blocks in block.items:
            nested: list[Flowable] = []
            for child in blocks:
                nested.extend(self._block_flowables(child, available_width - 8 * mm))
            flowables.append(
                ListFlowable(
                    [ListItem(nested, value=number)],
                    bulletType="1",
                    start=number,
                    leftIndent=7 * mm,
                    bulletColor=self.theme.color("accent"),
                )
            )
        return flowables

    def _inline_markup(self, tokens: Iterable[Token]) -> str:
        parts: list[str] = []
        bold_depth = 0
        for token in tokens:
            kind = token.type
            if kind == "text":
                parts.append(self._text_markup(token.content, bold=bold_depth > 0))
            elif kind == "code_inline":
                parts.append(
                    f'<font face="{self.theme.fonts.monospace}" '
                    f'color="{self.theme.color_hex("code_text")}">'
                    f'{self._text_markup(token.content)}</font>'
                )
            elif kind == "strong_open":
                parts.append("<b>")
                bold_depth += 1
            elif kind == "strong_close":
                bold_depth = max(0, bold_depth - 1)
                parts.append("</b>")
            elif kind == "em_open":
                parts.append("<i>")
            elif kind == "em_close":
                parts.append("</i>")
            elif kind == "s_open":
                parts.append("<strike>")
            elif kind == "s_close":
                parts.append("</strike>")
            elif kind == "link_open":
                href = escape(token.attrGet("href") or "", quote=True)
                parts.append(f'<link href="{href}" color="{self.theme.color_hex("link")}">')
            elif kind == "link_close":
                parts.append("</link>")
            elif kind == "softbreak":
                parts.append(" ")
            elif kind == "hardbreak":
                parts.append("<br/>")
            elif kind == "footnote_ref":
                number = int(token.meta.get("id", 0)) + 1
                parts.append(f"<super>[{number}]</super>")
            elif kind == "image":
                parts.append(escape(token.content or token.attrGet("src") or "image"))
            elif kind == "html_inline":
                raw = token.content
                if raw.lower().startswith("<input"):
                    checked = "checked" in raw.lower()
                    symbol = "☑" if checked else "☐"
                    marker = symbol if self._fonts_support(symbol) else ("[x]" if checked else "[ ]")
                    parts.append(self._text_markup(marker + " "))
                elif re.fullmatch(r"<br\s*/?>", raw, re.IGNORECASE):
                    parts.append("<br/>")
                elif re.fullmatch(r"</?(?:sub|sup)>", raw, re.IGNORECASE):
                    parts.append(raw.lower().replace("sup", "super").replace("sub", "sub"))
                else:
                    extractor = re.sub(r"<[^>]+>", "", raw)
                    parts.append(escape(extractor))
            elif token.children:
                parts.append(self._inline_markup(token.children))
        return "".join(parts)

    def _text_markup(self, value: str, bold: bool = False) -> str:
        primary_name = self.theme.fonts.bold if bold else self.theme.fonts.regular
        fallback_name = self.theme.fonts.fallback_bold if bold else self.theme.fonts.fallback_regular
        if primary_name == fallback_name:
            return escape(value)
        primary = pdfmetrics.getFont(primary_name)
        widths = getattr(primary.face, "charWidths", {})
        parts: list[str] = []
        current: list[str] = []
        using_fallback = False

        def flush() -> None:
            if not current:
                return
            text = escape("".join(current))
            parts.append(f'<font face="{fallback_name}">{text}</font>' if using_fallback else text)
            current.clear()

        for character in value:
            needs_fallback = ord(character) not in widths and ord(character) > 127
            if current and needs_fallback != using_fallback:
                flush()
            using_fallback = needs_fallback
            current.append(character)
        flush()
        return "".join(parts)

    def _fonts_support(self, character: str) -> bool:
        for font_name in (self.theme.fonts.regular, self.theme.fonts.fallback_regular):
            font = pdfmetrics.getFont(font_name)
            if ord(character) in getattr(font.face, "charWidths", {}):
                return True
        return False

    def _image_flowables(self, token: Token, available_width: float) -> list[Flowable]:
        source = token.attrGet("src") or ""
        alt = token.content.strip()
        path = self._resolve_image(source)
        max_height = self._portrait_body_height() * 0.62
        if path.suffix.lower() == ".svg":
            asset = load_svg_asset(path)
            scale = min(available_width / asset.width, max_height / asset.height, 1.25)
            image: Flowable = VectorDrawingFlowable(
                asset,
                asset.width * scale,
                asset.height * scale,
                None,
                self.styles["caption"],
            )
        else:
            image = Image(str(path))
            scale = min(available_width / image.imageWidth, max_height / image.imageHeight, 1.0)
            image.drawWidth = image.imageWidth * scale
            image.drawHeight = image.imageHeight * scale
            image.hAlign = "CENTER"
        flowables = [image]
        if alt:
            flowables.append(Paragraph(escape(alt), self.styles["caption"]))
        flowables.append(Spacer(1, 2.5 * mm))
        return flowables

    def _resolve_image(self, source: str) -> Path:
        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            suffix = Path(parsed.path).suffix or ".img"
            cache_dir = Path.cwd() / ".cache" / "mdpresent" / "images"
            cache_dir.mkdir(parents=True, exist_ok=True)
            key = re.sub(r"[^a-zA-Z0-9]", "", source)[-48:] or "remote"
            destination = cache_dir / f"{key}{suffix}"
            if not destination.is_file():
                request = Request(source, headers={"User-Agent": "mDpresent/0.1"})
                with urlopen(request, timeout=20) as response:
                    destination.write_bytes(response.read())
            return destination
        path = Path(source).expanduser()
        if not path.is_absolute():
            path = self.document_dir / path
        path = path.resolve()
        if not path.is_file():
            raise RenderError(f"Markdown image does not exist: {path}")
        return path

    def _render_diagram(self, block: MermaidBlock) -> tuple[VectorAsset, str]:
        if self.diagram_renderer is None:
            self.diagram_renderer = MermaidRenderer(self.theme, self.document_dir)
        self.diagram_count += 1
        title_match = re.search(r"^\s*accTitle\s*:\s*(.+)$", block.code, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else f"Diagram {self.diagram_count}"
        return self.diagram_renderer.render(block.code), title

    def _plan_diagram(
        self,
        block: MermaidBlock,
        portrait_body: tuple[float, float],
        landscape_body: tuple[float, float],
    ) -> DiagramPlan:
        asset, label = self._render_diagram(block)
        caption_height = self.styles["caption"].leading + 5 * mm
        portrait_fit = min(
            portrait_body[0] / asset.width,
            (portrait_body[1] - caption_height) / asset.height,
            1.25,
        )
        settings = self.theme.data["mermaid"]
        landscape_threshold = float(settings["landscape_when_scale_below"])

        if portrait_fit >= landscape_threshold:
            inline_scale = min(
                portrait_body[0] / asset.width,
                (portrait_body[1] * 0.70) / asset.height,
                1.15,
            )
            return DiagramPlan(
                orientation="portrait",
                pages=[],
                inline=VectorDrawingFlowable(
                    asset,
                    asset.width * inline_scale,
                    asset.height * inline_scale,
                    label,
                    self.styles["caption"],
                ),
            )

        portrait_page_fit = min(
            (portrait_body[0] - 6 * mm) / asset.width,
            (portrait_body[1] - caption_height - 3 * mm) / asset.height,
            1.25,
        )
        landscape_fit = min(
            (landscape_body[0] - 6 * mm) / asset.width,
            (landscape_body[1] - caption_height - 3 * mm) / asset.height,
            1.25,
        )
        orientation = "landscape" if landscape_fit > portrait_page_fit * 1.06 else "portrait"
        body = landscape_body if orientation == "landscape" else portrait_body
        fit = landscape_fit if orientation == "landscape" else portrait_page_fit
        split_threshold = float(settings["split_when_scale_below"])

        if fit >= split_threshold:
            page = DiagramPageFlowable(
                asset,
                body[0],
                body[1],
                label,
                self.styles["diagram_label"],
                self.theme.color("surface"),
                self.theme.color("diagram_border"),
                fit,
            )
            return DiagramPlan(orientation=orientation, pages=[page])

        tile_scale = max(float(settings["tile_scale"]), split_threshold)
        inset = 6 * mm
        view_width = body[0] - inset
        view_height = body[1] - self.styles["diagram_label"].leading - 6 * mm
        overlap = float(settings["tile_overlap_mm"]) * mm
        scaled_width = asset.width * tile_scale
        scaled_height = asset.height * tile_scale
        x_starts = _tile_starts(scaled_width, view_width, overlap)
        y_starts = _tile_starts(scaled_height, view_height, overlap)
        part_count = len(x_starts) * len(y_starts)
        pages: list[DiagramPageFlowable] = []
        part = 0
        for y_start in y_starts:
            for x_start in x_starts:
                part += 1
                pages.append(
                    DiagramPageFlowable(
                        asset,
                        body[0],
                        body[1],
                        label,
                        self.styles["diagram_label"],
                        self.theme.color("surface"),
                        self.theme.color("diagram_border"),
                        tile_scale,
                        crop_x=x_start,
                        crop_y_top=y_start,
                        part=part,
                        part_count=part_count,
                    )
                )
        return DiagramPlan(orientation=orientation, pages=pages)

    def _make_styles(self) -> dict[str, ParagraphStyle]:
        document = self.theme.data["document"]
        spacing = self.theme.data["spacing"]
        base_size = float(document["base_font_size"])
        leading = base_size * float(document["line_height"])
        paragraph_after = float(spacing["paragraph_after_mm"]) * mm
        before = float(spacing["heading_before_mm"]) * mm
        after = float(spacing["heading_after_mm"]) * mm

        body = ParagraphStyle(
            "body",
            fontName=self.theme.fonts.regular,
            fontSize=base_size,
            leading=leading,
            textColor=self.theme.color("text"),
            spaceAfter=paragraph_after,
            splitLongWords=True,
            allowWidows=0,
            allowOrphans=0,
        )
        styles: dict[str, ParagraphStyle] = {"body": body}
        heading_sizes = {1: 20, 2: 15.5, 3: 12.8, 4: 11.5, 5: 10.8, 6: 10.3}
        for level, size in heading_sizes.items():
            styles[f"h{level}"] = ParagraphStyle(
                f"h{level}",
                parent=body,
                fontName=self.theme.fonts.heading_bold,
                fontSize=size,
                leading=size * 1.18,
                textColor=self.theme.color("heading_text"),
                spaceBefore=before * (1.2 if level <= 2 else 0.8),
                spaceAfter=after,
                keepWithNext=True,
            )
        styles["first_title"] = ParagraphStyle(
            "first-title",
            parent=styles["h1"],
            fontSize=27,
            leading=32,
            textColor=self.theme.color("title_text"),
            spaceBefore=0,
            spaceAfter=0,
        )
        styles["list_body"] = ParagraphStyle(
            "list-body",
            parent=body,
            spaceAfter=float(spacing["list_gap_mm"]) * mm,
        )
        styles["caption"] = ParagraphStyle(
            "caption",
            parent=body,
            fontName=self.theme.fonts.italic,
            fontSize=8.4,
            leading=10.5,
            textColor=self.theme.color("muted_text"),
            alignment=TA_CENTER,
            spaceBefore=1.5 * mm,
            spaceAfter=1 * mm,
        )
        styles["blockquote"] = ParagraphStyle(
            "blockquote",
            parent=body,
            textColor=self.theme.color("blockquote_text"),
            spaceAfter=2 * mm,
        )
        styles["diagram_label"] = ParagraphStyle(
            "diagram-label",
            parent=body,
            fontName=self.theme.fonts.heading_bold,
            fontSize=9.2,
            leading=11,
            textColor=self.theme.color("heading_text"),
            alignment=TA_LEFT,
            spaceAfter=0,
        )
        styles["code"] = ParagraphStyle(
            "code",
            parent=body,
            fontName=self.theme.fonts.monospace,
            fontSize=8.2,
            leading=10.7,
            textColor=self.theme.color("code_text"),
            backColor=self.theme.color("code_background"),
            borderColor=self.theme.color("code_border"),
            borderWidth=0.8,
            borderPadding=3.2 * mm,
            borderRadius=2 * mm,
            leftIndent=0,
            rightIndent=0,
            spaceBefore=1.5 * mm,
            spaceAfter=4 * mm,
        )
        return styles

    def _portrait_body_width(self) -> float:
        return self.portrait_size[0] - self.left_margin - self.right_margin

    def _portrait_body_height(self) -> float:
        return self.portrait_size[1] - self.top_margin - self.bottom_margin


def _tile_starts(content_size: float, viewport_size: float, overlap: float) -> list[float]:
    if content_size <= viewport_size:
        return [0.0]
    step = max(1.0, viewport_size - min(overlap, viewport_size * 0.25))
    count = math.ceil((content_size - overlap) / step)
    maximum = content_size - viewport_size
    return [min(index * step, maximum) for index in range(count)]


def _ellipsize(text: str, font: str, size: float, max_width: float) -> str:
    if pdfmetrics.stringWidth(text, font, size) <= max_width:
        return text
    suffix = "..."
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = text[:middle].rstrip() + suffix
        if pdfmetrics.stringWidth(candidate, font, size) <= max_width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + suffix


def _standalone_image(tokens: list[Token]) -> Token | None:
    meaningful = [token for token in tokens if token.type not in {"link_open", "link_close"}]
    return meaningful[0] if len(meaningful) == 1 and meaningful[0].type == "image" else None


def render_markdown(
    source: str | Path,
    output: str | Path | None = None,
    config: str | Path | None = None,
) -> Path:
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise RenderError(f"Markdown file does not exist: {source_path}")
    output_path = Path(output).expanduser().resolve() if output else source_path.with_suffix(".pdf")
    if output_path == source_path:
        raise RenderError("Input and output paths must be different")
    theme = load_theme(config)
    MarkdownPdfRenderer(source_path, output_path, theme).render()
    return output_path
