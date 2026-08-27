"""
PDF extraction with multiple backends:
- Fast mode: PyMuPDF with multi-strategy table detection (good for simple tables)
- Accurate mode: IBM Docling with TableFormer AI (better for complex/borderless tables)
"""

import inspect
import os
import re
import sys
from pathlib import Path

# Suppress PyMuPDF's "Consider using pymupdf_layout" recommendation
# This prints to stdout and pollutes --stdout output
os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")

# Version for cache invalidation - increment when extraction logic changes
# Format: major.minor.patch
# 3.1.0: Page separators now use <!-- PAGE_BREAK --> instead of -----
#        Image extraction includes nested XObjects (full=True)
# 3.2.0: Fast mode now includes image references in markdown (write_images=True)
#        Cache keys now include no_images flag to avoid contamination
# 3.3.0: Image paths in cached markdown now use relative 'images/' prefix
#        (fixes broken temp directory references in cached output)
# 4.0.0: Pages are now marked with <!-- Page N --> in both modes.
#        The old <!-- PAGE_BREAK --> substitution was dead code: it looked for
#        "\n-----\n", which pymupdf4llm never emits (page_separators defaults
#        to False, and when enabled the text is "--- end of page=N ---").
#        Fast mode no longer passes table_strategy and no longer runs OCR
#        unless asked; see extract_pdf_fast() for why.
EXTRACTOR_VERSION = "4.0.0"


def check_docling_models():
    """Check if Docling models are downloaded."""
    try:
        from huggingface_hub import scan_cache_dir

        cache_info = scan_cache_dir()
        # Check for docling models in HF cache
        docling_repos = [r for r in cache_info.repos if "docling" in r.repo_id.lower()]
        return len(docling_repos) > 0
    except Exception:
        return False


PAGE_MARKER = "<!-- Page {} -->"


def _page_number(chunk: dict, fallback: int) -> int:
    """
    Read the 1-based page number from a pymupdf4llm page chunk.

    The two to_markdown implementations disagree on the key: the layout path
    stores it under metadata["page_number"], the legacy path under
    metadata["page"]. Fall back to the chunk's position if neither is present.
    """
    metadata = chunk.get("metadata") or {}
    for key in ("page_number", "page"):
        value = metadata.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return fallback


def _join_page_chunks(chunks: list) -> str:
    """Join pymupdf4llm page chunks into markdown with <!-- Page N --> markers."""
    parts = []
    for position, chunk in enumerate(chunks, start=1):
        text = (chunk.get("text") or "").strip()
        marker = PAGE_MARKER.format(_page_number(chunk, position))
        parts.append(f"{marker}\n\n{text}")
    return "\n\n".join(parts) + "\n"


def _looks_untextual(markdown: str, page_count: int) -> bool:
    """
    True if the markdown carries almost no text beyond its page markers.

    Used to warn about scanned PDFs, which yield empty pages unless OCR runs.
    """
    if page_count <= 0:
        return False
    body = re.sub(r"<!-- Page \d+ -->", "", markdown)
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    return len(body.strip()) < 20 * page_count


def extract_pdf_fast(
    pdf_path: str,
    image_dir: str = None,
    show_progress: bool = False,
    use_ocr: bool = False,
) -> str:
    """
    Fast PDF extraction using PyMuPDF, one <!-- Page N --> marker per page.

    Two upstream details drive the arguments below. Since pymupdf4llm 1.27.2.1
    the package pulls in pymupdf-layout and routes to_markdown() through a
    second implementation (helpers/document_layout.py), chosen at import time
    by whether pymupdf.layout is importable. That layout path:

      * does not accept table_strategy - it lands in **kwargs and is dropped,
        so passing it only creates the illusion of control. On the legacy path
        table_strategy="text" actively hurts: on a two-column journal article
        it shredded the cover page into 673 pseudo-table rows.
      * runs OCR by default, which cost ~5x the runtime on a born-digital test
        document while extracting one image fewer. Hence use_ocr=False here,
        with a warning when the result looks like it needed OCR after all.

    Args:
        pdf_path: Path to the PDF file
        image_dir: Directory to save extracted images (None = skip images)
        show_progress: Whether to show progress output
        use_ocr: Run OCR on pages with little extractable text (much slower)

    Returns:
        Markdown string of the PDF content, pages separated by page markers,
        with image references if image_dir was provided.
    """
    import pymupdf4llm

    if show_progress:
        print("Extracting with PyMuPDF (fast mode)...", file=sys.stderr)

    # page_chunks=True is the only dependable way to get page boundaries.
    # page_separators defaults to False in both implementations, so no
    # separator is emitted at all, and enabling it yields different text per
    # path ("--- end of page=N ---" vs "--- end of {page.page_number=} ---").
    chunks = pymupdf4llm.to_markdown(
        pdf_path,
        show_progress=show_progress,
        write_images=image_dir is not None,
        # Upstream calls .strip() on this (helpers/utils.py, md_path), so it
        # must be a str - a Path raises AttributeError.
        image_path=str(image_dir) if image_dir is not None else "",
        # Only honoured by the legacy path, where it keeps images from being
        # written next to the source PDF (pymupdf4llm issue #352). The layout
        # path overrides it with the document's own name.
        filename=Path(pdf_path).stem,
        page_chunks=True,
        # Ignored by the legacy path, which has no OCR stage.
        use_ocr=use_ocr,
    )

    markdown = _join_page_chunks(chunks)

    if not use_ocr and _looks_untextual(markdown, len(chunks)):
        print(
            "WARNING: Barely any text extracted - this may be a scanned PDF. "
            "Retry with --ocr.",
            file=sys.stderr,
        )

    return markdown


def _save_docling_images(result, output_dir: Path) -> list:
    """
    Save images from a Docling conversion result to output directory.

    Images are saved in iteration order, which matches the order of
    <!-- image --> placeholders in the exported markdown.

    Args:
        result: Docling ConversionResult object
        output_dir: Directory to save images to

    Returns:
        List of saved image paths (in iteration order)
    """
    from docling_core.types.doc.document import PictureItem

    output_dir.mkdir(parents=True, exist_ok=True)
    image_paths = []

    # Restricted to PictureItem on purpose: those are exactly the elements that
    # export_to_markdown renders as an <!-- image --> placeholder. Accepting any
    # element with an .image attribute would let a table with a rendered image
    # slip in and shift every later placeholder onto the wrong picture.
    for i, (element, _level) in enumerate(result.document.iterate_items()):
        if isinstance(element, PictureItem) and element.image is not None:
            img_path = output_dir / f"figure_{i:04d}.png"
            element.image.pil_image.save(str(img_path))
            image_paths.append(str(img_path))

    return image_paths


# Sentinel handed to docling's export_to_markdown; replaced by numbered markers.
_DOCLING_PAGE_BREAK = "<!-- DOCLING_PAGE_BREAK -->"


def _mark_pages(segments: list) -> str:
    """Prefix each page segment with its <!-- Page N --> marker."""
    return (
        "\n\n".join(
            f"{PAGE_MARKER.format(number)}\n\n{segment.strip()}"
            for number, segment in enumerate(segments, start=1)
        )
        + "\n"
    )


def _docling_export_support(document) -> set:
    """
    Which page-aware export parameters the installed docling-core offers.

    page_break_placeholder arrived in docling-core 2.24.0 and page_no in 2.26.0,
    and docling does not pin docling-core tightly - an older core can sit under a
    current docling, where either argument raises TypeError. Inspecting the
    signature keeps a genuine export failure from being read as a missing
    feature, which a bare `except TypeError` around the call would do.
    """
    try:
        parameters = inspect.signature(document.export_to_markdown).parameters
    except (TypeError, ValueError):
        return set()
    return {
        name for name in ("page_break_placeholder", "page_no") if name in parameters
    }


def _docling_to_markdown(document, image_mode) -> str:
    """
    Export a DoclingDocument to markdown carrying <!-- Page N --> markers.

    Preferred route is a single whole-document export with a page-break
    placeholder. Docling's per-page export is documented to duplicate tables in
    larger documents, so it only serves as a fallback - either when the
    placeholder route yields a segment count that disagrees with the page count,
    or when the installed docling-core is too old to offer the placeholder.
    """
    supported = _docling_export_support(document)
    total_pages = document.num_pages()

    if "page_break_placeholder" in supported:
        markdown = document.export_to_markdown(
            image_mode=image_mode,
            page_break_placeholder=_DOCLING_PAGE_BREAK,
        )
        segments = markdown.split(_DOCLING_PAGE_BREAK)

        if not total_pages or len(segments) == total_pages:
            return _mark_pages(segments)

        # Numbering these 1..n would put wrong page numbers on real content -
        # an empty page yields one break too few - so ask page by page instead.
        print(
            f"WARNING: {len(segments)} page-break segments for {total_pages} pages; "
            "falling back to per-page export.",
            file=sys.stderr,
        )

    if "page_no" in supported and total_pages:
        return _mark_pages(
            [
                document.export_to_markdown(page_no=number, image_mode=image_mode)
                for number in range(1, total_pages + 1)
            ]
        )

    # Wrong page numbers would be worse than none, so degrade to an unmarked
    # export and say exactly what would fix it.
    print(
        "WARNING: this docling-core exposes neither page_break_placeholder nor "
        "page_no, so the output carries no page markers. Upgrade with "
        "'uv pip install -U docling-core' (needs >= 2.26.0).",
        file=sys.stderr,
    )
    return document.export_to_markdown(image_mode=image_mode)


def extract_pdf_docling(
    pdf_path: str,
    output_dir: str = None,
    images_scale: float = 4.0,
    show_progress: bool = False,
) -> tuple:
    """
    Extract PDF using Docling with accurate tables + high-res images.

    Uses IBM's TableFormer AI model for ~93.6% table extraction accuracy.
    Also extracts images at configurable resolution (default 4x for crisp images).

    Args:
        pdf_path: Path to the PDF file
        output_dir: Directory to save extracted images (None = skip images)
        images_scale: Image resolution multiplier (default: 4.0 for high-res)
        show_progress: Whether to show progress output

    Returns:
        tuple: (markdown: str, image_paths: list[str])
    """
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling_core.types.doc.base import ImageRefMode

    # Check if this is first run (models need downloading)
    if not check_docling_models():
        print(
            "First run: downloading Docling AI models (one-time setup, ~2-3 minutes)...",
            file=sys.stderr,
        )

    if show_progress:
        print(
            f"Processing PDF with Docling (accurate mode, ~1 sec/page)...",
            file=sys.stderr,
        )

    # Configure pipeline for accurate tables + image extraction
    pipeline_options = PdfPipelineOptions(
        do_table_structure=True,
        generate_picture_images=output_dir is not None,
        images_scale=images_scale,
    )
    pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )

    # Convert the document
    result = converter.convert(pdf_path)

    # Check for conversion errors
    if hasattr(result, "errors") and result.errors:
        for error in result.errors:
            print(f"WARNING: Docling conversion error: {error}", file=sys.stderr)

    # Check conversion status
    from docling.datamodel.base_models import ConversionStatus

    if hasattr(result, "status") and result.status != ConversionStatus.SUCCESS:
        print(
            f"WARNING: Docling conversion status: {result.status.name}",
            file=sys.stderr,
        )

    # Save images to output directory (order matters for placeholder replacement)
    image_paths = []
    if output_dir:
        image_paths = _save_docling_images(result, Path(output_dir))
        if show_progress and image_paths:
            print(
                f"Extracted {len(image_paths)} images at {images_scale}x resolution",
                file=sys.stderr,
            )

    # Export markdown with placeholders, one <!-- Page N --> marker per page
    md = _docling_to_markdown(result.document, ImageRefMode.PLACEHOLDER)

    # Replace placeholders with actual image references (order must match iteration order)
    for img_path in image_paths:
        md = md.replace("<!-- image -->", f"![Figure](images/{Path(img_path).name})", 1)

    return md, image_paths


def extract_pdf_to_markdown(
    pdf_path: str, accurate: bool = False, show_progress: bool = False
) -> str:
    """
    Extract PDF to markdown with configurable accuracy/speed trade-off.

    Args:
        pdf_path: Path to the PDF file
        accurate: If True, use Docling AI (better for complex tables, slower).
                  If False, use PyMuPDF (fast, good for simple tables).
        show_progress: Whether to show progress output

    Returns:
        Markdown string of the PDF content
    """
    if accurate:
        # Use Docling without image extraction
        md, _ = extract_pdf_docling(
            pdf_path, output_dir=None, show_progress=show_progress
        )
        return md
    else:
        return extract_pdf_fast(pdf_path, show_progress=show_progress)


def get_page_count(pdf_path: str) -> int:
    """Get the number of pages in a PDF using pymupdf (faster than Docling for this)."""
    import pymupdf

    doc = pymupdf.open(pdf_path)
    count = len(doc)
    doc.close()
    return count


def extract_images(pdf_path: str, output_dir: str, show_progress: bool = False) -> list:
    """
    Extract images from PDF to output directory.

    Uses pymupdf for image extraction since Docling focuses on document structure.
    Deduplicates by xref to avoid extracting the same image multiple times
    (e.g., icons/logos reused across pages).

    Returns:
        List of extracted image paths
    """
    import pymupdf

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(pdf_path)
    extracted = []
    image_count = 0
    seen_xrefs = set()  # Track already-extracted images by xref

    for page_num in range(len(doc)):
        page = doc[page_num]
        # full=True includes images nested inside form XObjects (common in
        # documents exported from Word/PowerPoint)
        images = page.get_images(full=True)

        for img_index, img in enumerate(images):
            try:
                xref = img[0]

                # Skip if we've already extracted this image
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)

                pix = pymupdf.Pixmap(doc, xref)

                # Convert CMYK to RGB if necessary
                if pix.n - pix.alpha > 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)

                image_count += 1
                img_filename = f"image_{image_count:04d}.png"
                img_path = output_path / img_filename
                pix.save(str(img_path))
                extracted.append(str(img_path))

                pix = None
            except Exception as e:
                # Log instead of silently swallowing errors
                print(
                    f"WARNING: Failed to extract image {img_index} on page {page_num + 1}: {e}",
                    file=sys.stderr,
                )
                continue

    doc.close()

    if show_progress and extracted:
        print(f"Extracted {len(extracted)} unique images", file=sys.stderr)

    return extracted
