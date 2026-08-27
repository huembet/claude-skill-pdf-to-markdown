# PDF to Markdown Converter

Convert PDF documents to clean, structured Markdown with table and image extraction.

## Features

- **Text extraction** with formatting preservation (headers, bold, italic, lists)
- **Page markers** — every page starts with `<!-- Page N -->` in both modes
- **Table extraction** with two modes:
  - Fast mode: PyMuPDF (good for simple tables)
  - Accurate mode: IBM Docling AI (better for complex/borderless tables)
- **Image extraction** to cache directory with paths in output
- **Aggressive caching** - extract once, reuse forever

## Installation

```bash
cd ~/.claude/skills/pdf-to-markdown
uv venv .venv

# For fast mode (default):
uv pip install --python .venv/bin/python -r requirements.txt

# For --docling mode (high-accuracy tables), add:
uv pip install --python .venv/bin/python -r requirements-docling.txt
```

Versions are pinned. `pymupdf4llm` 1.27.2.1 started shipping `pymupdf-layout`,
which silently switches `to_markdown()` to a second implementation with a
different parameter set — unsupported arguments are dropped into `**kwargs`
without a warning. Re-verify page markers, image references and runtime before
raising the pins.

## Usage

```bash
# Basic conversion (outputs to document.md)
.venv/bin/python scripts/pdf_to_md.py document.pdf

# High-accuracy tables (slower)
.venv/bin/python scripts/pdf_to_md.py document.pdf --docling

# Custom output path
.venv/bin/python scripts/pdf_to_md.py document.pdf output.md
```

## Options

| Option | Description |
|--------|-------------|
| `--docling` | Use Docling AI for high-accuracy tables |
| `--ocr` | OCR pages with little extractable text (fast mode, much slower) |
| `--no-progress` | Disable progress indicator |
| `--clear-cache` | Clear cache for this PDF and re-extract |
| `--clear-all-cache` | Clear entire cache |
| `--cache-stats` | Show cache statistics |

## Project Structure

```
scripts/
  pdf_to_md.py              # Main CLI tool
  extractor.py              # PDF extraction library (fast + accurate modes)
  verify.py                 # Acceptance checks against a real PDF
requirements.txt            # Fast mode, pinned
requirements-docling.txt    # Additional pins for --docling
```

## Verifying a change

```bash
.venv/bin/python scripts/verify.py known-document.pdf
```

Prints the installed versions, then checks each available mode: page markers
numbered without gaps, image references that resolve, no images loose beside the
PDF, and a cached re-run with identical content. Exits non-zero if any mode
fails. Run it after changing extraction or raising a pin — particularly for
`--docling`, whose models only download where `huggingface.co` is reachable.

## Cache

PDFs are cached in `~/.cache/pdf-to-markdown/`. Cache is invalidated when:
- Source PDF is modified
- Extractor version changes
- Explicitly cleared with `--clear-cache`

## License

MIT
