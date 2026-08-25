from mdpresent.markdown_parser import Heading, ListBlock, TableBlock, parse_markdown


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

