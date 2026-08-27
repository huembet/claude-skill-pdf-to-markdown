---
name: pdf-to-markdown
description: Convert entire PDF documents to clean, structured Markdown for full context loading. Use this skill when the user wants to extract ALL text from a PDF into context (not grep/search), when discussing or analyzing PDF content in full, when the user mentions "load the whole PDF", "bring the PDF into context", "read the entire PDF", or when partial extraction/grepping would miss important context. This is the preferred method for PDF text extraction over page-by-page or grep approaches.
---

# PDF to Markdown Converter

Extract complete PDF content as structured Markdown, preserving:
- Headers (detected by font size, converted to # tags)
- Bold, italic, monospace formatting
- Tables (converted to Markdown tables)
- Lists (ordered and unordered)
- Multi-column layouts (correct reading order)
- Code blocks
- **Images** (extracted and copied next to output with relative paths)

## When to Use This Skill

**USE THIS** when:
- User wants the "whole PDF" or "entire document" in context
- Analyzing, summarizing, or discussing PDF content
- User says "load", "read", "bring in", "extract" a PDF
- Grepping/searching would miss context or structure
- PDF has tables, formatting, or structure to preserve

## Environment Setup

This skill uses a dedicated virtual environment at `~/.claude/skills/pdf-to-markdown/.venv/` to avoid polluting the user's working directory.

### First-Time Setup (if .venv doesn't exist)

Versions are pinned in `requirements.txt`. Install from the file rather than by
package name — see "Why the versions are pinned" below.

```bash
# For fast mode only (PyMuPDF):
cd ~/.claude/skills/pdf-to-markdown && uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt

# For --docling mode (high-accuracy tables), add:
cd ~/.claude/skills/pdf-to-markdown && uv pip install --python .venv/bin/python -r requirements-docling.txt
```

### Why the versions are pinned

`pymupdf4llm` 1.27.2.1 began shipping `pymupdf-layout`. When that package is
importable, `to_markdown()` routes through a **second implementation** with a
different parameter set and different page metadata. The switch is silent:
unsupported arguments land in `**kwargs` and are dropped without a warning.

**After a version bump**, re-check against a known PDF with the bundled script:

```bash
~/.claude/skills/pdf-to-markdown/.venv/bin/python \
    ~/.claude/skills/pdf-to-markdown/scripts/verify.py known-document.pdf
```

It prints the installed versions, then asserts what the skill promises: one
`<!-- Page N -->` per page numbered without gaps, image references that resolve,
no images loose beside the PDF, and a cached re-run with identical content. Fast
mode always runs; `--docling` mode runs too when docling is installed, and
`--ocr` adds that variant. Exit code is 0 only if every mode passes.

### Verify Installation
```bash
# Verify fast mode:
~/.claude/skills/pdf-to-markdown/.venv/bin/python -c "import pymupdf; import pymupdf4llm; print('OK')"

# Verify docling mode:
~/.claude/skills/pdf-to-markdown/.venv/bin/python -c "import pymupdf; import docling; import docling_core; print('OK')"
```

## Quick Start

```bash
# Convert PDF to markdown (always extracts images)
~/.claude/skills/pdf-to-markdown/.venv/bin/python ~/.claude/skills/pdf-to-markdown/scripts/pdf_to_md.py document.pdf

# Output: document.md + images/ folder (next to the .md file)
```

## Standard Workflow

When user provides a PDF and wants full content in context:

### Step 1: Ensure the skill venv exists
```bash
test -d ~/.claude/skills/pdf-to-markdown/.venv || (cd ~/.claude/skills/pdf-to-markdown && uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt)
```

### Step 2: Convert PDF to Markdown
```bash
~/.claude/skills/pdf-to-markdown/.venv/bin/python ~/.claude/skills/pdf-to-markdown/scripts/pdf_to_md.py "/path/to/document.pdf"
```

### Step 3: Read the output
```bash
# Output is written to document.md in the same directory as the PDF
cat /path/to/document.md
```

## Caching

PDFs are **aggressively cached** to avoid re-processing. First extraction is slow, every subsequent request is instant.

### How It Works
- **Cache location**: `~/.cache/pdf-to-markdown/<cache_key>/`
- **Cache key**: Based on file content hash + extraction mode
- **Invalidation**: Cache is invalidated when:
  - Source PDF is modified (size or mtime changes)
  - Extractor version changes (automatic re-extraction)
  - Explicitly cleared with `--clear-cache` or `--clear-all-cache`

### Cache Commands
```bash
# Clear cache for a specific PDF
~/.claude/skills/pdf-to-markdown/.venv/bin/python ~/.claude/skills/pdf-to-markdown/scripts/pdf_to_md.py document.pdf --clear-cache

# Clear entire cache
~/.claude/skills/pdf-to-markdown/.venv/bin/python ~/.claude/skills/pdf-to-markdown/scripts/pdf_to_md.py --clear-all-cache

# Show cache statistics
~/.claude/skills/pdf-to-markdown/.venv/bin/python ~/.claude/skills/pdf-to-markdown/scripts/pdf_to_md.py --cache-stats
```

### Cache Contents
```
~/.cache/pdf-to-markdown/<cache_key>/
├── metadata.json    # source path, mtime, size, total_pages
├── full_output.md   # cached full markdown
└── images/          # extracted images
```

## Image Handling

Images are always extracted. They are:
1. **Cached** in `~/.cache/pdf-to-markdown/<cache_key>/images/`
2. **Copied** to `images/` folder next to the output `.md` file
3. **Referenced** in the markdown with relative paths (`images/filename.png`)
4. **Summarized** in a table at the end of the document

### Auto-View Behavior for Images

**IMPORTANT:** When the extracted markdown contains image references like:
```
**[Image: figure_1.png (1200x800, 125.3KB)]**
```

And the user asks about something that might be visual (charts, graphs, diagrams, figures, screenshots, layouts, designs, plots, illustrations), **automatically use the Read tool** to view the relevant image file(s) before answering. Don't ask the user - just look at it.

**Examples of when to auto-view images:**
- User: "What does the chart on page 3 show?" → Read the image file
- User: "Summarize the figures in this paper" → Read all image files
- User: "What's in the diagram?" → Read the image file
- User: "Describe the architecture shown" → Read the image file
- User: "What are the results?" (and there's a results figure) → Read it

## Output Format

The markdown output includes:

### Header (metadata)
```yaml
---
source: document.pdf
total_pages: 42
extracted_at: 2025-01-15T10:30:00
from_cache: true
images_dir: images
---
```

### Page markers

Every page begins with an HTML comment carrying its 1-based page number, in
**both** fast and `--docling` mode:

```markdown
<!-- Page 7 -->

...content of page 7...

<!-- Page 8 -->
```

Markers are invisible when the markdown is rendered, so they cost nothing
visually, but they let you cite a page ("see page 7") and let a reader locate a
passage in the original PDF. Empty pages still get their marker, so the
numbering never has gaps.

### Content with image references
```markdown
<!-- Page 1 -->

# Main Title

## Section Header

Regular paragraph text with **bold**, *italic*, and `code` formatting.

![Figure 1](images/figure_1.png)

**[Image: figure_1.png (800x600, 45.2KB)]**

| Column A | Column B |
|----------|----------|
| Data 1   | Data 2   |
```

### Image summary table (at end)
```markdown
---

## Extracted Images

| # | File | Dimensions | Size |
|---|------|------------|------|
| 1 | figure_1.png | 800x600 | 45.2KB |
| 2 | chart_2.png | 1200x800 | 89.1KB |
```

## Script Reference

Location: `~/.claude/skills/pdf-to-markdown/scripts/pdf_to_md.py`
Checks:   `~/.claude/skills/pdf-to-markdown/scripts/verify.py` (see "After a version bump")

```
Usage: pdf_to_md.py <input.pdf> [output.md] [options]

Options:
  --docling         Use Docling AI for high-accuracy tables (~1 sec/page)
  --ocr             OCR pages with little extractable text (fast mode, slower)
  --no-progress     Disable progress indicator

Cache Options:
  --clear-cache        Clear cache for this PDF and re-extract
  --clear-all-cache    Clear entire cache directory and exit
  --cache-stats        Show cache statistics and exit
```

## High-Accuracy Mode (Docling)

For PDFs with complex tables that need high accuracy, use the `--docling` flag:

```bash
~/.claude/skills/pdf-to-markdown/.venv/bin/python \
    ~/.claude/skills/pdf-to-markdown/scripts/pdf_to_md.py \
    document.pdf --docling
```

**When to use `--docling`:**
- PDF has complex tables (borderless, merged cells, multi-column)
- Table accuracy is critical (medical data, financial reports)
- You're seeing garbled table output in default mode

**Trade-offs:**
- ~1 second per page (vs instant for fast mode)
- First run downloads AI models (~500MB one-time)
- Higher-resolution images (4x default)

**Note:** `--accurate` is an alias for `--docling`.

## Troubleshooting

### "No module named pymupdf4llm" or venv doesn't exist
Recreate the skill's virtual environment:
```bash
# For fast mode:
cd ~/.claude/skills/pdf-to-markdown && rm -rf .venv && uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt

# For docling mode:
cd ~/.claude/skills/pdf-to-markdown && rm -rf .venv && uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt -r requirements-docling.txt
```

### Output is empty or nearly empty
The PDF probably has no text layer (a scan). Fast mode does not OCR by default,
because on born-digital PDFs OCR costs several times the runtime for no gain.
Re-run with `--ocr`:
```bash
~/.claude/skills/pdf-to-markdown/.venv/bin/python ~/.claude/skills/pdf-to-markdown/scripts/pdf_to_md.py document.pdf --ocr
```
The script warns on stderr when output looks like it needed OCR.

### `--docling` fails with a network error
Docling downloads its models from Hugging Face on first use. Behind a proxy or
in a sandbox that blocks `huggingface.co`, the run fails with `403 Forbidden`.
Either allow that host, or populate `~/.cache/huggingface` on a connected
machine and copy it across. Fast mode needs no downloads.

### Poor extraction quality
- Try `--docling` for complex tables

### Tables not formatting correctly
For complex tables, use `--docling` mode which uses IBM's TableFormer AI model.

## Comparison with Other Approaches

| Approach | Use Case | Limitations |
|----------|----------|-------------|
| **This skill (pymupdf4llm)** | Full document context with images | Large PDFs may exceed context |
| **--docling mode** | Complex tables, medical/financial PDFs | Slower (~1 sec/page), larger models |
| Grepping PDF | Find specific text | Loses structure, no images |
| Page-by-page extraction | Targeted pages | Manual, loses cross-page context |
| Read tool on PDF | Quick preview | Limited formatting preservation |
