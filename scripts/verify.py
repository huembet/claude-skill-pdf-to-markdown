#!/usr/bin/env python3
"""
Acceptance checks for the pdf-to-markdown skill.

Drives the real CLI over a PDF and asserts the properties the skill promises:
one page marker per page numbered without gaps, image references that resolve,
images written where the markdown says they are, and a cached re-run that
reproduces the same content. Prints the installed versions first, so a report
always says which combination of packages produced it.

The docling path needs models that only download on a machine with access to
huggingface.co, which is why this exists as a script rather than a fixed
transcript: run it wherever docling actually works.

Usage:
    python verify.py <input.pdf>                 # fast mode, plus docling if installed
    python verify.py <input.pdf> --skip-docling  # fast mode only
    python verify.py <input.pdf> --ocr           # also check the --ocr variant
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

PAGE_MARKER_RE = re.compile(r"<!-- Page (\d+) -->")
IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
# add_metadata_header() stamps a timestamp and a from_cache flag, so a cached
# run is expected to differ there and nowhere else.
HEADER_RE = re.compile(r"\A---\n.*?\n---\n\n", re.DOTALL)

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


def environment_report():
    """Installed versions plus which pymupdf4llm implementation is active."""
    lines = []
    for package in (
        "pymupdf",
        "pymupdf4llm",
        "pymupdf-layout",
        "docling",
        "docling-core",
    ):
        try:
            lines.append(f"  {package:<16} {version(package)}")
        except PackageNotFoundError:
            lines.append(f"  {package:<16} not installed")

    try:
        import pymupdf4llm

        # True means to_markdown() routes through helpers/document_layout.py,
        # which takes a different parameter set than the legacy implementation.
        active = getattr(pymupdf4llm, "_use_layout", None)
        label = {True: "layout", False: "legacy"}.get(active, "unknown")
        lines.append(f"  {'implementation':<16} {label}")
    except ImportError:
        lines.append(f"  {'implementation':<16} pymupdf4llm not installed")

    return lines


def docling_installed():
    try:
        import docling  # noqa: F401
        import docling_core  # noqa: F401
    except ImportError:
        return False
    return True


def body_of(markdown):
    """Markdown without the generated metadata header."""
    return HEADER_RE.sub("", markdown)


def run_cli(pdf_path, extra_args):
    """Run pdf_to_md.py on pdf_path, returning (result, elapsed_seconds)."""
    command = [
        sys.executable,
        str(SCRIPT_DIR / "pdf_to_md.py"),
        str(pdf_path),
        "--no-progress",
        *extra_args,
    ]
    started = time.time()
    result = subprocess.run(command, capture_output=True, text=True)
    return result, time.time() - started


def diagnostic_lines(stderr, limit=8):
    """
    The lines worth showing from a failed run.

    Dependencies log freely to stderr (RapidOCR alone emits several INFO lines
    per run), so a plain tail buries the actual error. Prefer the script's own
    ERROR/HINT/WARNING lines and only fall back to the tail when there are none.
    """
    lines = [line.rstrip() for line in (stderr or "").splitlines() if line.strip()]
    flagged = [
        line for line in lines if line.startswith(("ERROR:", "HINT:", "WARNING:"))
    ]
    return (flagged or lines)[-limit:]


def check_markers(markdown, expected_pages):
    found = [int(n) for n in PAGE_MARKER_RE.findall(markdown)]
    if not found:
        return False, "no page markers at all"
    if found != list(range(1, expected_pages + 1)):
        return False, (
            f"{len(found)} markers for {expected_pages} pages, "
            f"sequence starts {found[:5]}"
        )
    return True, f"{len(found)} page markers, gapless 1-{expected_pages}"


def check_image_refs(markdown, output_dir):
    refs = IMAGE_REF_RE.findall(markdown)
    if not refs:
        return True, "no image references (document has no images)"
    broken = [r for r in refs if not (output_dir / r).exists()]
    if broken:
        return False, f"{len(broken)} of {len(refs)} references do not resolve: {broken[:3]}"
    return True, f"{len(refs)} image references, all resolve"


def check_no_stray_images(output_dir):
    """Images belong in images/, never loose beside the PDF."""
    stray = [
        p.name
        for p in output_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    ]
    if stray:
        return False, f"{len(stray)} image files loose beside the PDF: {stray[:3]}"
    return True, "no stray image files beside the PDF"


def check_image_table(markdown, image_dir):
    has_images = image_dir.exists() and any(image_dir.iterdir())
    has_table = "## Extracted Images" in markdown
    if has_images and not has_table:
        return False, "images were extracted but the summary table is missing"
    if has_table and not has_images:
        return False, "summary table present but no images on disk"
    return True, ("summary table present" if has_table else "no images, no table")


def check_cache(cold_body, warm_body, warm_stderr):
    if "from cache" not in warm_stderr:
        return False, "second run did not report a cache hit"
    if cold_body != warm_body:
        return False, "cached output differs from the cold run"
    return True, "second run hit the cache with identical content"


def verify_mode(pdf_source, label, extra_args):
    """Run one mode end to end in its own directory. Returns True if all checks pass."""
    print(f"\n{label}", flush=True)

    with tempfile.TemporaryDirectory(prefix="pdf_verify_") as workspace:
        output_dir = Path(workspace)
        pdf_path = output_dir / Path(pdf_source).name
        shutil.copy2(pdf_source, pdf_path)
        markdown_path = pdf_path.with_suffix(".md")

        from extractor import get_page_count

        expected_pages = get_page_count(str(pdf_path))

        # The conversion's own progress goes to stderr, which run_cli captures
        # so it can be reported on failure. Say what is happening first -
        # otherwise a docling run that is downloading half a gigabyte of models
        # looks indistinguishable from a hang.
        notice = f"  ... converting {expected_pages} pages"
        if "--docling" in extra_args:
            notice += " (~1s/page; a first docling run also fetches ~500MB of models)"
        elif "--ocr" in extra_args:
            notice += " (OCR is several times slower than the default)"
        print(notice, flush=True)

        # --clear-cache drops any cache entry and then converts, so this one
        # run is the cold run - no need to pay for extraction twice.
        cold, elapsed = run_cli(pdf_path, ["--clear-cache", *extra_args])
        if cold.returncode != 0 or not markdown_path.exists():
            print(f"  [FAIL] run failed (exit {cold.returncode})")
            for line in diagnostic_lines(cold.stderr):
                print(f"         {line}")
            return False

        cold_markdown = markdown_path.read_text(encoding="utf-8")
        results = [
            (True, f"run completed in {elapsed:.1f}s, {expected_pages} pages"),
            check_markers(cold_markdown, expected_pages),
            check_image_refs(cold_markdown, output_dir),
            check_no_stray_images(output_dir),
            check_image_table(cold_markdown, output_dir / "images"),
        ]

        warm, _ = run_cli(pdf_path, extra_args)
        warm_markdown = markdown_path.read_text(encoding="utf-8")
        results.append(
            check_cache(body_of(cold_markdown), body_of(warm_markdown), warm.stderr or "")
        )

        for ok, message in results:
            print(f"  [{'ok' if ok else 'FAIL'}] {message}")

        for line in (cold.stderr or "").splitlines():
            if line.startswith("WARNING:"):
                print(f"  [note] {line.rstrip()}")

        return all(ok for ok, _ in results)


def main():
    parser = argparse.ArgumentParser(
        description="Verify the pdf-to-markdown skill against a PDF."
    )
    parser.add_argument("pdf", help="PDF to run the checks against")
    parser.add_argument(
        "--skip-docling",
        action="store_true",
        help="Do not check --docling mode even when docling is installed",
    )
    parser.add_argument(
        "--ocr", action="store_true", help="Also check the --ocr variant of fast mode"
    )
    args = parser.parse_args()

    pdf_source = Path(args.pdf).expanduser().resolve()
    if not pdf_source.is_file():
        print(f"ERROR: no such file: {pdf_source}", file=sys.stderr)
        return 2

    print("Environment")
    for line in environment_report():
        print(line)

    modes = [("fast mode", [])]
    if args.ocr:
        modes.append(("fast mode, --ocr", ["--ocr"]))
    if args.skip_docling:
        pass
    elif docling_installed():
        modes.append(("docling mode", ["--docling"]))
    else:
        print("\ndocling mode\n  [skip] docling is not installed in this environment")

    passed = [verify_mode(pdf_source, label, extra) for label, extra in modes]

    print()
    if all(passed):
        print(f"All checks passed ({len(passed)} mode(s)).")
        return 0
    print(f"{passed.count(False)} of {len(passed)} mode(s) failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
