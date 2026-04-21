"""
PDF renderer for resumasher.

Parses a minimal resume/cover-letter/interview-prep markdown schema into
ATS-safe single-column PDFs using reportlab. Pure Python, no native deps.

CLI:
    python scripts/render_pdf.py --input resume.md --style eu --output out.pdf
    python scripts/render_pdf.py --input resume.md --style us --output out.pdf --photo me.jpg
    python scripts/render_pdf.py --input cover.md --kind cover-letter --output cover.pdf
    python scripts/render_pdf.py --input prep.md --kind interview-prep --output prep.pdf

Resume markdown schema (intentionally minimal, headings are semantic):

    # {Full Name}
    {email} | {phone} | {linkedin-url} | {location}

    ## Summary
    {one paragraph}

    ## Experience
    ### {Title} — {Company} ({dates})
    - bullet
    - bullet

    ## Education
    ### {Degree} — {Institution} ({dates})
    - bullet

    ## Skills
    - Category: item, item, item

    ## Projects
    ### {Project name} ({path})
    - bullet

Cover letter schema: H1 = recipient/greeting; paragraphs separated by blank lines.
Interview prep schema: H2 sections (e.g., "SQL", "Case Study", "Behavioral STAR"),
H3 subsections for individual questions, paragraphs for answers.

Design choices (traced to the eng review):
- reportlab Platypus (Flowables + SimpleDocTemplate) gives reflow-aware layout
  with selectable text. No image-based rendering.
- DejaVu Sans bundled as the default font. Covers Latin Extended + Cyrillic +
  Greek + common symbols. Prevents the "box character" failure for international
  names like Jiří, Björn, François, Muñoz and emoji 🐍.
- Single column, block layout only. No floats, no tables-for-layout, no absolute
  positioning. This is what ATS parsers expect.
- US style suppresses the photo even if one is provided (enforced here, not in
  the orchestrator, so the rule holds regardless of caller).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle, StyleSheet1
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image,
    KeepTogether,
    PageBreak,
)

# Pillow is a transitive dependency of reportlab 4.x, so importing it here
# adds no new requirement. We use it to downscale oversized student photos
# before embedding — a 3000x4000 headshot at source resolution inflates the
# PDF by 1MB+ for no visible benefit at a 3cm print size.
from io import BytesIO
from PIL import Image as PILImage

# ---------------------------------------------------------------------------
# Font registration
# ---------------------------------------------------------------------------

# Find DejaVu Sans: prefer the bundled asset shipped with the skill; fall back
# to the system copy if the repo was cloned without the asset. If neither
# exists, reportlab will use Helvetica, which lacks broad Unicode coverage
# and will render boxes for non-Latin characters. We warn loudly in that case.

_ROOT = Path(__file__).resolve().parent.parent
_FONT_REG_NAME = "ResumasherSans"
_FONT_BOLD_NAME = "ResumasherSans-Bold"
_FONT_REGISTERED = False


def _register_fonts() -> tuple[str, str]:
    """Register DejaVu Sans under stable names. Returns (regular, bold) names."""
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return _FONT_REG_NAME, _FONT_BOLD_NAME

    candidates_regular = [
        _ROOT / "assets" / "DejaVuSans.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/Library/Fonts/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/DejaVuSans.ttf"),
    ]
    candidates_bold = [
        _ROOT / "assets" / "DejaVuSans-Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/Library/Fonts/DejaVuSans-Bold.ttf"),
        Path("/System/Library/Fonts/DejaVuSans-Bold.ttf"),
    ]

    regular = next((p for p in candidates_regular if p.exists()), None)
    bold = next((p for p in candidates_bold if p.exists()), None)

    if regular is None:
        # Fall back to Helvetica but warn loudly on stderr so the caller knows
        # non-ASCII content may render badly.
        print(
            "WARNING: DejaVuSans.ttf not found; falling back to Helvetica. "
            "Non-ASCII characters (é, ñ, ř, emoji) may render as boxes. "
            "Bundle assets/DejaVuSans.ttf with the skill to fix.",
            file=sys.stderr,
        )
        _FONT_REGISTERED = True
        return "Helvetica", "Helvetica-Bold"

    pdfmetrics.registerFont(TTFont(_FONT_REG_NAME, str(regular)))
    if bold is not None:
        pdfmetrics.registerFont(TTFont(_FONT_BOLD_NAME, str(bold)))
    else:
        # Register regular under bold name too so <b> doesn't crash.
        pdfmetrics.registerFont(TTFont(_FONT_BOLD_NAME, str(regular)))

    # Map bold/italic so Paragraph <b>...</b> resolves correctly.
    from reportlab.pdfbase.pdfmetrics import registerFontFamily
    registerFontFamily(
        _FONT_REG_NAME,
        normal=_FONT_REG_NAME,
        bold=_FONT_BOLD_NAME,
        italic=_FONT_REG_NAME,
        boldItalic=_FONT_BOLD_NAME,
    )
    _FONT_REGISTERED = True
    return _FONT_REG_NAME, _FONT_BOLD_NAME


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------


@dataclass
class ResumeSubBlock:
    """
    A sub-role inside a block, for multi-role tenures at one company.
    Example: under "### Meta (July 2017 – August 2025)", the bold line
    "**Senior Director, Data Science** (Aug 2022 – Aug 2025)" is a sub-block.
    """
    title: str  # e.g., "Senior Director, Data Science (Aug 2022 – Aug 2025)"
    bullets: list[str] = field(default_factory=list)


@dataclass
class ResumeBlock:
    title: str  # e.g., "Senior Analyst — Deloitte (2023–2024)"
    bullets: list[str] = field(default_factory=list)
    # Sub-roles for multi-title tenures at one company. When present, the
    # renderer emits them in order, each with its own title + bullets.
    # When empty, the block's `bullets` field is used directly.
    sub_blocks: list[ResumeSubBlock] = field(default_factory=list)


@dataclass
class ResumeSection:
    heading: str  # e.g., "Experience"
    blocks: list[ResumeBlock] = field(default_factory=list)
    # For Skills, bullets live on the section directly (no sub-block titles).
    raw_bullets: list[str] = field(default_factory=list)
    raw_paragraphs: list[str] = field(default_factory=list)


@dataclass
class ResumeDoc:
    name: str = ""
    contact_line: str = ""
    sections: list[ResumeSection] = field(default_factory=list)


class MissingContactHeaderError(ValueError):
    """
    Raised by the render functions when `parse_resume_markdown` returns a
    `ResumeDoc` with no candidate name. Happens when the tailor emits output
    that doesn't start with `# Name` — we've seen this when the LLM
    pipe-joins the name and contact info onto a single line-1 string with
    no `# ` prefix ("Candidate Name | +43 ... | email@example.com").

    Why this is fatal: an ATS parses the PDF and extracts the candidate
    name from the top of the document. Without `doc.name`, the rendered
    PDF has no name at all — the ATS cannot associate the application with
    a candidate, the submission silently filters out, and the student
    never hears back and assumes the role was competitive. That is the
    worst possible failure mode for a job-application tool.

    Renderer loudly refuses to produce such a PDF. The caller (SKILL.md
    Phase 8) catches this exception and surfaces an actionable message to
    the student, including the `first_line` attribute so they can see
    what the tailor actually emitted vs what the parser expected.

    Tracked via `docs/KNOWN_FAILURE_MODES.md` #1 and issue #18.
    """

    def __init__(self, first_line: str) -> None:
        self.first_line = first_line
        super().__init__(
            "Resume markdown is missing the candidate name header. "
            f"Expected `# Your Name` on line 1, got: {first_line[:100]!r}"
        )


_SUB_BLOCK_RE = re.compile(r"^\*\*(.+?)\*\*\s*(.*)$")


def parse_resume_markdown(text: str) -> ResumeDoc:
    """
    Parse the resume markdown schema documented at module top.

    The parser is intentionally lenient: it accepts whatever H1/H2/H3 shape
    the upstream tailor emits, and it tolerates missing sections, extra
    blank lines, and bullets-as-asterisks or bullets-as-dashes.

    Multi-role tenures: inside a block, a line starting with `**Title**`
    followed by optional metadata (e.g., `**Director** (2020–2022, Zurich)`)
    opens a new sub-block. Subsequent bullets belong to that sub-block
    until the next `**Title**` line or the next `### Block`. This
    preserves the career-progression narrative when one company had
    multiple role titles over a tenure.
    """
    doc = ResumeDoc()
    lines = text.splitlines()

    current_section: Optional[ResumeSection] = None
    current_block: Optional[ResumeBlock] = None
    current_sub: Optional[ResumeSubBlock] = None

    for raw_line in lines:
        line = raw_line.rstrip()

        if not line.strip():
            continue

        if line.startswith("# ") and not doc.name:
            doc.name = line[2:].strip()
            continue

        if line.startswith("## "):
            current_section = ResumeSection(heading=line[3:].strip())
            doc.sections.append(current_section)
            current_block = None
            current_sub = None
            continue

        if line.startswith("### "):
            if current_section is None:
                current_section = ResumeSection(heading="Experience")
                doc.sections.append(current_section)
            current_block = ResumeBlock(title=line[4:].strip())
            current_section.blocks.append(current_block)
            current_sub = None
            continue

        # Sub-block title: **Title** followed by optional metadata (dates, location).
        # Only matches when the FULL line is the bold phrase + optional trailing text,
        # AND we're currently inside a block. Otherwise a bold phrase inside a bullet
        # or paragraph would hijack the parser.
        if current_block is not None:
            sub_match = _SUB_BLOCK_RE.match(line)
            if sub_match:
                title_core = sub_match.group(1).strip()
                trailing = sub_match.group(2).strip()
                sub_title = f"{title_core} {trailing}".strip() if trailing else title_core
                current_sub = ResumeSubBlock(title=sub_title)
                current_block.sub_blocks.append(current_sub)
                continue

        bullet_match = re.match(r"^\s*[-*]\s+(.*)$", line)
        if bullet_match:
            bullet_text = bullet_match.group(1).strip()
            # Bullets go to the most-specific container: sub-block if one is open,
            # else the current block, else the section-level raw_bullets.
            if current_sub is not None:
                current_sub.bullets.append(bullet_text)
            elif current_block is not None:
                current_block.bullets.append(bullet_text)
            elif current_section is not None:
                current_section.raw_bullets.append(bullet_text)
            continue

        if current_section is None and not doc.contact_line and doc.name:
            doc.contact_line = line.strip()
            continue

        if current_section is not None:
            current_section.raw_paragraphs.append(line.strip())

    return doc


# ---------------------------------------------------------------------------
# Style sheet
# ---------------------------------------------------------------------------


def _build_styles() -> StyleSheet1:
    regular, bold = _register_fonts()
    ss = StyleSheet1()
    ss.add(ParagraphStyle(
        name="Name",
        fontName=bold,
        fontSize=20,
        leading=24,
        alignment=TA_LEFT,
        spaceAfter=2,
    ))
    ss.add(ParagraphStyle(
        name="Contact",
        fontName=regular,
        fontSize=10,
        leading=13,
        alignment=TA_LEFT,
        spaceAfter=10,
    ))
    ss.add(ParagraphStyle(
        name="SectionHeading",
        fontName=bold,
        fontSize=12,
        leading=15,
        alignment=TA_LEFT,
        spaceBefore=10,
        spaceAfter=4,
        textColor="#111111",
    ))
    ss.add(ParagraphStyle(
        name="BlockTitle",
        fontName=bold,
        fontSize=11,
        leading=14,
        alignment=TA_LEFT,
        spaceBefore=4,
        spaceAfter=2,
    ))
    # Sub-block titles (multi-role tenures inside one company). Same face as
    # BlockTitle but slightly smaller and indented so the visual hierarchy
    # Company > Role is obvious.
    ss.add(ParagraphStyle(
        name="SubBlockTitle",
        fontName=bold,
        fontSize=10.5,
        leading=13,
        alignment=TA_LEFT,
        leftIndent=8,
        spaceBefore=3,
        spaceAfter=1,
    ))
    ss.add(ParagraphStyle(
        name="Bullet",
        fontName=regular,
        fontSize=10,
        leading=13,
        alignment=TA_LEFT,
        leftIndent=14,
        bulletIndent=4,
        spaceAfter=1,
    ))
    ss.add(ParagraphStyle(
        name="Body",
        fontName=regular,
        fontSize=10.5,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=8,
    ))
    ss.add(ParagraphStyle(
        name="SubheadCenter",
        fontName=regular,
        fontSize=10,
        leading=13,
        alignment=TA_CENTER,
        spaceAfter=10,
    ))
    ss.add(ParagraphStyle(
        name="NameCenter",
        fontName=bold,
        fontSize=20,
        leading=24,
        alignment=TA_CENTER,
        spaceAfter=2,
    ))
    return ss


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _section_order_eu(doc: ResumeDoc) -> list[ResumeSection]:
    """EU style: Summary, Experience, Education, Skills, Projects, then others."""
    return _ordered_sections(doc, ["Summary", "Experience", "Education", "Skills", "Projects"])


def _section_order_us(doc: ResumeDoc) -> list[ResumeSection]:
    """US style: Summary, Experience, Projects, Skills, Education, then others.

    The reorder is conventional — US resumes lead with experience and projects,
    put education last unless the candidate is a new grad.
    """
    return _ordered_sections(doc, ["Summary", "Experience", "Projects", "Skills", "Education"])


def _ordered_sections(doc: ResumeDoc, preferred: list[str]) -> list[ResumeSection]:
    by_heading = {s.heading: s for s in doc.sections}
    ordered: list[ResumeSection] = []
    seen: set[str] = set()
    for heading in preferred:
        if heading in by_heading:
            ordered.append(by_heading[heading])
            seen.add(heading)
    for section in doc.sections:
        if section.heading not in seen:
            ordered.append(section)
            seen.add(section.heading)
    return ordered


def _build_resume_flowables(
    doc: ResumeDoc,
    styles: StyleSheet1,
    section_order_fn,
    center_header: bool,
    photo_path: Optional[str],
) -> list:
    flow: list = []
    name_style = styles["NameCenter"] if center_header else styles["Name"]
    contact_style = styles["SubheadCenter"] if center_header else styles["Contact"]

    if photo_path:
        try:
            # Small photo, ~3cm wide, inline above the name. EU conventional.
            # Downscale oversized images (typical phone/camera export) before
            # embedding so the output PDF stays small enough to email + upload
            # to ATS without hitting size caps.
            photo_source = _downscale_photo_for_embed(photo_path)
            img = Image(photo_source, width=3 * cm, height=3 * cm)
            flow.append(img)
            flow.append(Spacer(1, 4))
        except Exception as e:
            print(
                f"WARNING: could not embed photo at {photo_path}: {e}",
                file=sys.stderr,
            )

    if doc.name:
        flow.append(Paragraph(_escape(doc.name), name_style))
    if doc.contact_line:
        flow.append(Paragraph(_escape(doc.contact_line), contact_style))

    for section in section_order_fn(doc):
        flow.append(Paragraph(_escape(section.heading), styles["SectionHeading"]))
        # Paragraphs directly under the section (summary body).
        for para in section.raw_paragraphs:
            flow.append(Paragraph(_escape(para), styles["Body"]))
        # Bare bullets (common for Skills).
        for bullet in section.raw_bullets:
            flow.append(Paragraph(_escape(bullet), styles["Bullet"], bulletText="•"))
        # Blocks (Experience, Education, Projects).
        for block in section.blocks:
            group: list = [Paragraph(_escape(block.title), styles["BlockTitle"])]
            # Direct bullets (single-role blocks).
            for bullet in block.bullets:
                group.append(Paragraph(_escape(bullet), styles["Bullet"], bulletText="•"))
            # Sub-blocks for multi-role tenures. Each sub-block title stays
            # glued to its own bullets via the group list, so KeepTogether
            # prevents a page break from splitting role-title from its bullets.
            for sub in block.sub_blocks:
                group.append(Paragraph(_escape(sub.title), styles["SubBlockTitle"]))
                for bullet in sub.bullets:
                    group.append(Paragraph(_escape(bullet), styles["Bullet"], bulletText="•"))
            flow.append(KeepTogether(group))

    return flow


_MARKDOWN_BOLD_RE = re.compile(r"\*\*([^\n*][^\n]*?)\*\*")


# Max dimension for embedded photos. The photo prints at 3cm × 3cm; at 300 DPI
# that's ~354px. 500px is generous, keeps the embedded image sharp on zoom
# without bloating the PDF. A typical 3000×4000 iPhone photo at source adds
# ~1MB to the PDF; downscaled to 500×500 it adds ~50-80KB.
_PHOTO_MAX_DIM = 500


def _downscale_photo_for_embed(path: str) -> object:
    """
    Open `path` with Pillow, downscale to fit within _PHOTO_MAX_DIM, return
    a BytesIO of JPEG bytes suitable for reportlab's Image flowable.

    JPEG (not PNG) because:
    - Student headshots are photographic content. JPEG at q=85 is visually
      indistinguishable from lossless at 3cm print size.
    - JPEG compresses photographic content 5-10x better than PNG. A 500x500
      real-photo PNG is ~200KB; the same as JPEG is ~30-50KB.
    - That ratio is what turned a 1MB bloated PDF into a sub-150KB one for
      the Keensight run that motivated this fix.

    If Pillow can't open the file (unsupported format, corrupted), fall back
    to returning the original path — reportlab will either handle it or fail
    with its own error message.
    """
    try:
        img = PILImage.open(path)
        # Convert to RGB. JPEG doesn't support alpha, and PIL's JPEG encoder
        # rejects 'P' (palette) and 'RGBA' modes outright.
        if img.mode != "RGB":
            img = img.convert("RGB")
        # Only downscale; never upscale a small image.
        if max(img.size) > _PHOTO_MAX_DIM:
            img.thumbnail((_PHOTO_MAX_DIM, _PHOTO_MAX_DIM), PILImage.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        buf.seek(0)
        return buf
    except Exception as exc:
        print(
            f"NOTE: could not downscale {path} ({exc}); embedding at source "
            f"resolution. PDF may be larger than expected.",
            file=sys.stderr,
        )
        return path


def _escape(text: str) -> str:
    """Escape the small HTML-ish subset reportlab Paragraph interprets, and
    convert markdown inline formatting to reportlab's HTML-like tags.

    reportlab Paragraph accepts an HTML-like subset for <b>, <i>, etc. Any
    literal &, <, > in user content must be escaped or the parser throws.

    After escaping, we translate `**bold**` → `<b>bold</b>` so markdown
    emphasis from upstream sub-agents (common in interview-prep and cover
    letters) renders as bold, not as literal asterisks. Italic (`*x*`) is
    intentionally skipped — too easy to false-match on single asterisks in
    prose (e.g., "footnote*").

    The regex requires at least one non-asterisk, non-newline character
    inside the pair, and forbids the pair from spanning a line break. This
    keeps "*" alone, "a ** b", and multi-line content safe.
    """
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return _MARKDOWN_BOLD_RE.sub(r"<b>\1</b>", escaped)


# ---------------------------------------------------------------------------
# Public render functions
# ---------------------------------------------------------------------------


def _first_non_empty_line(text: str) -> str:
    """Return the first non-empty line of `text`, or the empty string.

    Used only for diagnostic error messages — tells the student what the
    parser actually saw on line 1 when the expected `# Name` shape is
    missing. Kept as a small function so the behavior is unit-testable
    independent of the renderers.
    """
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _assert_contact_header_present(doc: ResumeDoc, source_markdown: str) -> None:
    """
    Loud gate before any PDF renders. If `doc.name` is empty, the tailored
    markdown is missing its `# Name` H1 and the rendered PDF would ship with
    no candidate identification — the worst possible ATS outcome. Raise
    rather than silently produce a subtly-wrong PDF the student might
    submit without noticing.

    See `MissingContactHeaderError` docstring for the rationale.
    """
    if not doc.name:
        raise MissingContactHeaderError(_first_non_empty_line(source_markdown))


def render_resume_eu(
    source_markdown: str,
    output_path: str | Path,
    photo: Optional[str] = None,
) -> Path:
    """EU-style single-column resume. Photo optional (DACH convention)."""
    styles = _build_styles()
    doc = parse_resume_markdown(source_markdown)
    _assert_contact_header_present(doc, source_markdown)
    flow = _build_resume_flowables(
        doc,
        styles,
        section_order_fn=_section_order_eu,
        center_header=False,
        photo_path=photo,
    )
    return _write_pdf(flow, output_path, pagesize=A4)


def render_resume_us(
    source_markdown: str,
    output_path: str | Path,
    photo: Optional[str] = None,  # accepted for API symmetry; always suppressed
) -> Path:
    """US-style single-column resume. Photo is always suppressed regardless of input.

    The suppression is enforced here, not in the caller, so the invariant
    holds even if someone wires the orchestrator wrong.
    """
    if photo is not None:
        print(
            "NOTE: US-style resume suppresses photo even when one is provided.",
            file=sys.stderr,
        )
    styles = _build_styles()
    doc = parse_resume_markdown(source_markdown)
    _assert_contact_header_present(doc, source_markdown)
    flow = _build_resume_flowables(
        doc,
        styles,
        section_order_fn=_section_order_us,
        center_header=True,
        photo_path=None,  # enforced
    )
    return _write_pdf(flow, output_path, pagesize=LETTER)


def render_cover_letter(source_markdown: str, output_path: str | Path) -> Path:
    """Cover letter: one-page letter. H1 is greeting line; paragraphs are blocks."""
    styles = _build_styles()
    flow: list = []
    current_para: list[str] = []
    greeting_done = False

    for raw_line in source_markdown.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            if current_para:
                flow.append(Paragraph(_escape(" ".join(current_para)), styles["Body"]))
                current_para = []
            continue
        if line.startswith("# "):
            # Flush anything pending, then emit the greeting as an H1.
            if current_para:
                flow.append(Paragraph(_escape(" ".join(current_para)), styles["Body"]))
                current_para = []
            flow.append(Paragraph(_escape(line[2:].strip()), styles["BlockTitle"]))
            flow.append(Spacer(1, 6))
            greeting_done = True
            continue
        current_para.append(line.strip())

    if current_para:
        flow.append(Paragraph(_escape(" ".join(current_para)), styles["Body"]))

    return _write_pdf(flow, output_path, pagesize=A4)


def render_interview_prep(source_markdown: str, output_path: str | Path) -> Path:
    """Interview prep: H2 = category (SQL / Case / Behavioral), H3 = question."""
    styles = _build_styles()
    flow: list = []
    current_para: list[str] = []

    for raw_line in source_markdown.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            if current_para:
                flow.append(Paragraph(_escape(" ".join(current_para)), styles["Body"]))
                current_para = []
            continue

        if line.startswith("# "):
            if current_para:
                flow.append(Paragraph(_escape(" ".join(current_para)), styles["Body"]))
                current_para = []
            flow.append(Paragraph(_escape(line[2:].strip()), styles["Name"]))
            continue

        if line.startswith("## "):
            if current_para:
                flow.append(Paragraph(_escape(" ".join(current_para)), styles["Body"]))
                current_para = []
            flow.append(Paragraph(_escape(line[3:].strip()), styles["SectionHeading"]))
            continue

        if line.startswith("### "):
            if current_para:
                flow.append(Paragraph(_escape(" ".join(current_para)), styles["Body"]))
                current_para = []
            flow.append(Paragraph(_escape(line[4:].strip()), styles["BlockTitle"]))
            continue

        bullet_match = re.match(r"^\s*[-*]\s+(.*)$", line)
        if bullet_match:
            if current_para:
                flow.append(Paragraph(_escape(" ".join(current_para)), styles["Body"]))
                current_para = []
            flow.append(Paragraph(
                _escape(bullet_match.group(1).strip()),
                styles["Bullet"],
                bulletText="•",
            ))
            continue

        current_para.append(line.strip())

    if current_para:
        flow.append(Paragraph(_escape(" ".join(current_para)), styles["Body"]))

    return _write_pdf(flow, output_path, pagesize=A4)


def _write_pdf(flow: list, output_path: str | Path, pagesize) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pdf_doc = SimpleDocTemplate(
        str(output_path),
        pagesize=pagesize,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title="Resume",
        author="resumasher",
    )
    pdf_doc.build(flow)
    return output_path


# ---------------------------------------------------------------------------
# ATS verification helper (pdfminer round-trip)
# ---------------------------------------------------------------------------


def assert_ats_roundtrip(pdf_path: str | Path, expected_substrings: list[str]) -> None:
    """
    Extract text from pdf_path and assert every expected substring is present.

    Raises AssertionError listing any missing strings. Used by tests and by
    the dev-loop manual ATS sanity check.
    """
    from pdfminer.high_level import extract_text

    extracted = extract_text(str(pdf_path)) or ""
    # Normalize whitespace: pdfminer often inserts line breaks inside words.
    normalized = re.sub(r"\s+", " ", extracted)

    missing: list[str] = []
    for needle in expected_substrings:
        needle_norm = re.sub(r"\s+", " ", needle).strip()
        if not needle_norm:
            continue
        if needle_norm not in normalized:
            missing.append(needle_norm)

    if missing:
        raise AssertionError(
            "ATS round-trip failed. Missing substrings:\n  "
            + "\n  ".join(f"- {m}" for m in missing)
            + f"\n\nExtracted text (first 800 chars):\n{normalized[:800]}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render markdown to ATS-safe PDF.")
    parser.add_argument("--input", required=True, help="Path to markdown input")
    parser.add_argument(
        "--kind",
        choices=["resume", "cover-letter", "interview-prep"],
        default="resume",
        help="Document kind (default: resume)",
    )
    parser.add_argument(
        "--style",
        choices=["eu", "us"],
        default="eu",
        help="Resume style (only applies when --kind=resume)",
    )
    parser.add_argument("--output", required=True, help="Output PDF path")
    parser.add_argument("--photo", default=None, help="Optional photo path (EU resume only)")
    args = parser.parse_args(argv)

    try:
        source = Path(args.input).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(
            f"ERROR: could not read {args.input} as UTF-8. "
            f"If this file came from Windows Notepad, resave it as UTF-8.",
            file=sys.stderr,
        )
        return 2

    try:
        if args.kind == "resume":
            if args.style == "eu":
                render_resume_eu(source, args.output, photo=args.photo)
            else:
                render_resume_us(source, args.output, photo=args.photo)
        elif args.kind == "cover-letter":
            render_cover_letter(source, args.output)
        elif args.kind == "interview-prep":
            render_interview_prep(source, args.output)
    except MissingContactHeaderError as exc:
        print(
            f"ERROR: {args.input} is missing the candidate name header.\n"
            f"\n"
            f"Expected on line 1:\n"
            f"  # Your Name\n"
            f"  email | phone | linkedin | location\n"
            f"\n"
            f"Got:\n"
            f"  {exc.first_line[:120]}\n"
            f"\n"
            f"The tailored markdown ships without a name header, which would\n"
            f"produce a PDF with no candidate identification — an ATS cannot\n"
            f"associate the application with you. Refusing to render.\n"
            f"\n"
            f"This is almost certainly a tailor prompt failure, not something\n"
            f"you did wrong. Re-run /resumasher to regenerate the tailored\n"
            f"markdown. Tracked: KNOWN_FAILURE_MODES.md #1 / issue #18.",
            file=sys.stderr,
        )
        return 2

    print(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
