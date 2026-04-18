"""
Tests for scripts/render_pdf.py.

Strategy: produce PDFs from known markdown inputs, then extract text with
pdfminer.six and assert every expected substring made it through. That's the
automated tier of the ATS verification gate from the design doc.

Also covers:
- EU vs US section ordering
- US style always suppresses photo (invariant enforced in render_resume_us)
- Unicode content (Björn, Jiří Švec, François, emoji) round-trips
- Empty sections don't break layout
- HTML-ish special chars (&, <, >) are escaped, not interpreted
- Cover letter + interview prep renders produce readable PDFs
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.render_pdf import (
    assert_ats_roundtrip,
    parse_resume_markdown,
    render_cover_letter,
    render_interview_prep,
    render_resume_eu,
    render_resume_us,
)


SAMPLE_RESUME_MD = """# Ana Müller
ana.muller@example.com | +43 664 1234567 | linkedin.com/in/anamuller | Vienna, Austria

## Summary
Business analytics MSc graduate with focus on ML deployment and SQL-driven decision support.

## Experience
### Data Analyst Intern — Raiffeisen Bank (Summer 2025)
- Built a churn prediction model on 2.3M rows; F1=0.82.
- Delivered a Tableau dashboard used by 12 relationship managers.

### Teaching Assistant — CEU Vienna (2024-2025)
- Coached 40 students through the Python for Data Analysis course.

## Education
### MSc Business Analytics — CEU Vienna (2024-2026)
- Capstone: Inventory optimization for FirmX (R, Python, consulting).

## Skills
- Languages: Python, R, SQL, Bash
- Tools: pandas, scikit-learn, XGBoost, Tableau, Git

## Projects
### Churn Classifier (/projects/churn-model)
- XGBoost, F1=0.82 on 2.3M rows, deployed via Flask.
"""


UNICODE_HEAVY_RESUME_MD = """# Jiří Švec
jiri.svec@example.cz | +420 777 123456 | linkedin.com/in/jirisvec | Praha, Česko

## Summary
Analytics engineer. Fluent in Python 🐍 and R.

## Experience
### Senior Analyst — Škoda Auto (2022-2024)
- Built ETL för Björn's fleet dashboard using Airflow & Snowflake.
- Mentored François (intern) on statistical modelling.
"""


HTML_TRAP_RESUME_MD = """# Sam <Test>
sam@example.com | 555-0000 | linkedin.com/in/sam | Internet

## Summary
I love A & B testing; I ship code <fast> and <safely>.

## Experience
### Growth Analyst — Foo & Co (2023-2024)
- Shipped A/B tests with p < 0.05 thresholds.
"""


SAMPLE_COVER_MD = """# Dear Deloitte Hiring Team,

I'm writing to apply for the Data Analyst role at Deloitte. My MSc capstone on inventory optimization for FirmX gives me direct experience with the kind of consulting engagement your Vienna practice runs.

I was excited to read Deloitte's recent announcement of the expanded AI advisory practice (press release, 2026-02-08). My deployment work on a Flask-served churn model aligns with the kind of ML productionisation your team is scaling.

Thank you for considering my application. I would love the opportunity to discuss how my analytics background could contribute to your team.
"""


SAMPLE_INTERVIEW_PREP_MD = """# Interview Prep: Data Analyst — Deloitte

## SQL
### Window functions over a sales table
Walk the interviewer through using ROW_NUMBER() partitioned by customer_id, ordered by purchase_date DESC, to find the most recent purchase per customer. Mention the alternative of a correlated subquery and why the window function is faster.

## Case Study
### Declining revenue at a retail client
Frame: problem definition → hypothesis tree (price? mix? volume? external?) → data to collect → quick-win recommendation. Reference your capstone on inventory optimization as proof you've done this shape before.

## Behavioral STAR
### Tell me about a time you handled ambiguous data
Situation: capstone consulting project at FirmX with incomplete SKU-level history. Task: estimate weekly demand. Action: triangulated using POS exports + manually tagged supplier catalogs. Result: 12% forecast-error reduction vs. baseline.
"""


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


def test_parse_resume_extracts_name_and_contact():
    doc = parse_resume_markdown(SAMPLE_RESUME_MD)
    assert doc.name == "Ana Müller"
    assert "ana.muller@example.com" in doc.contact_line
    assert "Vienna" in doc.contact_line


def test_parse_resume_extracts_all_sections():
    doc = parse_resume_markdown(SAMPLE_RESUME_MD)
    headings = [s.heading for s in doc.sections]
    assert "Summary" in headings
    assert "Experience" in headings
    assert "Education" in headings
    assert "Skills" in headings
    assert "Projects" in headings


def test_parse_resume_captures_block_bullets():
    doc = parse_resume_markdown(SAMPLE_RESUME_MD)
    experience = next(s for s in doc.sections if s.heading == "Experience")
    assert len(experience.blocks) == 2
    assert any("F1=0.82" in b for b in experience.blocks[0].bullets)


def test_parse_resume_empty_input_returns_empty_doc():
    doc = parse_resume_markdown("")
    assert doc.name == ""
    assert doc.contact_line == ""
    assert doc.sections == []


# ---------------------------------------------------------------------------
# EU render tests
# ---------------------------------------------------------------------------


def test_render_resume_eu_happy_path_roundtrip(tmp_path: Path):
    out = tmp_path / "eu.pdf"
    render_resume_eu(SAMPLE_RESUME_MD, out)
    assert out.exists() and out.stat().st_size > 1000
    assert_ats_roundtrip(out, [
        "Ana Müller",
        "ana.muller@example.com",
        "Raiffeisen Bank",
        "F1=0.82",
        "Python, R, SQL",
        "MSc Business Analytics",
    ])


def test_render_resume_eu_photo_missing_warns_but_completes(tmp_path: Path, capsys):
    out = tmp_path / "eu.pdf"
    render_resume_eu(SAMPLE_RESUME_MD, out, photo=str(tmp_path / "nonexistent.jpg"))
    assert out.exists()
    captured = capsys.readouterr()
    assert "could not embed photo" in captured.err.lower()


def test_render_resume_eu_empty_sections_do_not_crash(tmp_path: Path):
    md = "# Test User\ntest@example.com\n\n## Skills\n\n## Experience\n"
    out = tmp_path / "empty.pdf"
    render_resume_eu(md, out)
    assert out.exists() and out.stat().st_size > 500


# ---------------------------------------------------------------------------
# US render tests
# ---------------------------------------------------------------------------


def test_render_resume_us_happy_path_roundtrip(tmp_path: Path):
    out = tmp_path / "us.pdf"
    render_resume_us(SAMPLE_RESUME_MD, out)
    assert out.exists()
    assert_ats_roundtrip(out, [
        "Ana Müller",
        "Raiffeisen Bank",
        "MSc Business Analytics",
    ])


def test_render_resume_us_suppresses_photo_even_when_provided(tmp_path: Path, capsys):
    # Use the bundled font file as a stand-in for "some image path the caller passed"
    # US should never embed it.
    fake_photo = tmp_path / "fake-photo.jpg"
    fake_photo.write_bytes(b"not a real image but we should never try to read it")
    out = tmp_path / "us.pdf"
    render_resume_us(SAMPLE_RESUME_MD, out, photo=str(fake_photo))
    captured = capsys.readouterr()
    assert "suppresses photo" in captured.err.lower()
    # PDF still produced successfully (because we never tried to read the fake image).
    assert out.exists() and out.stat().st_size > 500


# ---------------------------------------------------------------------------
# Unicode + escape tests (critical gap 2 from the design doc)
# ---------------------------------------------------------------------------


def test_render_resume_eu_handles_unicode_names(tmp_path: Path):
    out = tmp_path / "unicode.pdf"
    render_resume_eu(UNICODE_HEAVY_RESUME_MD, out)
    assert_ats_roundtrip(out, [
        "Jiří Švec",
        "Škoda Auto",
        "Björn",
        "François",
        "Česko",
    ])


def test_render_resume_eu_escapes_html_like_chars(tmp_path: Path):
    out = tmp_path / "html.pdf"
    # If escaping is broken, reportlab will either crash on the < > or
    # interpret Foo & Co as a broken entity. Either way the extracted text
    # would lose the literal chars. Verify they round-trip.
    render_resume_eu(HTML_TRAP_RESUME_MD, out)
    assert_ats_roundtrip(out, [
        "Sam <Test>",
        "A & B testing",
        "Foo & Co",
        "p < 0.05",
    ])


# ---------------------------------------------------------------------------
# Cover letter + interview prep
# ---------------------------------------------------------------------------


def test_render_cover_letter_roundtrip(tmp_path: Path):
    out = tmp_path / "cover.pdf"
    render_cover_letter(SAMPLE_COVER_MD, out)
    assert out.exists()
    assert_ats_roundtrip(out, [
        "Dear Deloitte Hiring Team",
        "Data Analyst",
        "FirmX",
        "AI advisory practice",
    ])


def test_render_interview_prep_roundtrip(tmp_path: Path):
    out = tmp_path / "prep.pdf"
    render_interview_prep(SAMPLE_INTERVIEW_PREP_MD, out)
    assert out.exists()
    assert_ats_roundtrip(out, [
        "SQL",
        "Window functions",
        "Case Study",
        "hypothesis tree",
        "Behavioral STAR",
        "capstone consulting project at FirmX",
    ])


# ---------------------------------------------------------------------------
# ATS round-trip helper itself
# ---------------------------------------------------------------------------


def test_assert_ats_roundtrip_raises_on_missing(tmp_path: Path):
    out = tmp_path / "r.pdf"
    render_resume_eu(SAMPLE_RESUME_MD, out)
    with pytest.raises(AssertionError) as exc:
        assert_ats_roundtrip(out, ["this string is definitely not in the resume xyz789"])
    assert "Missing substrings" in str(exc.value)
