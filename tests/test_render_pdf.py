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

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.render_pdf import (
    MissingContactHeaderError,
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


@pytest.mark.parametrize(
    "renderer,kwargs",
    [
        (render_resume_us, {}),
        (render_resume_eu, {"photo": None}),
    ],
    ids=["us", "eu"],
)
def test_contact_info_header_survives_both_styles(tmp_path: Path, renderer, kwargs):
    """
    The contact_info header that build_prompt inserts (a 2-line block of
    ``# Name`` + ``email | phone | linkedin | location``) must render
    identically through US and EU styles. This locks in the claim that the
    tailor's markdown is style-agnostic — the only US/EU differences are
    downstream (section ordering, photo handling, center vs left header).
    Regression guard for a future renderer change that breaks one style.
    """
    md = (
        "# Eduardo Ariño de la Rubia\n"
        "earino@gmail.com | +1 650 200 7168 | linkedin.com/in/earino | Vienna\n"
        "\n"
        "## Summary\n"
        "Test summary for style-agnostic header check.\n"
        "\n"
        "## Experience\n"
        "\n"
        "### Senior Director — Meta (2017 - Present)\n"
        "- Did stuff.\n"
    )
    out = tmp_path / "resume.pdf"
    renderer(md, out, **kwargs)

    assert_ats_roundtrip(out, [
        "Eduardo Ariño de la Rubia",
        "earino@gmail.com",
        "+1 650 200 7168",
        "linkedin.com/in/earino",
        "Vienna",
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


# ---------------------------------------------------------------------------
# MissingContactHeaderError — loud failure when tailor emits no `# Name` H1
#
# Issue #18 / KNOWN_FAILURE_MODES.md #1: the tailor sometimes produces a
# tailored-resume.md that pipe-joins the name and contact info onto line 1
# with no `# ` prefix. The parser doesn't recognize it as a contact header,
# silently drops everything before the first `##`, and the PDF ships with
# no candidate name anywhere — an ATS cannot identify the applicant.
#
# Fix: renderer raises MissingContactHeaderError loudly. Student gets an
# actionable error instead of a subtly-broken PDF they'd submit without
# noticing.
# ---------------------------------------------------------------------------


# The shape from issue #18: pipe-joined contact line with no `# Name` H1.
PIPE_JOINED_NO_H1_MD = """Test Candidate | +43 664 0000000 | test@example.com | Vienna, Austria | linkedin.com/in/testcandidate | github.com/testcandidate

## Summary
MSc Business Analytics candidate with a strong foundation in statistical modeling.

## Experience
### Data Analyst — Some Company (2024)
- Did a thing.
"""


def test_render_resume_eu_raises_on_missing_contact_header(tmp_path: Path):
    """Regression guard for issue #18: pipe-separated line 1 with no
    `# Name` H1. The renderer MUST refuse — a broken PDF shipped to an
    ATS silently filters out the application and the student never
    hears back.

    If this test ever starts passing WITHOUT the exception, someone
    removed the loud-failure gate and we risk shipping subtly-wrong
    PDFs again."""
    out = tmp_path / "no-header.pdf"
    with pytest.raises(MissingContactHeaderError) as exc:
        render_resume_eu(PIPE_JOINED_NO_H1_MD, out)

    # The exception message should name the problem clearly.
    assert "missing the candidate name header" in str(exc.value).lower()
    # The first_line attribute should carry what the parser actually saw,
    # so the caller can surface it in the student-facing error message.
    assert "Test Candidate" in exc.value.first_line
    assert "|" in exc.value.first_line
    # No PDF should have been written — the renderer refuses BEFORE rendering.
    assert not out.exists()


def test_render_resume_us_raises_on_missing_contact_header(tmp_path: Path):
    """Same loud-failure gate applies to US style. US suppresses the photo
    but still needs the candidate name for ATS identification — the bug
    and the fix are style-agnostic."""
    out = tmp_path / "no-header-us.pdf"
    with pytest.raises(MissingContactHeaderError):
        render_resume_us(PIPE_JOINED_NO_H1_MD, out)
    assert not out.exists()


def test_missing_contact_header_error_captures_first_line_for_diagnostics():
    """The exception's `first_line` attribute is the diagnostic breadcrumb
    the SKILL.md Phase 8 error handler shows the student ('Got: <this>').
    Verify it's set correctly for three different failure shapes."""
    # Shape A: pipe-separated without H1 (the #18 reporter case)
    md_a = "Candidate Name | email@example.com | +1 555 0000\n\n## Summary\nHi.\n"
    out = Path("/tmp/never-written.pdf")
    with pytest.raises(MissingContactHeaderError) as exc:
        render_resume_eu(md_a, out)
    assert exc.value.first_line.startswith("Candidate Name |")

    # Shape B: a title/summary line (agent might synthesize this wrong —
    # option (a) of our design debate would have embedded "MSc Business
    # Analytics Candidate" as the candidate's name if we'd gone that way).
    md_b = "MSc Business Analytics Candidate, Available July 2026\n\n## Summary\nHi.\n"
    with pytest.raises(MissingContactHeaderError) as exc:
        render_resume_eu(md_b, out)
    assert "MSc Business Analytics Candidate" in exc.value.first_line

    # Shape C: completely empty markdown. first_line should be empty string,
    # the exception should still raise (empty name → fail).
    md_c = ""
    with pytest.raises(MissingContactHeaderError) as exc:
        render_resume_eu(md_c, out)
    assert exc.value.first_line == ""


def test_render_resume_eu_happy_path_still_works_with_gate(tmp_path: Path):
    """Sanity check: a well-formed resume with `# Name` H1 renders
    normally. The loud-failure gate doesn't interfere with good input."""
    md = (
        "# Ana Müller\n"
        "ana@example.com | +43 123 | Vienna\n"
        "\n"
        "## Summary\n"
        "Short summary.\n"
        "\n"
        "## Experience\n"
        "### Data Analyst — Raiffeisen (2025)\n"
        "- Shipped a churn model.\n"
    )
    out = tmp_path / "ok.pdf"
    render_resume_eu(md, out)
    assert out.exists()
    assert_ats_roundtrip(out, ["Ana Müller", "ana@example.com", "Raiffeisen"])


def test_cli_render_pdf_prints_actionable_error_and_exits_2(tmp_path: Path):
    """End-to-end via the CLI: simulate the path SKILL.md Phase 8 takes.
    Running `render_pdf --input <bad-md>` on a no-H1 markdown should
    print a multi-line error to stderr naming the problem, the expected
    shape, the actual line 1, and the suggested action — and exit 2
    so the orchestrator knows something broke."""
    bad_md = tmp_path / "bad.md"
    bad_md.write_text(PIPE_JOINED_NO_H1_MD, encoding="utf-8")
    out = tmp_path / "out.pdf"
    r = subprocess.run(
        [
            sys.executable, "-m", "scripts.render_pdf",
            "--input", str(bad_md),
            "--kind", "resume",
            "--style", "eu",
            "--output", str(out),
        ],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 2
    # Error message reaches stderr (so Phase 8 can surface it to the student)
    assert "missing the candidate name header" in r.stderr.lower()
    # And names the specific expected shape
    assert "# Your Name" in r.stderr
    # And shows the student what was actually on line 1
    assert "Test Candidate | +43" in r.stderr
    # And references the tracking issue so the student / agent can follow up
    assert "#18" in r.stderr
    # No PDF should have been written
    assert not out.exists()


# ---------------------------------------------------------------------------
# Issue #19 — synthetic blocks from `**Title** | metadata` under `##`
#
# Before the fix, `parse_resume_markdown` only recognized `**Title**` as a
# sub-block when it appeared inside a `### Company` block. Tailors often emit
# `**Project**` directly under `## Research Experience` without the `###`
# wrapper, and the parser was silently dropping the titles into
# raw_paragraphs and the bullets into raw_bullets. The renderer emitted
# all titles first, then all bullets — reader could not tell which bullet
# belonged to which project.
#
# After the fix: `**Title** | metadata` at section level creates a synthetic
# ResumeBlock. Bullets attach to it until the next `**Title**` or `##`.
# The trailing-metadata check is load-bearing — bold-only prose like
# "**Accomplished leader.**" in a Summary still falls through to paragraphs.
# ---------------------------------------------------------------------------


SHAPE_B_RESUME_MD = """# Test Candidate
test@example.com | +43 | Vienna, Austria

## Summary
MSc Business Analytics candidate.

## Research Experience

**SME High-Growth Determinants — Predictive Modeling** | Feb 2026 | Group Research
- Engineered an automated data pipeline for 20,000 firms.
- Developed 118 features for nonlinear patterns.

**Cross-Linguistic Sentiment Analysis via AWS** | Nov 2025 | Individual Research
- Built a cloud-native Python pipeline.
- Identified sentiment variations across 30 multilingual reports.

**Capstone Project — NLP on CRM Sales Data** | Ongoing | Collaborating with Sherpany
- Operationalising a structured sales-flag framework.
"""


def test_parse_synthetic_block_from_bold_title_with_metadata():
    """Shape B parsing: 3 `**Title** | metadata` lines under `##` produce
    3 blocks with the right bullets attached. This is the core issue #19 fix."""
    doc = parse_resume_markdown(SHAPE_B_RESUME_MD)
    research = next(s for s in doc.sections if s.heading == "Research Experience")

    assert len(research.blocks) == 3, (
        f"Expected 3 synthetic blocks, got {len(research.blocks)}"
    )
    assert research.raw_bullets == [], (
        f"Bullets should attach to blocks, not sit at section level. "
        f"Got raw_bullets: {research.raw_bullets}"
    )
    assert research.raw_paragraphs == [], (
        f"`**Title**` lines should become block titles, not paragraphs. "
        f"Got raw_paragraphs: {research.raw_paragraphs}"
    )

    # Block titles preserve the metadata (dates, context).
    assert research.blocks[0].title.startswith("SME High-Growth")
    assert "Feb 2026" in research.blocks[0].title
    assert research.blocks[1].title.startswith("Cross-Linguistic")
    assert research.blocks[2].title.startswith("Capstone Project")

    # Bullets attached correctly: 2, 2, 1.
    assert len(research.blocks[0].bullets) == 2
    assert len(research.blocks[1].bullets) == 2
    assert len(research.blocks[2].bullets) == 1
    assert "Engineered" in research.blocks[0].bullets[0]
    assert "Cloud-native" in research.blocks[1].bullets[0] or "cloud-native" in research.blocks[1].bullets[0]
    assert "sales-flag" in research.blocks[2].bullets[0]


def test_parse_bold_title_without_metadata_stays_as_paragraph():
    """Option-C edge case: a bold-only line in a section (no trailing
    metadata) is ambiguous — could be prose or a bare title. Parser
    treats it as prose to avoid hijacking Summary sections where bold
    emphasis is legitimate. Only `**Title** + trailing metadata` opens
    a synthetic block at section level."""
    md = (
        "# Test User\n"
        "test@example.com | +1\n"
        "\n"
        "## Summary\n"
        "**Accomplished data leader.**\n"
        "Further prose about the candidate.\n"
    )
    doc = parse_resume_markdown(md)
    summary = next(s for s in doc.sections if s.heading == "Summary")

    # No synthetic block — the bold-only line has no trailing metadata.
    assert summary.blocks == [], (
        f"Bold-only line (no trailing metadata) should NOT open a synthetic "
        f"block. Got blocks: {[b.title for b in summary.blocks]}"
    )
    # The bold line falls through to raw_paragraphs like any other prose.
    assert len(summary.raw_paragraphs) == 2
    assert "**Accomplished data leader.**" in summary.raw_paragraphs


def test_parse_three_level_sub_block_shape_still_works():
    """Regression guard: the existing multi-role tenure shape (`###
    Company` then `**Role**` as sub-block) must still produce a block
    with sub_blocks. The fix for shape B must not break shape A."""
    md = (
        "# Test User\n"
        "test@example.com | +1\n"
        "\n"
        "## Experience\n"
        "\n"
        "### Central European University (December 2017 – Present)\n"
        "**Professor of Practice** (August 2025 – Present)\n"
        "- Designed the practical deep learning course.\n"
        "**Visiting Professor** (December 2017 – July 2025)\n"
        "- Taught applied analytics.\n"
    )
    doc = parse_resume_markdown(md)
    experience = next(s for s in doc.sections if s.heading == "Experience")

    # One real block (the `### Company` heading).
    assert len(experience.blocks) == 1
    ceu = experience.blocks[0]
    assert ceu.title.startswith("Central European University")
    # Two sub-blocks (the `**Role**` lines inside the block).
    assert len(ceu.sub_blocks) == 2
    assert ceu.sub_blocks[0].title.startswith("Professor of Practice")
    assert ceu.sub_blocks[1].title.startswith("Visiting Professor")
    # Bullets attached to the right sub-block.
    assert any("deep learning" in b for b in ceu.sub_blocks[0].bullets)
    assert any("applied analytics" in b for b in ceu.sub_blocks[1].bullets)
    # Block has no DIRECT bullets — they're all on sub-blocks.
    assert ceu.bullets == []


def test_parse_mixed_shape_synthetic_then_real_then_sub():
    """Mixed shape: `**ProjectA**` (synthetic) then `### CompanyB` (real)
    then `**RoleB**` (sub-block of CompanyB). This confirms the
    synthetic_block_active flag toggles correctly on `###`, so a
    `**Title**` after a real block correctly becomes a sub-block."""
    md = (
        "# Test User\n"
        "test@example.com | +1\n"
        "\n"
        "## Experience\n"
        "\n"
        "**Project Alpha** | Jan 2025\n"
        "- Shipped alpha.\n"
        "\n"
        "### BigCo Inc. (2023 – Present)\n"
        "**Senior Role** (2024 – Present)\n"
        "- Did senior things.\n"
    )
    doc = parse_resume_markdown(md)
    experience = next(s for s in doc.sections if s.heading == "Experience")

    # Two top-level blocks: one synthetic (Project Alpha), one real (BigCo Inc.).
    assert len(experience.blocks) == 2
    synthetic, real = experience.blocks

    assert synthetic.title.startswith("Project Alpha")
    assert len(synthetic.bullets) == 1
    assert "alpha" in synthetic.bullets[0]
    assert synthetic.sub_blocks == [], (
        "Synthetic block should not have sub-blocks from the later `### BigCo` — "
        "the `###` closes the synthetic and opens a new real block."
    )

    assert real.title.startswith("BigCo Inc.")
    # The `**Senior Role**` under `### BigCo` becomes a sub-block, NOT a
    # sibling synthetic block — because `current_block` is real (not synthetic)
    # when we see the `**Title**`.
    assert len(real.sub_blocks) == 1
    assert real.sub_blocks[0].title.startswith("Senior Role")
    assert any("senior things" in b for b in real.sub_blocks[0].bullets)


def test_parse_bold_inline_in_paragraph_not_hijacked():
    """Regression guard: bold emphasis inside a paragraph (not a full
    line of bold) must not trigger block creation. The `_SUB_BLOCK_RE`
    regex requires the ENTIRE line to start with `**` and have the
    closing `**` match the opening — inline bold fails this check."""
    md = (
        "# Test User\n"
        "test@example.com | +1\n"
        "\n"
        "## Summary\n"
        "I ran **rigorous A/B testing** on a production pipeline.\n"
        "Results were **significant** at p < 0.05.\n"
    )
    doc = parse_resume_markdown(md)
    summary = next(s for s in doc.sections if s.heading == "Summary")

    assert summary.blocks == [], "Inline bold in prose should not open blocks"
    assert summary.raw_bullets == []
    assert len(summary.raw_paragraphs) == 2


def test_render_synthetic_blocks_interleave_bullets_with_titles(tmp_path: Path):
    """End-to-end: issue #19 shape renders with each bullet appearing
    BETWEEN its parent title and the next project's title in the PDF.
    This is the positional assertion that catches the exact regression
    we're fixing — before the fix, all titles stacked at the top and
    all bullets dumped at the bottom."""
    from pdfminer.high_level import extract_text

    out = tmp_path / "shape-b.pdf"
    render_resume_eu(SHAPE_B_RESUME_MD, out)
    assert out.exists()
    extracted = extract_text(str(out))

    def pos(needle: str) -> int:
        idx = extracted.find(needle)
        assert idx != -1, f"'{needle}' missing from extracted PDF text"
        return idx

    # Positional assertions: SME's bullet "Engineered..." must appear AFTER
    # the SME title AND BEFORE the next project's title. Same for the
    # Cross-Linguistic bullet and the Capstone bullet.
    sme_title = pos("SME High-Growth")
    sme_bullet = pos("Engineered an automated data pipeline")
    cross_title = pos("Cross-Linguistic")
    cross_bullet = pos("sentiment variations")
    capstone_title = pos("Capstone Project")
    capstone_bullet = pos("sales-flag framework")

    assert sme_title < sme_bullet < cross_title, (
        f"SME bullet out of place: title={sme_title}, bullet={sme_bullet}, "
        f"next_title={cross_title}. Before the fix, sme_bullet would appear "
        f"AFTER cross_title (flat bullet dump)."
    )
    assert cross_title < cross_bullet < capstone_title, (
        f"Cross-Linguistic bullet out of place: title={cross_title}, "
        f"bullet={cross_bullet}, next_title={capstone_title}."
    )
    assert capstone_title < capstone_bullet, (
        f"Capstone bullet appears before its title: title={capstone_title}, "
        f"bullet={capstone_bullet}."
    )
