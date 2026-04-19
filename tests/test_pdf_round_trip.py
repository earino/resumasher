"""
End-to-end PDF round-trip against the GOLDEN_FIXTURES/ realistic resume.

The existing tests in test_render_pdf.py cover parser correctness and
rendering invariants on small inline samples. This file covers the
realistic shape: the full Ana Müller fixture students actually read when
they first open the repo, run through render_resume_eu and render_resume_us,
then extracted with pdfminer.six and asserted against the claims the README
makes ("ATS-safe", "<200KB", "sections parse the way you'd expect").

A regression here means the tool is shipping broken output to students.
That's the line CI defends.

Artifacts are written to /tmp/resumasher-ci-pdfs/ so CI can upload them on
failure for a human to open and eyeball.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pdfminer.high_level import extract_text

from scripts.render_pdf import (
    assert_ats_roundtrip,
    render_resume_eu,
    render_resume_us,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_RESUME = REPO_ROOT / "GOLDEN_FIXTURES" / "resume.md"

# Size ceiling the README promises. A regression that blows past this usually
# means photo embedding, font subsetting, or reportlab upgraded and changed
# defaults. Either way: catch it before students do.
MAX_PDF_BYTES = 200_000

# Content markers we expect to survive the round-trip. These span name,
# contact, every section, unicode (Müller, €), numbers with punctuation
# (F1=0.82, €180K), and realistic job-history phrasing.
EXPECTED_SUBSTRINGS = [
    "Ana Müller",
    "ana.muller@example.com",
    "+43 664 1234567",
    "Vienna, Austria",
    "MSc Business Analytics",
    "Raiffeisen Bank International",
    "F1=0.82",
    "Tableau dashboard",
    "Central Graduate School",
    "Kontron AG",
    "€180K",
    "Capstone",
    "Python, R, SQL",
    "XGBoost",
    "Churn Classifier",
]

# Section headings we expect to appear in EU order: Summary first, then
# Experience, Education, Skills, Projects. US style reorders (Experience
# before Summary in some templates) — we check US ordering separately.
EU_SECTION_ORDER = ["Summary", "Experience", "Education", "Skills", "Projects"]


@pytest.fixture(scope="module")
def ci_artifact_dir() -> Path:
    """Persist PDFs to /tmp/resumasher-ci-pdfs/ so CI can upload on failure."""
    d = Path(os.environ.get("RESUMASHER_CI_PDF_DIR", "/tmp/resumasher-ci-pdfs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(scope="module")
def golden_markdown() -> str:
    assert GOLDEN_RESUME.exists(), (
        f"GOLDEN_FIXTURES/resume.md missing at {GOLDEN_RESUME}; CI can't run "
        f"the round-trip test without it."
    )
    return GOLDEN_RESUME.read_text(encoding="utf-8")


def test_golden_fixture_eu_round_trip(
    golden_markdown: str, ci_artifact_dir: Path
) -> None:
    out = ci_artifact_dir / "golden-eu.pdf"
    render_resume_eu(golden_markdown, out)

    assert out.exists(), "render_resume_eu did not produce a file"
    size = out.stat().st_size
    assert 1000 < size < MAX_PDF_BYTES, (
        f"EU golden fixture PDF is {size} bytes; expected 1KB–{MAX_PDF_BYTES}. "
        f"Under 1KB means empty render; over {MAX_PDF_BYTES} means we broke "
        f"the size ceiling the README promises."
    )

    assert_ats_roundtrip(out, EXPECTED_SUBSTRINGS)


def test_golden_fixture_us_round_trip(
    golden_markdown: str, ci_artifact_dir: Path
) -> None:
    out = ci_artifact_dir / "golden-us.pdf"
    render_resume_us(golden_markdown, out)

    assert out.exists(), "render_resume_us did not produce a file"
    size = out.stat().st_size
    assert 1000 < size < MAX_PDF_BYTES, (
        f"US golden fixture PDF is {size} bytes; expected 1KB–{MAX_PDF_BYTES}."
    )

    assert_ats_roundtrip(out, EXPECTED_SUBSTRINGS)


def test_golden_fixture_eu_section_order(
    golden_markdown: str, ci_artifact_dir: Path
) -> None:
    """EU style renders sections in source order: Summary → Experience →
    Education → Skills → Projects. A regression that shuffles sections
    breaks ATS parsing (Workday expects Experience before Education; both
    expect Summary up top)."""
    out = ci_artifact_dir / "golden-eu-ordering.pdf"
    render_resume_eu(golden_markdown, out)
    extracted = extract_text(str(out))

    positions = {}
    for heading in EU_SECTION_ORDER:
        idx = extracted.find(heading)
        assert idx != -1, (
            f"Section '{heading}' missing from EU PDF. Extracted text:\n"
            f"{extracted[:500]}..."
        )
        positions[heading] = idx

    ordered = sorted(EU_SECTION_ORDER, key=lambda h: positions[h])
    assert ordered == EU_SECTION_ORDER, (
        f"EU sections out of order. Expected {EU_SECTION_ORDER}, got "
        f"{ordered} (positions: {positions})."
    )


def test_golden_fixture_eu_bullets_attach_to_companies(
    golden_markdown: str, ci_artifact_dir: Path
) -> None:
    """Regression: sub-role titles + bullets must stay near their parent
    company heading, not float to the top of the section. The Raiffeisen
    bullet about F1=0.82 must appear AFTER the Raiffeisen company line
    and BEFORE the next company (Teaching Assistant)."""
    out = ci_artifact_dir / "golden-eu-attachment.pdf"
    render_resume_eu(golden_markdown, out)
    extracted = extract_text(str(out))

    raiffeisen = extracted.find("Raiffeisen Bank International")
    f1_bullet = extracted.find("F1=0.82")
    teaching = extracted.find("Teaching Assistant")
    kontron = extracted.find("Kontron AG")
    kontron_bullet = extracted.find("€180K")

    assert raiffeisen != -1 and f1_bullet != -1 and teaching != -1
    assert kontron != -1 and kontron_bullet != -1

    assert raiffeisen < f1_bullet < teaching, (
        f"Raiffeisen bullet 'F1=0.82' floated away from its company heading. "
        f"Positions: Raiffeisen={raiffeisen}, F1_bullet={f1_bullet}, "
        f"Teaching={teaching}."
    )
    assert kontron < kontron_bullet, (
        f"Kontron bullet '€180K' appeared before its company heading. "
        f"Positions: Kontron={kontron}, bullet={kontron_bullet}."
    )
