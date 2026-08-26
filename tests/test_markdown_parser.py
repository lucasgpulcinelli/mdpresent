from mdpresent.markdown_parser import (
    AdmonitionBlock,
    DefinitionListBlock,
    Heading,
    ListBlock,
    Paragraph,
    TableBlock,
    parse_markdown,
)


def test_parser_keeps_nested_lists_tables_and_front_matter() -> None:
    source = """---
title: Parser sample
---
# Main title

- outer
  1. inner one
  2. inner two

| Left | Right |
|:--|--:|
| A | 2 |
"""
    parsed = parse_markdown(source)
    assert parsed.title == "Parser sample"
    assert any(isinstance(block, Heading) for block in parsed.blocks)
    outer = next(block for block in parsed.blocks if isinstance(block, ListBlock))
    assert any(isinstance(child, ListBlock) for child in outer.items[0])
    table = next(block for block in parsed.blocks if isinstance(block, TableBlock))
    assert table.alignments == ["LEFT", "RIGHT"]
    assert len(table.rows) == 1


def test_first_h1_becomes_the_document_title() -> None:
    parsed = parse_markdown("## Preamble\n\n# Actual title\n")
    assert parsed.title == "Actual title"


def test_parser_supports_extended_report_blocks_and_labeled_images() -> None:
    parsed = parse_markdown(
        """Glossary term
: A definition with **markup**.

!!! warning "Review required"
    Water is H~2~O and ten squared is 10^2^.

![Alternative text](chart.png "Fallback title"){#fig-chart width="55%" label="Figure 1: Results"}
"""
    )

    definition = next(block for block in parsed.blocks if isinstance(block, DefinitionListBlock))
    assert len(definition.items) == 1
    admonition = next(block for block in parsed.blocks if isinstance(block, AdmonitionBlock))
    assert admonition.kind == "warning"
    assert [token.type for token in admonition.blocks[0].children] == [
        "text",
        "sub_open",
        "text",
        "sub_close",
        "text",
        "sup_open",
        "text",
        "sup_close",
        "text",
    ]
    paragraph = next(block for block in parsed.blocks if isinstance(block, Paragraph))
    image = paragraph.children[0]
    assert image.type == "image"
    assert image.attrGet("id") == "fig-chart"
    assert image.attrGet("width") == "55%"
    assert image.attrGet("label") == "Figure 1: Results"
