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
from reportlab.platypus.flowables import HRFlowable
from reportlab.lib.colors import HexColor

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
    # Path to the candidate's photo, extracted from an HTML comment in the
    # markdown header: `<!-- photo: /path/to/photo.jpg -->`. Populated by
    # `parse_resume_markdown` when the comment is present. Used by the
    # renderer as the photo source when no explicit `--photo` flag was
    # passed (see render_resume_eu / render_resume_us). Enables re-render
    # after markdown edits without external config state. Tracked: #20.
    photo_path: str = ""


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


# Sentinel used in `ResumeSection.raw_paragraphs` to mark a horizontal rule
# (markdown `---` / `___` / `***` on its own line). The renderer detects this
# exact string and emits an `HRFlowable` instead of a text paragraph. Null
# bytes are safe — they never appear in parsed markdown content. Tracked: #22.
_HR_SENTINEL = "\x00HR\x00"

# Markdown horizontal rule: `---`, `___`, or `***` on its own line, optionally
# with surrounding whitespace. Full-line match only (same design as
# `_SUB_BLOCK_RE` and `_PHOTO_COMMENT_RE`) — inline occurrences in prose
# don't trigger.
_HR_RE = re.compile(r"^\s*(?:-{3,}|_{3,}|\*{3,})\s*$")

_SUB_BLOCK_RE = re.compile(r"^\*\*(.+?)\*\*\s*(.*)$")

# `<!-- photo: /path/to/photo.jpg -->` — HTML comment the tailor emits at the
# top of the markdown when a photo is provided. The path can contain spaces,
# slashes, and extension characters. Whitespace around the path is allowed
# (and stripped) so the tailor doesn't have to be pixel-perfect. Only the
# full-line form matches — inline HTML comments in prose don't trigger.
_PHOTO_COMMENT_RE = re.compile(r"^\s*<!--\s*photo\s*:\s*(.+?)\s*-->\s*$")


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
    # True when current_block was created from a `**Title**` line at the
    # section level (not from a `### Company` heading). Used to decide
    # whether the NEXT `**Title**` should be a sub-block of the current
    # block (real `###` parent) or a sibling synthetic block (no `###`
    # parent — the issue #19 shape). Cleared on every `##` or `###`.
    synthetic_block_active = False

    for raw_line in lines:
        line = raw_line.rstrip()

        if not line.strip():
            continue

        if line.startswith("# ") and not doc.name:
            doc.name = line[2:].strip()
            continue

        # `<!-- photo: /path -->` HTML comment — captured once (first
        # occurrence wins; duplicates are probably copy-paste bugs).
        # Stored verbatim as the student typed it (absolute or relative);
        # the renderer resolves relative paths when embedding. See #20.
        if not doc.photo_path:
            photo_match = _PHOTO_COMMENT_RE.match(line)
            if photo_match:
                doc.photo_path = photo_match.group(1).strip()
                continue

        # Horizontal rule (`---` / `___` / `***` on its own line). Emit
        # the sentinel into the current section's paragraphs so the
        # renderer can convert it to an HRFlowable. Rules appearing
        # before any `##` section are dropped (no home for them). See #22.
        if _HR_RE.match(line):
            if current_section is not None:
                current_section.raw_paragraphs.append(_HR_SENTINEL)
            continue

        if line.startswith("## "):
            current_section = ResumeSection(heading=line[3:].strip())
            doc.sections.append(current_section)
            current_block = None
            current_sub = None
            synthetic_block_active = False
            continue

        if line.startswith("### "):
            if current_section is None:
                current_section = ResumeSection(heading="Experience")
                doc.sections.append(current_section)
            current_block = ResumeBlock(title=line[4:].strip())
            current_section.blocks.append(current_block)
            current_sub = None
            synthetic_block_active = False
            continue

        # `**Title**` with optional trailing metadata. Full-line match only,
        # so bold inline in prose ("a **really** cool thing") never matches.
        # Three interpretations depending on current parser state:
        #
        #  (a) Inside a real `### Company` block (current_block is set AND
        #      synthetic_block_active is False): open a sub-block for a
        #      multi-role tenure (Senior Director under a Company heading).
        #
        #  (b) At section level with no `###` above, AND trailing metadata
        #      is non-empty: open a synthetic block. This handles the
        #      issue #19 shape where the tailor emits `**Project Name** |
        #      Feb 2026 | Context` directly under `##` without the `###`
        #      wrapper. The trailing-metadata check is load-bearing —
        #      it rejects bold-only prose like "**Accomplished leader.**"
        #      in a Summary section, which should stay as a paragraph.
        #
        #  (c) Already inside a synthetic block (synthetic_block_active
        #      is True): open ANOTHER synthetic block as a sibling,
        #      because consecutive `**Project A** ... **Project B**` at
        #      section level are siblings, not parent-child.
        #
        # Bold lines without trailing metadata at section level fall
        # through to raw_paragraphs (existing behavior).
        sub_match = _SUB_BLOCK_RE.match(line)
        if sub_match and current_section is not None:
            title_core = sub_match.group(1).strip()
            trailing = sub_match.group(2).strip()
            bolded_title = f"{title_core} {trailing}".strip() if trailing else title_core

            if current_block is not None and not synthetic_block_active:
                # Shape (a): sub-role under a real `### Company` block.
                current_sub = ResumeSubBlock(title=bolded_title)
                current_block.sub_blocks.append(current_sub)
                continue

            if trailing:
                # Shape (b) or (c): synthetic block, either first one in
                # the section or a sibling of a previous synthetic.
                current_block = ResumeBlock(title=bolded_title)
                current_section.blocks.append(current_block)
                current_sub = None
                synthetic_block_active = True
                continue
            # else: bold line with no trailing metadata at section level
            # with no `###` parent. Fall through to raw_paragraphs so
            # bold-only prose in a Summary doesn't get hijacked as a
            # project heading.

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
        # Softer & smaller than body so the contact line recedes into
        # header territory rather than competing with bullets for
        # hierarchy attention. 9.5pt + #555555 matches BlockMetadata.
        fontSize=9.5,
        leading=12,
        alignment=TA_LEFT,
        spaceAfter=10,
        textColor="#555555",
    ))
    ss.add(ParagraphStyle(
        name="SectionHeading",
        fontName=bold,
        fontSize=12,
        leading=15,
        alignment=TA_LEFT,
        # spaceBefore bumped 10 → 14 alongside #42's stacked-date layout:
        # blocks gained a metadata line, so the heading-to-content rhythm
        # would otherwise feel proportionally compressed. Sections now read
        # as visually separate.
        spaceBefore=14,
        spaceAfter=4,
        textColor="#111111",
    ))
    ss.add(ParagraphStyle(
        name="BlockTitle",
        fontName=bold,
        fontSize=11,
        leading=14,
        alignment=TA_LEFT,
        # spaceBefore bumped 4 → 6 so consecutive blocks within a section
        # (Meta → Chief Data Scientist → Volunteer Translator) don't sit
        # tight against each other. Pure layout — no content change.
        spaceBefore=6,
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
    # Stacked-date metadata line that sits under a block or sub-block title
    # when the title contains a recognizable date segment. Slightly smaller
    # and regular-weight (vs bold title) creates real typographic hierarchy
    # without the ATS risk of 2-column / Table layouts. See issue #42.
    ss.add(ParagraphStyle(
        name="BlockMetadata",
        fontName=regular,
        fontSize=9.5,
        leading=12,
        alignment=TA_LEFT,
        spaceAfter=2,
        # Soft gray so the date/location line recedes as metadata. Works
        # alongside size + position to form a real 3-tier hierarchy
        # (bold title → soft metadata → regular bullets). The reason
        # color-as-hierarchy failed in PR #30 was that gray was the only
        # gesture; here it complements size and position.
        textColor="#555555",
    ))
    ss.add(ParagraphStyle(
        name="SubBlockMetadata",
        fontName=regular,
        fontSize=9.5,
        leading=12,
        alignment=TA_LEFT,
        leftIndent=8,
        spaceAfter=2,
        textColor="#555555",
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
    # Bullets that live under a sub-block title need to indent past the
    # sub-block's own indent (SubBlockTitle.leftIndent=8), or the bullet
    # character renders to the LEFT of the sub-block content above it —
    # which reads as broken hierarchy. Pre-#42 this latent bug was easy
    # to miss because the sub-block title-with-date occupied a single
    # dense line; #42's stacked-date layout puts the metadata line
    # immediately above the bullet, making the misalignment glaring.
    # leftIndent + 8 / bulletIndent + 8 keeps the bullet visibly indented
    # past its parent sub-block's content.
    ss.add(ParagraphStyle(
        name="SubBlockBullet",
        fontName=regular,
        fontSize=10,
        leading=13,
        alignment=TA_LEFT,
        leftIndent=22,
        bulletIndent=12,
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
        # Mirror the Contact style sizing/coloring (9.5pt, #555555) so
        # US-style center-aligned contact lines also recede into
        # header territory.
        fontSize=9.5,
        leading=12,
        alignment=TA_CENTER,
        spaceAfter=10,
        textColor="#555555",
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


_PHOTO_MAX_SIDE_CM = 3.0
_VALID_PHOTO_POSITIONS = ("left", "right", "center")


# Recognize a date segment inside a block/sub-block title. Heuristic: the
# date is whatever parenthesized expression at end-of-string, or pipe-
# separated segment, contains a 4-digit year (1900-2099). Bare years in
# prose like "2024 Economic Survey" don't match because they're not in
# parens or pipes. The rare false positive (e.g., "(founded 1995)" in a
# title) is acceptable — splitting it onto its own line is mildly weird,
# not destructive. See issue #42 for the full ATS rationale.
_YEAR_PATTERN = r"\b(?:19|20)\d{2}\b"
_PARENS_DATE_RE = re.compile(rf"\(([^()]*{_YEAR_PATTERN}[^()]*)\)\s*$")


def _split_title_and_date(title: str) -> tuple[str, Optional[str]]:
    """
    Try to extract a date segment from a block/sub-block title.

    Returns (title_without_date, date_string) when a date is detected,
    otherwise (original_title, None).

    Detection looks for either:
    - a trailing parenthesized expression containing a year:
      "Senior Analyst — Deloitte (Aug 2022 – Aug 2025)"
      "Project X (Feb 2026)"
      "Director (Aug 2022 – Present)"
    - a pipe-separated segment containing a year, in which case both
      surrounding pipes collapse to one:
      "Senior Analyst | 2022–2025 | Deloitte" → "Senior Analyst | Deloitte"

    Bare years in prose (not in parens/pipes) are NOT matched —
    "2024 Economic Survey" stays as the title.
    """
    m = _PARENS_DATE_RE.search(title)
    if m:
        date = m.group(1).strip()
        title_without = title[: m.start()].rstrip()
        return title_without, date

    if "|" in title:
        parts = [p.strip() for p in title.split("|")]
        for i, part in enumerate(parts):
            if re.search(_YEAR_PATTERN, part):
                date = part
                remaining = [p for j, p in enumerate(parts) if j != i and p]
                rebuilt = " | ".join(remaining)
                return rebuilt, date

    return title, None


def _render_titled_block(
    raw_title: str,
    title_style: ParagraphStyle,
    metadata_style: ParagraphStyle,
) -> list:
    """
    Render a block (or sub-block) title.

    If the raw title contains a recognizable date segment, the date moves
    to a separate metadata paragraph beneath the title. Otherwise the
    title renders as a single paragraph (current behavior — regression-
    guarded).

    Single-flow text only — no Tables, no tab stops. The metadata line is
    a real second paragraph that ATS parsers read in document order, which
    is what every commercial-ATS guidance recommends. See issue #42.
    """
    title_without_date, date = _split_title_and_date(raw_title)
    if date is None:
        # Run the title through _linkify_text so URLs in titles like
        # "Resumasher (github.com/earino/resumasher)" become clickable.
        # _linkify_text supersets _escape (HTML-escape + bold conversion +
        # link wrapping). Titles without URLs round-trip identically.
        return [Paragraph(_linkify_text(raw_title), title_style)]
    return [
        Paragraph(_linkify_text(title_without_date), title_style),
        # Date metadata is just a date — no URL expected. Still safe to
        # run through _linkify_text in case (yet ATS-irrelevant for dates).
        Paragraph(_escape(date), metadata_style),
    ]


def _section_divider() -> HRFlowable:
    """
    Thin horizontal rule that sits directly under a SectionHeading.

    Visual purpose: structural break between major sections (Summary /
    Experience / Education / Skills / Projects). Whitespace alone wasn't
    enough — the eye reads stacked sections as one continuous wall when
    the heading is just a 12pt bold line floating above content.

    Distinct from the in-content `---` markdown rule (`_HR_SENTINEL`,
    via `HRFlowable(thickness=0.5, color=#888888)`) which is a soft
    break inside a section. Section dividers are slightly thicker and
    darker so they read as structural rather than soft.

    No spaceBefore — the rule sits right below the heading text. The
    spaceAfter gives natural breathing room before the first block.
    """
    return HRFlowable(
        width="100%",
        thickness=0.75,
        color=HexColor("#333333"),
        spaceBefore=0,
        spaceAfter=6,
    )


def _photo_render_size_cm(photo_source) -> tuple[float, float]:
    """Compute embed width/height in cm, preserving source aspect ratio.

    Clamps the longer side to `_PHOTO_MAX_SIDE_CM`. A 3:4 portrait renders
    at ~2.25cm × 3cm; a 16:9 landscape at 3cm × 1.7cm; a square at 3cm × 3cm.

    Before this change (issue #22), the renderer hard-coded 3×3cm regardless
    of source aspect, which stretched portrait sources horizontally by up
    to ~33%. Students described it as "flattened" or "like a funeral hall
    portrait." See KNOWN_FAILURE_MODES.md #4.
    """
    from reportlab.lib.utils import ImageReader
    reader = ImageReader(photo_source)
    src_w, src_h = reader.getSize()
    if src_w <= 0 or src_h <= 0:
        # Defensive fallback — shouldn't happen for real images, but if
        # an image reports invalid dimensions, render at max × max rather
        # than crashing.
        return _PHOTO_MAX_SIDE_CM, _PHOTO_MAX_SIDE_CM
    if src_w >= src_h:
        return _PHOTO_MAX_SIDE_CM, _PHOTO_MAX_SIDE_CM * (src_h / src_w)
    return _PHOTO_MAX_SIDE_CM * (src_w / src_h), _PHOTO_MAX_SIDE_CM


def _build_resume_flowables(
    doc: ResumeDoc,
    styles: StyleSheet1,
    section_order_fn,
    center_header: bool,
    photo_path: Optional[str],
    photo_position: str = "right",
) -> list:
    flow: list = []
    name_style = styles["NameCenter"] if center_header else styles["Name"]
    contact_style = styles["SubheadCenter"] if center_header else styles["Contact"]

    if photo_path:
        try:
            # Small photo in the header, aspect-preserved, aligned per
            # `photo_position` (right / left / center). DACH convention is
            # top-right; French convention is top-left; centered is unusual
            # but supported. Downscale oversized source images before
            # embedding so the output PDF stays under the ATS-friendly size
            # cap (~200KB).
            photo_source = _downscale_photo_for_embed(photo_path)
            w_cm, h_cm = _photo_render_size_cm(photo_source)
            img = Image(photo_source, width=w_cm * cm, height=h_cm * cm)
            # Map photo_position to reportlab's hAlign. Unknown values fall
            # back to the default (right) rather than erroring — the
            # config write path validates, and tolerance here means a
            # typo'd config doesn't crash rendering.
            if photo_position not in _VALID_PHOTO_POSITIONS:
                photo_position = "right"
            img.hAlign = photo_position.upper()  # "LEFT" / "RIGHT" / "CENTER"
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
        # _linkify_contact wraps email + linkedin + github + https URLs
        # in clickable <a> tags. Non-URL text (phone, location) passes
        # through _escape unchanged. See _linkify_contact docstring.
        flow.append(Paragraph(_linkify_contact(doc.contact_line), contact_style))

    for section in section_order_fn(doc):
        flow.append(Paragraph(_escape(section.heading), styles["SectionHeading"]))
        flow.append(_section_divider())
        # Paragraphs directly under the section (summary body). Horizontal-
        # rule sentinels get emitted as HRFlowable instead of text. See
        # `_HR_SENTINEL` for why this shape (issue #22 markdown `---` was
        # silently dropped pre-fix).
        for para in section.raw_paragraphs:
            if para == _HR_SENTINEL:
                flow.append(Spacer(1, 4))
                flow.append(HRFlowable(
                    width="100%",
                    thickness=0.5,
                    color=HexColor("#888888"),
                    spaceBefore=2,
                    spaceAfter=4,
                ))
            else:
                # _linkify_text so URLs in Summary prose become clickable.
                flow.append(Paragraph(_linkify_text(para), styles["Body"]))
        # Bare bullets (common for Skills). _linkify_text so URLs embedded
        # in skills bullets ("see github.com/me/X") are clickable.
        for bullet in section.raw_bullets:
            flow.append(Paragraph(_linkify_text(bullet), styles["Bullet"], bulletText="•"))
        # Blocks (Experience, Education, Projects).
        #
        # KeepTogether granularity (issue #42 follow-up): we split each block
        # into smaller KeepTogether groups instead of one giant group per
        # block. Why: when an entire block (title + 3 sub-blocks + their
        # bullets) is bigger than the remaining page space, reportlab
        # page-breaks BEFORE the whole thing, leaving the bottom of the
        # page blank. Per-sub-block KeepTogether lets reportlab break
        # between sub-blocks (the natural typesetting break) while still
        # gluing each sub-role title to its own bullets.
        for block in section.blocks:
            block_group: list = list(_render_titled_block(
                block.title, styles["BlockTitle"], styles["BlockMetadata"],
            ))
            # Direct bullets (single-role blocks) stay glued to the block
            # title — page-breaking between a job title and its first
            # bullet would orphan the title at the bottom of a page.
            for bullet in block.bullets:
                block_group.append(Paragraph(
                    _linkify_text(bullet), styles["Bullet"], bulletText="•",
                ))
            flow.append(KeepTogether(block_group))
            # Each sub-block (multi-role tenure entry) gets its own
            # KeepTogether so a sub-role title stays with its bullets, but
            # adjacent sub-blocks within the same parent block CAN break
            # across pages — which is the right typesetting boundary.
            for sub in block.sub_blocks:
                sub_group: list = list(_render_titled_block(
                    sub.title, styles["SubBlockTitle"], styles["SubBlockMetadata"],
                ))
                for bullet in sub.bullets:
                    sub_group.append(Paragraph(
                        _linkify_text(bullet), styles["SubBlockBullet"], bulletText="•",
                    ))
                flow.append(KeepTogether(sub_group))

    return flow


_MARKDOWN_BOLD_RE = re.compile(r"\*\*([^\n*][^\n]*?)\*\*")


# Patterns for linkifying URLs and emails anywhere in resume content. Order
# matters: email is matched before generic URLs (an email contains '@' but no
# '://'), and specific hosts (linkedin / github) match before the generic
# https URL pattern so bare-domain forms like "linkedin.com/in/foo" or
# "github.com/me" (no scheme — the form students typically write in project
# titles and bullets) get linked too.
#
# URL-character exclusion class. Stops at characters that are NEVER part of a
# URL per RFC 3986 (whitespace, `<>"`{}^|\`), at the pipe-separator that
# contact lines use, and at closing paren / bracket so paren-balancing works
# in patterns like "Foo (github.com/me/foo)". Backtick exclusion fixes the
# specific failure surfaced by real-run testing — the tailor LLM sometimes
# wraps URLs in markdown code spans (`github.com/foo`), and pre-fix the regex
# greedily consumed the closing backtick into the URL match, producing a
# broken href.
_URL_DISALLOWED = r"\s<>\"`{}^|\\)\]"
_LINK_EMAIL_RE = r"[\w.+-]+@[\w-]+\.[\w.-]+"
_LINK_LINKEDIN_RE = rf"linkedin\.com/in/[^{_URL_DISALLOWED}]+"
_LINK_GITHUB_RE = rf"github\.com/[^{_URL_DISALLOWED}]+"
_LINK_HTTPS_RE = rf"https?://[^{_URL_DISALLOWED}]+"
_LINK_PATTERN = re.compile(
    rf"({_LINK_EMAIL_RE}|{_LINK_HTTPS_RE}|{_LINK_LINKEDIN_RE}|{_LINK_GITHUB_RE})"
)


def _linkify_text(text: str) -> str:
    """
    Wrap email + URL substrings in clickable `<a>` tags, HTML-escape
    everything else (and convert `**bold**` to `<b>bold</b>` via `_escape`).

    Used everywhere resume text is rendered into a Paragraph: contact line,
    block titles, sub-block titles, bullets, and Summary-style paragraphs.
    URLs in the body of a project title (`Resumasher (github.com/earino/
    resumasher)`) or in a bullet description (`see https://...`) are now
    clickable in the PDF, same as the contact line's email and LinkedIn
    profile.

    Email → `<a href="mailto:...">text</a>`.
    `https://...` URL → `<a href="...">text</a>`.
    Bare-domain `linkedin.com/in/X` / `github.com/X` get an automatic
    `https://` prefix on the href; displayed text stays bare-domain.

    Link styling: no `color=` attribute on the `<a>` tag, so links inherit
    the surrounding paragraph color. The clickable behavior is communicated
    by the PDF reader's cursor change, not by 1990s-blue-underline. Modern
    resume convention.

    ATS impact: zero. Link annotations are a PDF metadata layer that does
    not alter the text stream — pdfminer and other parsers extract identical
    text whether links are present or not.
    """
    parts = _LINK_PATTERN.split(text)
    out: list[str] = []
    # re.split with a capture group returns alternating non-match / match
    # / non-match. Even indices are non-link text (HTML-escape via _escape);
    # odd indices are link candidates we wrap in <a>.
    for i, part in enumerate(parts):
        if i % 2 == 0:
            out.append(_escape(part))
            continue
        if "@" in part and "://" not in part:
            href = f"mailto:{part}"
        elif part.startswith("http"):
            href = part
        else:
            href = f"https://{part}"
        out.append(f'<a href="{href}">{_escape(part)}</a>')
    return "".join(out)


# Backwards-compatible alias. The function previously named _linkify_contact
# is now the general _linkify_text — same behavior, broader name. Keep the
# old name for any caller / test that imports it.
_linkify_contact = _linkify_text


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


def _resolve_photo_path(explicit_flag: Optional[str], doc: ResumeDoc) -> Optional[str]:
    """Decide which photo (if any) the renderer should embed.

    Precedence, highest to lowest:
      1. Explicit `--photo <path>` flag (argument to the renderer). Wins
         over everything — "the caller knows best."
      2. Markdown comment `<!-- photo: /path -->` in the tailored
         markdown (exposed via `doc.photo_path`). This makes the markdown
         self-describing so re-render after manual edits works without
         external config state. Shipped in #20.
      3. No photo. Renderer embeds nothing.

    Returns None when no photo should be embedded, or a path string
    otherwise. Shared by render_resume_eu and render_resume_us (US
    style discards the result either way, but the precedence logic is
    the same for API symmetry).
    """
    if explicit_flag is not None:
        return explicit_flag
    if doc.photo_path:
        return doc.photo_path
    return None


def render_resume_eu(
    source_markdown: str,
    output_path: str | Path,
    photo: Optional[str] = None,
    photo_position: str = "right",
) -> Path:
    """EU-style single-column resume. Photo optional (DACH convention).

    Photo source precedence: `photo` argument > markdown `<!-- photo: -->`
    comment > no photo. See `_resolve_photo_path`.

    `photo_position` controls alignment: "right" (DACH convention, default),
    "left" (French / Benelux convention), or "center" (unusual but
    supported). Unknown values fall back to "right".
    """
    styles = _build_styles()
    doc = parse_resume_markdown(source_markdown)
    _assert_contact_header_present(doc, source_markdown)
    resolved_photo = _resolve_photo_path(photo, doc)
    flow = _build_resume_flowables(
        doc,
        styles,
        section_order_fn=_section_order_eu,
        center_header=False,
        photo_path=resolved_photo,
        photo_position=photo_position,
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
            flow.append(_section_divider())
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
    parser.add_argument(
        "--photo-position",
        choices=["right", "left", "center"],
        default="right",
        help="Photo alignment (EU resume only). Right is DACH convention. "
             "Left is French/Benelux. Center is unusual but supported.",
    )
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
                render_resume_eu(
                    source, args.output,
                    photo=args.photo,
                    photo_position=args.photo_position,
                )
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
    # See orchestration.py for the rationale — Windows defaults stdout/stderr
    # to CP1252 which crashes on non-ASCII glyphs (→, …, curly quotes, unicode
    # names). Force UTF-8 at the CLI boundary.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
