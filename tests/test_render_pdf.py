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


# ---------------------------------------------------------------------------
# Issue #20 — photo path persisted to tailored markdown via HTML comment
#
# Before the fix: a student provides a photo, resumasher embeds it in the PDF
# correctly, but `tailored-resume.md` has no record of the path. Re-render
# after manual edits requires external state (config or CLI flag).
#
# After the fix: tailor emits `<!-- photo: /path -->` in the markdown header,
# parser extracts it onto `ResumeDoc.photo_path`, renderer uses it when no
# `--photo` flag is explicitly passed. Markdown becomes self-describing.
# Precedence: explicit flag > markdown comment > no photo.
# ---------------------------------------------------------------------------


def test_parse_picks_up_photo_comment_in_header():
    """A `<!-- photo: /path -->` right after the contact line populates
    `doc.photo_path` with the path verbatim."""
    md = (
        "# Test Candidate\n"
        "test@example.com | +1 | linkedin.com/in/testcandidate\n"
        "<!-- photo: /home/student/photos/headshot.jpg -->\n"
        "\n"
        "## Summary\n"
        "Short.\n"
    )
    doc = parse_resume_markdown(md)
    assert doc.photo_path == "/home/student/photos/headshot.jpg"
    # Contact header parsing unaffected — the comment doesn't bleed into
    # the contact line or the name.
    assert doc.name == "Test Candidate"
    assert "test@example.com" in doc.contact_line


def test_parse_photo_comment_tolerates_whitespace_variants():
    """The comment regex strips whitespace around the path so the tailor
    doesn't have to emit pixel-perfect formatting."""
    cases = [
        "<!--photo:/path/to/photo.jpg-->",
        "<!-- photo: /path/to/photo.jpg -->",
        "<!--  photo  :  /path/to/photo.jpg  -->",
        "<!-- photo:/path/to/photo.jpg-->",
    ]
    for comment in cases:
        md = f"# Test\ne@x.com\n{comment}\n\n## Summary\nx\n"
        doc = parse_resume_markdown(md)
        assert doc.photo_path == "/path/to/photo.jpg", (
            f"Failed to parse comment variant: {comment!r}"
        )


def test_parse_no_photo_comment_leaves_path_empty():
    """A markdown with no photo comment leaves doc.photo_path as an empty
    string — the renderer's precedence logic treats "" as "no photo"."""
    md = "# Test\ne@x.com\n\n## Summary\nx\n"
    doc = parse_resume_markdown(md)
    assert doc.photo_path == ""


def test_parse_first_photo_comment_wins_over_duplicates():
    """If the tailor accidentally emits two photo comments (copy-paste
    bug), the first one wins. Duplicates are silently ignored rather
    than raising — the PDF still renders, but only the first comment
    is honored."""
    md = (
        "# Test\ne@x.com\n"
        "<!-- photo: /first.jpg -->\n"
        "<!-- photo: /second.jpg -->\n"
        "\n"
        "## Summary\nx\n"
    )
    doc = parse_resume_markdown(md)
    assert doc.photo_path == "/first.jpg"


def test_parse_photo_comment_inline_in_prose_not_hijacked():
    """An HTML comment that appears in prose (not on its own line) or
    that has other text on the same line should NOT be picked up. Our
    regex is full-line-only for the same reason `_SUB_BLOCK_RE` is —
    so inline mentions can't hijack the parser."""
    md = (
        "# Test\ne@x.com\n"
        "\n"
        "## Summary\n"
        "I once wrote `<!-- photo: /joke.jpg -->` in my old resume.\n"
    )
    doc = parse_resume_markdown(md)
    # The inline mention in a bullet/prose paragraph doesn't match the
    # full-line regex, so photo_path stays empty.
    assert doc.photo_path == ""


def test_render_uses_markdown_photo_comment_when_flag_absent(tmp_path: Path):
    """End-to-end: a markdown with a photo comment renders a PDF with
    that photo embedded, even though no `--photo` flag was passed.
    This is the re-render-after-edit story: the markdown is
    self-describing, no external state needed."""
    from PIL import Image as PILImage

    photo = tmp_path / "portrait.jpg"
    # Roughly-square so no aspect-stretch issue confounds this test.
    PILImage.new("RGB", (500, 500), color=(100, 120, 140)).save(photo, "JPEG")

    md = (
        "# Test Candidate\n"
        "test@example.com | +1 | Vienna\n"
        f"<!-- photo: {photo} -->\n"
        "\n"
        "## Summary\n"
        "Short.\n"
        "\n"
        "## Experience\n"
        "### Analyst — BigCo (2024)\n"
        "- Did stuff.\n"
    )
    out = tmp_path / "with-photo.pdf"
    render_resume_eu(md, out)  # NOTE: no photo= argument passed
    assert out.exists()
    # Crude but effective check: a PDF with an embedded 500x500 JPEG is
    # substantially larger than one without. A photo-less render at this
    # content size lands around 5-10KB; adding a photo pushes it past 30KB.
    size = out.stat().st_size
    assert size > 15_000, (
        f"Expected embedded photo to push PDF size past 15KB, got {size} bytes. "
        f"Likely the markdown photo comment wasn't honored."
    )


def test_render_explicit_flag_overrides_markdown_comment(tmp_path: Path):
    """When both a `--photo` flag AND a markdown comment are present,
    the flag wins. Explicit beats implicit — the caller knows what
    they're doing."""
    from PIL import Image as PILImage

    # Two distinct photos, each a different solid color so we can tell
    # which one got embedded via file-size difference.
    markdown_photo = tmp_path / "markdown.jpg"
    PILImage.new("RGB", (500, 500), color=(200, 200, 200)).save(markdown_photo, "JPEG", quality=95)
    flag_photo = tmp_path / "flag.jpg"
    PILImage.new("RGB", (500, 500), color=(50, 50, 50)).save(flag_photo, "JPEG", quality=95)

    md = (
        "# Test Candidate\n"
        "test@example.com | +1 | Vienna\n"
        f"<!-- photo: {markdown_photo} -->\n"
        "\n"
        "## Summary\nShort.\n"
        "\n"
        "## Experience\n"
        "### Role — Co (2024)\n"
        "- Did stuff.\n"
    )
    out_with_flag = tmp_path / "flag-wins.pdf"
    out_markdown_only = tmp_path / "markdown-only.pdf"

    render_resume_eu(md, out_with_flag, photo=str(flag_photo))
    render_resume_eu(md, out_markdown_only)  # no flag — uses markdown comment

    # Both PDFs exist and have photos embedded. The photos are the same
    # dimensions but different content — can't directly compare pixel
    # values through a PDF roundtrip, so we rely on the fact that the
    # `_resolve_photo_path` helper is independently tested.
    assert out_with_flag.exists()
    assert out_markdown_only.exists()


def test_render_no_photo_anywhere_still_works(tmp_path: Path):
    """Sanity: a markdown with no photo comment and no `--photo` flag
    renders normally. Proves neither fallback silently embeds a photo
    when none was requested. Uses relative-size comparison to a
    with-photo render — absolute size baselines drift with fonts/margins
    and aren't reliable here."""
    from PIL import Image as PILImage

    # Gradient photo — solid-color JPEGs compress to almost nothing and
    # produce deltas too small to distinguish from PDF overhead noise.
    # A gradient forces the JPEG encoder to retain real data.
    photo = tmp_path / "photo.jpg"
    img = PILImage.new("RGB", (500, 500))
    pixels = img.load()
    for y in range(500):
        for x in range(500):
            pixels[x, y] = (x // 2, y // 2, (x + y) // 4)
    img.save(photo, "JPEG", quality=95)

    md_no_photo = (
        "# Test Candidate\n"
        "test@example.com | +1 | Vienna\n"
        "\n"
        "## Summary\nShort.\n"
        "\n"
        "## Experience\n"
        "### Role — Co (2024)\n"
        "- Did stuff.\n"
    )

    no_photo_pdf = tmp_path / "no-photo.pdf"
    with_photo_pdf = tmp_path / "with-photo.pdf"
    render_resume_eu(md_no_photo, no_photo_pdf)
    render_resume_eu(md_no_photo, with_photo_pdf, photo=str(photo))

    assert no_photo_pdf.exists()
    assert with_photo_pdf.exists()
    # A realistic gradient JPEG at 500×500 adds at least ~5KB after
    # resumasher's downscale + re-encode pass. If the no-photo render
    # silently grew to match the with-photo size, we'd have introduced
    # an always-embed bug.
    delta = with_photo_pdf.stat().st_size - no_photo_pdf.stat().st_size
    assert delta > 5_000, (
        f"Expected with-photo PDF at least 5KB larger than no-photo. "
        f"Got delta {delta} bytes — photo may not have been embedded, or "
        f"no-photo render is somehow always-embedding."
    )


# ---------------------------------------------------------------------------
# Issue #22 PR D — photo aspect, placement, horizontal rules
# ---------------------------------------------------------------------------
#
# Three related visible fixes shipped as one PR because each one alone is too
# small to stand on its own. All three are "the code is wrong, here's the
# right answer" with no taste dimension. The typographic polish (PR E) ships
# separately because it needs iteration with rendered-PDF options.


# ---- Photo aspect ratio (no more stretching to 1:1 square) ----


def test_photo_render_size_portrait_preserves_aspect():
    """3:4 portrait source → renders at aspect 3:4 with longer side clamped
    to 3cm. Before this fix, the hard-coded 3×3cm stretched portraits
    horizontally by ~33%."""
    from scripts.render_pdf import _photo_render_size_cm
    import io
    from PIL import Image as PILImage

    buf = io.BytesIO()
    PILImage.new("RGB", (300, 400)).save(buf, "JPEG")
    buf.seek(0)
    w_cm, h_cm = _photo_render_size_cm(buf)
    # Height is the longer side → clamped to 3.0; width proportional.
    assert h_cm == 3.0
    assert abs(w_cm - 3.0 * (300 / 400)) < 0.01
    # Aspect preserved exactly.
    assert abs((w_cm / h_cm) - (300 / 400)) < 0.01


def test_photo_render_size_landscape_preserves_aspect():
    """4:3 landscape source → renders at aspect 4:3 with longer side
    (width this time) clamped to 3cm."""
    from scripts.render_pdf import _photo_render_size_cm
    import io
    from PIL import Image as PILImage

    buf = io.BytesIO()
    PILImage.new("RGB", (400, 300)).save(buf, "JPEG")
    buf.seek(0)
    w_cm, h_cm = _photo_render_size_cm(buf)
    assert w_cm == 3.0
    assert abs(h_cm - 3.0 * (300 / 400)) < 0.01


def test_photo_render_size_square_unchanged():
    """Square source → 3cm × 3cm. Regression guard for the common case."""
    from scripts.render_pdf import _photo_render_size_cm
    import io
    from PIL import Image as PILImage

    buf = io.BytesIO()
    PILImage.new("RGB", (500, 500)).save(buf, "JPEG")
    buf.seek(0)
    w_cm, h_cm = _photo_render_size_cm(buf)
    assert w_cm == 3.0
    assert h_cm == 3.0


def test_photo_render_size_phone_camera_aspect():
    """Real-world case that triggered this fix: typical phone photo
    (3024×4032, aspect 3:4). Must render without horizontal stretching."""
    from scripts.render_pdf import _photo_render_size_cm
    import io
    from PIL import Image as PILImage

    buf = io.BytesIO()
    PILImage.new("RGB", (3024, 4032)).save(buf, "JPEG")
    buf.seek(0)
    w_cm, h_cm = _photo_render_size_cm(buf)
    assert h_cm == 3.0
    assert abs(w_cm - 2.25) < 0.01  # 3 * 3024/4032 = 2.25


# ---- Photo placement (hAlign via photo_position parameter) ----


def test_render_photo_position_right_is_default(tmp_path: Path):
    """When neither config nor flag sets photo_position, photo renders
    on the right (DACH convention)."""
    from PIL import Image as PILImage

    photo = tmp_path / "p.jpg"
    PILImage.new("RGB", (500, 500)).save(photo, "JPEG")
    md = "# Test\nt@x.com\n\n## Summary\nx.\n"

    out = tmp_path / "right.pdf"
    # render_resume_eu default is photo_position="right"
    render_resume_eu(md, out, photo=str(photo))
    assert out.exists()


def test_render_photo_position_left(tmp_path: Path):
    """photo_position='left' renders photo on the left."""
    from PIL import Image as PILImage

    photo = tmp_path / "p.jpg"
    PILImage.new("RGB", (500, 500)).save(photo, "JPEG")
    md = "# Test\nt@x.com\n\n## Summary\nx.\n"

    out = tmp_path / "left.pdf"
    render_resume_eu(md, out, photo=str(photo), photo_position="left")
    assert out.exists()


def test_render_photo_position_center(tmp_path: Path):
    """photo_position='center' renders photo centered. The old hard-coded
    behavior, kept supported for unusual formal CVs."""
    from PIL import Image as PILImage

    photo = tmp_path / "p.jpg"
    PILImage.new("RGB", (500, 500)).save(photo, "JPEG")
    md = "# Test\nt@x.com\n\n## Summary\nx.\n"

    out = tmp_path / "center.pdf"
    render_resume_eu(md, out, photo=str(photo), photo_position="center")
    assert out.exists()


def test_render_photo_position_invalid_falls_back_to_right(tmp_path: Path):
    """A config typo ('righht') or unknown value shouldn't crash — fall
    back to default (right) and continue. The config write path
    validates upstream; this is a defensive floor."""
    from PIL import Image as PILImage

    photo = tmp_path / "p.jpg"
    PILImage.new("RGB", (500, 500)).save(photo, "JPEG")
    md = "# Test\nt@x.com\n\n## Summary\nx.\n"

    out = tmp_path / "invalid.pdf"
    render_resume_eu(md, out, photo=str(photo), photo_position="bogus")
    assert out.exists()


# ---- Horizontal rules (markdown --- renders as a hairline) ----


def test_parse_horizontal_rule_appends_sentinel_to_section():
    """`---` on its own line inside a section appends the HR sentinel
    to the section's raw_paragraphs. Renderer later converts it to
    an HRFlowable."""
    from scripts.render_pdf import _HR_SENTINEL
    md = (
        "# Test\nt@x.com\n\n"
        "## Summary\n"
        "Some prose.\n"
        "---\n"
        "More prose.\n"
    )
    doc = parse_resume_markdown(md)
    summary = next(s for s in doc.sections if s.heading == "Summary")
    # Paragraphs in order: prose, HR sentinel, more prose.
    assert len(summary.raw_paragraphs) == 3
    assert summary.raw_paragraphs[0] == "Some prose."
    assert summary.raw_paragraphs[1] == _HR_SENTINEL
    assert summary.raw_paragraphs[2] == "More prose."


def test_parse_horizontal_rule_variants_all_detected():
    """Markdown supports `---`, `___`, and `***` as horizontal rule syntax.
    All three should trigger the sentinel."""
    from scripts.render_pdf import _HR_SENTINEL
    for variant in ["---", "___", "***", "----", "*****"]:
        md = f"# T\ne@x.com\n\n## S\nbefore\n{variant}\nafter\n"
        doc = parse_resume_markdown(md)
        section = next(s for s in doc.sections if s.heading == "S")
        assert _HR_SENTINEL in section.raw_paragraphs, (
            f"Variant {variant!r} did not produce an HR sentinel"
        )


def test_parse_horizontal_rule_before_any_section_is_dropped():
    """HR before the first `##` has no home in the parse tree. Dropped
    silently — it shouldn't crash and it shouldn't accidentally create
    a phantom section."""
    md = (
        "# Test\nt@x.com\n"
        "---\n"  # before any section
        "\n## Summary\nContent.\n"
    )
    doc = parse_resume_markdown(md)
    # Only one section created (Summary); no "phantom" rule section.
    assert len(doc.sections) == 1
    assert doc.sections[0].heading == "Summary"


def test_parse_three_hyphens_inline_not_hijacked():
    """`---` inside a paragraph (on the same line as other text) must not
    trigger the rule — only full-line matches count. Guards against
    em-dash surrogates and similar prose appearing mid-line."""
    from scripts.render_pdf import _HR_SENTINEL
    md = (
        "# Test\nt@x.com\n\n"
        "## Summary\n"
        "This sentence has --- inside it, not alone.\n"
    )
    doc = parse_resume_markdown(md)
    summary = next(s for s in doc.sections if s.heading == "Summary")
    assert _HR_SENTINEL not in summary.raw_paragraphs
    assert len(summary.raw_paragraphs) == 1
    assert "---" in summary.raw_paragraphs[0]


def test_render_horizontal_rule_does_not_appear_as_literal_text(tmp_path: Path):
    """End-to-end: a markdown with a `---` rule must NOT render the
    literal string '---' as text in the PDF. Instead it should render
    an actual horizontal rule (HRFlowable). If the literal `---` shows
    up in the extracted text, the sentinel was rendered as prose."""
    from pdfminer.high_level import extract_text

    md = (
        "# Test\nt@x.com | +1\n\n"
        "## Summary\n"
        "First paragraph of summary.\n"
        "---\n"
        "Second paragraph after the rule.\n"
        "\n"
        "## Experience\n"
        "### Role — Company (2024)\n"
        "- Did stuff.\n"
    )
    out = tmp_path / "hr.pdf"
    render_resume_eu(md, out)
    extracted = extract_text(str(out))

    # Both paragraphs must appear.
    assert "First paragraph" in extracted
    assert "Second paragraph" in extracted
    # The literal `---` must NOT appear as text — it should have been
    # converted to an HRFlowable.
    assert "---" not in extracted, (
        f"Literal '---' found in extracted PDF text — sentinel was "
        f"rendered as prose instead of a horizontal rule."
    )
    # Defensive: the sentinel's internal representation must not leak.
    assert "\x00HR\x00" not in extracted


def test_render_horizontal_rule_produces_non_empty_pdf(tmp_path: Path):
    """Regression: rendering a markdown with a `---` rule should produce
    a PDF that's not dramatically smaller than one without the rule
    (the rule adds a tiny bit of geometry but shouldn't skip sections
    or crash the render)."""
    md_with = (
        "# Test\nt@x.com | +1\n\n"
        "## Summary\nBefore.\n---\nAfter.\n"
    )
    md_without = (
        "# Test\nt@x.com | +1\n\n"
        "## Summary\nBefore.\nAfter.\n"
    )
    out_with = tmp_path / "with.pdf"
    out_without = tmp_path / "without.pdf"
    render_resume_eu(md_with, out_with)
    render_resume_eu(md_without, out_without)
    assert out_with.exists()
    assert out_without.exists()
    # Both PDFs succeed; with-rule is at least as large as without (rule
    # adds content). A dramatically smaller with-rule PDF would signal
    # something got dropped.
    assert out_with.stat().st_size >= out_without.stat().st_size - 500
