# mDpresent

mDpresent turns Markdown into a themed PDF. ReportLab lays out every page. Mermaid CLI renders diagrams to SVG, and `svglib` passes the SVG paths to ReportLab without rasterizing them.

## Set up

The commands below use the requested shared virtual environment and the project's local Mermaid runtime.

```bash
cd /home/lucasegp/mdpresent
/home/lucasegp/venv/bin/pip install -e '.[dev]'
PUPPETEER_SKIP_DOWNLOAD=1 npm install
```

Chrome or Chromium must be installed. The renderer finds `google-chrome`, `chromium`, or `chromium-browser`; `mermaid.chrome` can point to a different executable.

## Render a document

```bash
/home/lucasegp/venv/bin/mdpresent examples/features.md \
  --config examples/theme.yml \
  --output examples/output/features.pdf
```

Without `--output`, the PDF is written next to the Markdown file. Without `--config`, mDpresent uses the built-in theme.

An example branded theme is included:

```bash
mdpresent examples/features.md -c themes/icmc.yml -o features-icmc.pdf
```

The ICMC theme follows the supplied ICMC/USP identity manual with a blue-only header.  See `themes/README.md` for the source details.

To make a complete editable theme file:

```bash
/home/lucasegp/venv/bin/mdpresent --write-default-config my-theme.yml
```

The default theme draws a small built-in mark. Set `header.logo` to a file path to replace it; relative paths resolve from the theme file. Raster logos keep their own brand colors. SVG logos can use `{{ logo_primary }}` and `{{ logo_secondary }}` placeholders so their colors follow the YAML theme.

## Markdown coverage

The renderer handles headings, emphasis, strong text, strikeout, links, inline code, fenced code with syntax color, local or remote images, labeled figures, block quotes, admonitions, definition lists, subscript and superscript, rules, footnotes, task lists, ordered and unordered nested lists, tables that repeat their header across pages, and explicit `<!-- pagebreak -->` markers.

Standalone image alt text is used as its label by default. A standard Markdown image title overrides it, and the `label` attribute is the most explicit option. Image attributes also accept an anchor plus `width` and `height` in `%`, `mm`, `cm`, `in`, `pt`, or `px`:

```markdown
![Accessible description](assets/chart.png "Figure 1 — Quarterly results"){#fig-results width="70%"}
![Accessible description](assets/chart.png){label="Figure 2 — Detail" width=95mm}
```

Definition lists, callouts, and scientific notation use these extensions:

```markdown
Release gate
: A condition that must pass before publishing.

!!! warning "Review required"
    Water is H~2~O and this sample contains 2^10^ records.
```

A Mermaid fence stays vector in the PDF:

````markdown
```mermaid
flowchart LR
    A[Markdown] --> B[SVG]
    B --> C[ReportLab PDF]
```
````

Small diagrams stay in the text flow. If a chart would become too small, mDpresent gives it one dedicated page and expands that page in either dimension until the full vector chart fits at `minimum_font_size`. The custom page is never smaller than the configured paper size, and the following content returns to the normal page template. `dedicated_page_when_scale_below` controls when a chart leaves the text flow; `font_size` and `minimum_font_size` control its rendered and minimum PDF label sizes.
