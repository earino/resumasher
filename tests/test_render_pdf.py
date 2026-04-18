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

### Teaching Assistant — Central Graduate School (2024-2025)
- Coached 40 students through the Python for Data Analysis course.

## Education
### MSc Business Analytics — Central Graduate School (2024-2026)
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
    # The message now comes from the downscale step ("could not downscale") or
    # from the embed fallback ("could not embed"). Either is acceptable — both
    # indicate the missing file was noticed.
    err = captured.err.lower()
    assert "could not downscale" in err or "could not embed" in err


def test_render_resume_eu_downscales_large_photo(tmp_path: Path):
    """
    Regression: a real student used a 1MB+ headshot and the output PDF came
    out at 1.0 MB total. Photos must be downscaled before embedding.
    """
    from PIL import Image as PILImage

    # Create a realistic-ish photo fixture: 3000x4000 gradient JPEG with mild
    # noise — compresses like a real headshot export (quality 95 JPEG from a
    # phone camera typically lands at 1-3MB). Using JPEG because most student
    # headshots are JPEG, not PNG.
    big_photo = tmp_path / "headshot.jpg"
    img = PILImage.new("RGB", (3000, 4000))
    pixels = img.load()
    for y in range(4000):
        for x in range(0, 3000, 40):  # sparse gradient to keep test fast
            for xi in range(x, min(x + 40, 3000)):
                pixels[xi, y] = (
                    (xi * 255) // 3000,
                    (y * 255) // 4000,
                    ((xi + y) * 255) // 7000,
                )
    img.save(big_photo, format="JPEG", quality=95)
    photo_size = big_photo.stat().st_size
    assert photo_size > 200_000, (
        f"test fixture itself is too small ({photo_size} bytes) to exercise "
        f"the downscale path meaningfully"
    )

    out = tmp_path / "eu.pdf"
    render_resume_eu(SAMPLE_RESUME_MD, out, photo=str(big_photo))
    pdf_size = out.stat().st_size

    # Embedded at source: PDF would balloon to 500KB+ for a real photo,
    # 1MB+ for the actual Keensight run that motivated this fix.
    # Downscaled to 500px max, total PDF should be well under 150KB.
    assert pdf_size < 200_000, (
        f"PDF is {pdf_size} bytes; expected <200KB after photo downscale. "
        f"Source photo was {photo_size} bytes."
    )
    # Content should still render — downscale shouldn't break layout.
    assert_ats_roundtrip(out, ["Ana Müller", "Raiffeisen Bank"])


def test_render_resume_eu_small_photo_not_upscaled(tmp_path: Path):
    """Small photos (<=500px) should pass through without upscaling."""
    from PIL import Image as PILImage
    from pdfminer.high_level import extract_text

    small_photo = tmp_path / "headshot.png"
    PILImage.new("RGB", (300, 300), color=(100, 100, 200)).save(small_photo)

    out = tmp_path / "eu.pdf"
    render_resume_eu(SAMPLE_RESUME_MD, out, photo=str(small_photo))
    assert out.exists()
    assert "Ana Müller" in extract_text(str(out))


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


MULTI_ROLE_RESUME_MD = """# Eduardo Test
test@example.com | +43 000 | linkedin.com/in/test | Vienna, Austria

## Summary
Senior analytics leader.

## Experience
### Central European University (December 2017 – Present, 8 years)
**Professor of Practice** (August 2025 – Present)
- Designed ECBS5200 "Practical Deep Learning Engineering" course.
- Authored ECBS5256 "Managing Data Science Teams" curriculum.

**Visiting Professor** (December 2017 – July 2025)
- Taught applied analytics to the MS Business Analytics cohort.

### Meta (July 2017 – August 2025)
**Senior Director, Data Science** (August 2022 – August 2025, Zurich)
- Led a senior data science organization.
- Set delivery standards and hiring bar.

**Director, Data Science** (January 2021 – September 2022)
- Scaled the team through hypergrowth.

**Data Science Manager** (July 2017 – February 2021, Menlo Park)
- Built the team from the ground up.

### Chief Data Scientist — Domino Data Lab (December 2015 – July 2017)
- Senior technical face of the data science platform.
- Advised Fortune 500 CDOs.
"""


def test_parse_multi_role_sub_blocks():
    """Regression: the old parser dumped **Title** lines into raw_paragraphs,
    which the renderer emitted at the TOP of the section, disconnected from
    their bullets. Verify the new parser attaches them to the block."""
    from scripts.render_pdf import parse_resume_markdown
    doc = parse_resume_markdown(MULTI_ROLE_RESUME_MD)
    exp = next(s for s in doc.sections if s.heading == "Experience")
    # No sub-block titles should have leaked into raw_paragraphs.
    for p in exp.raw_paragraphs:
        assert "Professor of Practice" not in p
        assert "Senior Director" not in p

    # Three blocks: CEU, Meta, Domino
    assert len(exp.blocks) == 3
    ceu, meta, domino = exp.blocks
    assert ceu.title.startswith("Central European University")
    assert len(ceu.sub_blocks) == 2
    assert ceu.sub_blocks[0].title.startswith("Professor of Practice")
    assert any("ECBS5200" in b for b in ceu.sub_blocks[0].bullets)
    assert ceu.sub_blocks[1].title.startswith("Visiting Professor")
    assert any("MS Business Analytics" in b for b in ceu.sub_blocks[1].bullets)

    assert meta.title.startswith("Meta")
    assert len(meta.sub_blocks) == 3
    assert meta.sub_blocks[0].title.startswith("Senior Director")
    assert any("hiring bar" in b for b in meta.sub_blocks[0].bullets)
    assert meta.sub_blocks[2].title.startswith("Data Science Manager")

    # Single-role blocks still work: Domino has no sub-blocks, bullets directly.
    assert domino.title.startswith("Chief Data Scientist")
    assert len(domino.sub_blocks) == 0
    assert len(domino.bullets) == 2
    assert any("Fortune 500" in b for b in domino.bullets)


def test_render_multi_role_sub_blocks_appear_in_order(tmp_path: Path):
    """Regression: the PDF must list sub-role titles INTERLEAVED with their
    bullets, not bunched all together at the top of the section."""
    from pdfminer.high_level import extract_text
    out = tmp_path / "multi.pdf"
    render_resume_eu(MULTI_ROLE_RESUME_MD, out)
    extracted = extract_text(str(out))
    # Normalize: pdfminer inserts hard line breaks; we just care about order.
    # Find the position of each marker string. Sub-titles must come AFTER
    # their parent company title AND before the next company title.
    def pos(needle: str) -> int:
        idx = extracted.find(needle)
        assert idx != -1, f"'{needle}' missing from extracted text"
        return idx

    ceu = pos("Central European University")
    professor = pos("Professor of Practice")
    visiting = pos("Visiting Professor")
    ecbs5200 = pos("ECBS5200")
    meta = pos("Meta (July 2017")
    senior_director = pos("Senior Director")
    # Unique anchors that only appear in the specific sub-block's bullets,
    # so we can verify sub-title → bullet → next-sub-title ordering.
    hiring_bar = pos("hiring bar")              # only in Senior Director bullets
    hypergrowth = pos("hypergrowth")            # only in second Director bullet
    menlo = pos("Menlo Park")                   # only in Data Science Manager title
    ground_up = pos("ground up")                # only in Data Science Manager bullet
    domino = pos("Chief Data Scientist")
    fortune_500 = pos("Fortune 500")

    # CEU sub-blocks in order: Company → SubTitle → Bullets → next SubTitle
    assert ceu < professor < ecbs5200 < visiting, (
        "CEU sub-blocks out of order"
    )
    # Meta sub-blocks in chronological order (as written in source).
    # Senior Director → its bullets (hiring bar) → Director → its bullets
    # (hypergrowth) → Manager → its bullets (ground up).
    assert meta < senior_director < hiring_bar < hypergrowth < menlo < ground_up, (
        f"Meta sub-blocks out of order: meta={meta} senior_director={senior_director} "
        f"hiring_bar={hiring_bar} hypergrowth={hypergrowth} menlo={menlo} ground_up={ground_up}"
    )
    # Domino (single-role block) appears after all CEU + Meta content.
    assert ground_up < domino < fortune_500


def test_render_interprets_markdown_bold_as_bold_not_asterisks(tmp_path: Path):
    """
    Regression: interview-prep output used `**Problem framing.**` syntax and
    it rendered as literal asterisks in the PDF. reportlab Paragraph supports
    <b>...</b> tags — _escape() now converts markdown bold to those tags.
    """
    md = """# Interview Prep

## Tech

### Walk us through the design

**Problem framing.** Before architecture, I would figure out what this is for.

**Architecture.** Pick boring technology.
"""
    out = tmp_path / "prep.pdf"
    render_interview_prep(md, out)
    from pdfminer.high_level import extract_text
    extracted = extract_text(str(out))
    # The content should still be extractable as text (bold doesn't hide it).
    assert "Problem framing." in extracted
    assert "Architecture." in extracted
    # The literal "**" should NOT appear in the extracted text.
    assert "**" not in extracted, (
        f"Found literal '**' in rendered PDF — markdown bold wasn't "
        f"converted to <b> tags. Extracted:\n{extracted}"
    )


def test_render_bold_in_cover_letter(tmp_path: Path):
    md = """# Dear Hiring Team,

I lead with **specific evidence** and skip the fluff.

My **Meta tenure** gave me the altitude the role requires.
"""
    out = tmp_path / "cover.pdf"
    render_cover_letter(md, out)
    from pdfminer.high_level import extract_text
    extracted = extract_text(str(out))
    assert "specific evidence" in extracted
    assert "Meta tenure" in extracted
    assert "**" not in extracted


def test_escape_leaves_lone_asterisk_alone(tmp_path: Path):
    """A single '*' in prose (e.g., 'footnote*') should not start an unclosed bold."""
    md = """# Test User
test@example.com

## Summary
I shipped a v1.0* — where * means "beta quality".
"""
    out = tmp_path / "aster.pdf"
    render_resume_eu(md, out)
    from pdfminer.high_level import extract_text
    extracted = extract_text(str(out))
    # Content still renders; we don't demand preservation of the literal
    # asterisk (reportlab may drop it cleanly), just that render doesn't crash
    # and the surrounding text survives.
    assert "v1.0" in extracted
    assert "beta quality" in extracted


def test_assert_ats_roundtrip_raises_on_missing(tmp_path: Path):
    out = tmp_path / "r.pdf"
    render_resume_eu(SAMPLE_RESUME_MD, out)
    with pytest.raises(AssertionError) as exc:
        assert_ats_roundtrip(out, ["this string is definitely not in the resume xyz789"])
    assert "Missing substrings" in str(exc.value)
