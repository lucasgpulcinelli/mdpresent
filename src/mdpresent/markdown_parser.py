"""Turn Markdown-It tokens into a small document model for ReportLab."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
from typing import Any

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.admon import admon_plugin
from mdit_py_plugins.attrs import attrs_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.subscript import sub_plugin
from mdit_py_plugins.superscript import superscript_plugin
from mdit_py_plugins.tasklists import tasklists_plugin


@dataclass
class Block:
    pass


@dataclass
class Heading(Block):
    level: int
    children: list[Token]


@dataclass
class Paragraph(Block):
    children: list[Token]


@dataclass
class CodeBlock(Block):
    code: str
    language: str = ""


@dataclass
class MermaidBlock(Block):
    code: str


@dataclass
class ListBlock(Block):
    ordered: bool
    items: list[list[Block]]
    start: int = 1


@dataclass
class QuoteBlock(Block):
    blocks: list[Block]


@dataclass
class DefinitionListBlock(Block):
    items: list[tuple[list[Token], list[list[Block]]]]


@dataclass
class AdmonitionBlock(Block):
    kind: str
    title: list[Token]
    blocks: list[Block]


@dataclass
class TableBlock(Block):
    header: list[list[Token]]
    rows: list[list[list[Token]]]
    alignments: list[str | None] = field(default_factory=list)


@dataclass
class Rule(Block):
    pass


@dataclass
class PageBreakBlock(Block):
    pass


@dataclass
class HtmlBlock(Block):
    text: str


@dataclass
class FootnotesBlock(Block):
    items: list[tuple[int, list[Block]]]


@dataclass
class ParsedDocument:
    blocks: list[Block]
    metadata: dict[str, Any]
    title: str | None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def text(self) -> str:
        return "".join(self.parts).strip()


def _markdown_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark", {"html": True, "linkify": True})
    parser.enable("table")
    parser.enable("strikethrough")
    parser.use(attrs_plugin, allowed=("id", "class", "width", "height", "label"))
    parser.use(deflist_plugin)
    parser.use(admon_plugin)
    parser.use(sub_plugin)
    parser.use(superscript_plugin)
    parser.use(footnote_plugin)
    parser.use(tasklists_plugin, enabled=True, label=True)
    return parser


def parse_markdown(source: str) -> ParsedDocument:
    metadata, body = _split_front_matter(source)
    tokens = _markdown_parser().parse(body)
    blocks, _ = _parse_blocks(tokens, 0, set())
    title = str(metadata.get("title")) if metadata.get("title") else None
    if title is None:
        first_h1 = next((block for block in blocks if isinstance(block, Heading) and block.level == 1), None)
        if first_h1:
            title = inline_plain_text(first_h1.children)
    return ParsedDocument(blocks=blocks, metadata=metadata, title=title)


def _split_front_matter(source: str) -> tuple[dict[str, Any], str]:
    if not source.startswith("---\n"):
        return {}, source
    match = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", source, re.DOTALL)
    if not match:
        return {}, source
    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}, source
    if not isinstance(metadata, dict):
        return {}, source
    return metadata, source[match.end() :]


def _parse_blocks(tokens: list[Token], index: int, stop: set[str]) -> tuple[list[Block], int]:
    blocks: list[Block] = []
    while index < len(tokens) and tokens[index].type not in stop:
        token = tokens[index]

        if token.type == "heading_open":
            level = int(token.tag[1:])
            inline = tokens[index + 1]
            blocks.append(Heading(level, list(inline.children or [])))
            index += 3
        elif token.type == "paragraph_open":
            inline = tokens[index + 1]
            blocks.append(Paragraph(list(inline.children or [])))
            index += 3
        elif token.type in {"bullet_list_open", "ordered_list_open"}:
            block, index = _parse_list(tokens, index)
            blocks.append(block)
        elif token.type == "blockquote_open":
            nested, index = _parse_blocks(tokens, index + 1, {"blockquote_close"})
            blocks.append(QuoteBlock(nested))
            index += 1
        elif token.type == "dl_open":
            block, index = _parse_definition_list(tokens, index)
            blocks.append(block)
        elif token.type == "admonition_open":
            block, index = _parse_admonition(tokens, index)
            blocks.append(block)
        elif token.type == "fence":
            language = (token.info or "").strip().split(maxsplit=1)[0].lower()
            if language == "mermaid":
                blocks.append(MermaidBlock(token.content.strip()))
            else:
                blocks.append(CodeBlock(token.content.rstrip("\n"), language))
            index += 1
        elif token.type == "code_block":
            blocks.append(CodeBlock(token.content.rstrip("\n")))
            index += 1
        elif token.type == "table_open":
            block, index = _parse_table(tokens, index)
            blocks.append(block)
        elif token.type == "hr":
            blocks.append(Rule())
            index += 1
        elif token.type == "html_block":
            raw = token.content.strip()
            if re.fullmatch(r"<!--\s*pagebreak\s*-->", raw, re.IGNORECASE):
                blocks.append(PageBreakBlock())
            else:
                extractor = _TextExtractor()
                extractor.feed(raw)
                if extractor.text():
                    blocks.append(HtmlBlock(extractor.text()))
            index += 1
        elif token.type == "footnote_block_open":
            block, index = _parse_footnotes(tokens, index)
            blocks.append(block)
        else:
            index += 1
    return blocks, index


def _parse_list(tokens: list[Token], index: int) -> tuple[ListBlock, int]:
    opening = tokens[index]
    ordered = opening.type == "ordered_list_open"
    start_value = opening.attrGet("start") or "1"
    try:
        start = int(start_value)
    except ValueError:
        start = 1
    closing = "ordered_list_close" if ordered else "bullet_list_close"
    items: list[list[Block]] = []
    index += 1
    while index < len(tokens) and tokens[index].type != closing:
        if tokens[index].type != "list_item_open":
            index += 1
            continue
        item_blocks, index = _parse_blocks(tokens, index + 1, {"list_item_close"})
        items.append(item_blocks)
        index += 1
    return ListBlock(ordered=ordered, items=items, start=start), index + 1


def _parse_definition_list(tokens: list[Token], index: int) -> tuple[DefinitionListBlock, int]:
    items: list[tuple[list[Token], list[list[Block]]]] = []
    index += 1
    while index < len(tokens) and tokens[index].type != "dl_close":
        if tokens[index].type != "dt_open":
            index += 1
            continue
        term: list[Token] = []
        if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
            term = list(tokens[index + 1].children or [])
        index += 1
        while index < len(tokens) and tokens[index].type != "dt_close":
            index += 1
        index += 1

        definitions: list[list[Block]] = []
        while index < len(tokens) and tokens[index].type == "dd_open":
            blocks, index = _parse_blocks(tokens, index + 1, {"dd_close"})
            definitions.append(blocks)
            index += 1
        items.append((term, definitions))
    return DefinitionListBlock(items), index + 1


def _parse_admonition(tokens: list[Token], index: int) -> tuple[AdmonitionBlock, int]:
    opening = tokens[index]
    kind = str(opening.meta.get("tag", "note"))
    title: list[Token] = []
    index += 1
    if index < len(tokens) and tokens[index].type == "admonition_title_open":
        if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
            title = list(tokens[index + 1].children or [])
        while index < len(tokens) and tokens[index].type != "admonition_title_close":
            index += 1
        index += 1
    blocks, index = _parse_blocks(tokens, index, {"admonition_close"})
    return AdmonitionBlock(kind=kind, title=title, blocks=blocks), index + 1


def _parse_table(tokens: list[Token], index: int) -> tuple[TableBlock, int]:
    header: list[list[Token]] = []
    rows: list[list[list[Token]]] = []
    alignments: list[str | None] = []
    current_row: list[list[Token]] | None = None
    in_header = False
    index += 1

    while index < len(tokens) and tokens[index].type != "table_close":
        token = tokens[index]
        if token.type == "thead_open":
            in_header = True
        elif token.type == "thead_close":
            in_header = False
        elif token.type == "tr_open":
            current_row = []
        elif token.type in {"th_open", "td_open"}:
            cell_children: list[Token] = []
            style = token.attrGet("style") or ""
            if token.type == "th_open":
                if "right" in style:
                    alignments.append("RIGHT")
                elif "center" in style:
                    alignments.append("CENTER")
                else:
                    alignments.append("LEFT")
            cursor = index + 1
            while cursor < len(tokens) and tokens[cursor].type not in {"th_close", "td_close"}:
                if tokens[cursor].type == "inline":
                    cell_children.extend(tokens[cursor].children or [])
                cursor += 1
            if current_row is not None:
                current_row.append(cell_children)
            index = cursor
        elif token.type == "tr_close" and current_row is not None:
            if in_header:
                header = current_row
            else:
                rows.append(current_row)
            current_row = None
        index += 1

    if not header and rows:
        header = rows.pop(0)
    return TableBlock(header=header, rows=rows, alignments=alignments[: len(header)]), index + 1


def _parse_footnotes(tokens: list[Token], index: int) -> tuple[FootnotesBlock, int]:
    items: list[tuple[int, list[Block]]] = []
    index += 1
    while index < len(tokens) and tokens[index].type != "footnote_block_close":
        if tokens[index].type != "footnote_open":
            index += 1
            continue
        number = int(tokens[index].meta.get("id", len(items))) + 1
        item_blocks, index = _parse_blocks(tokens, index + 1, {"footnote_close"})
        items.append((number, item_blocks))
        index += 1
    return FootnotesBlock(items), index + 1


def inline_plain_text(tokens: list[Token]) -> str:
    parts: list[str] = []
    for token in tokens:
        if token.type in {"text", "code_inline"}:
            parts.append(token.content)
        elif token.type == "image":
            parts.append(token.content)
        elif token.type in {"softbreak", "hardbreak"}:
            parts.append(" ")
        elif token.type == "footnote_ref":
            parts.append(f"[{int(token.meta.get('id', 0)) + 1}]")
        elif token.children:
            parts.append(inline_plain_text(token.children))
    return re.sub(r"\s+", " ", "".join(parts)).strip()
