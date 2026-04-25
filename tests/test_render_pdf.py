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
    _build_styles,
    _linkify_contact,
    _linkify_text,
    _render_titled_block,
    _split_title_and_date,
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
    # Pre-#42 this anchored on "Meta (July 2017" — the contiguous form
    # before block-title-with-date got split into title + metadata
    # paragraphs. After #42 the parens-segment is on its own line, so we
    # anchor on bare "Meta" (still unique in the fixture).
    meta = pos("Meta")
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


# ---------------------------------------------------------------------------
# Issue #42: stacked dates under block titles.
#
# Block / sub-block titles that contain a recognizable date segment now
# render as two paragraphs (title above, date below in a slightly smaller
# regular-weight metadata style) instead of one. Single-flow text — no
# Tables, no tab stops — so commercial ATS parsers (Workday, Greenhouse,
# Taleo, iCIMS) read the date in document order rather than potentially
# scrambling a 2-column layout.
# ---------------------------------------------------------------------------


def test_split_parenthesized_month_year_range():
    title = "Senior Analyst — Deloitte (Aug 2022 – Aug 2025)"
    t, d = _split_title_and_date(title)
    assert t == "Senior Analyst — Deloitte"
    assert d == "Aug 2022 – Aug 2025"


def test_split_year_range_pipe_separated():
    title = "Senior Analyst | 2022–2025 | Deloitte"
    t, d = _split_title_and_date(title)
    assert t == "Senior Analyst | Deloitte"
    assert d == "2022–2025"


def test_split_present():
    title = "Director (Aug 2022 – Present)"
    t, d = _split_title_and_date(title)
    assert t == "Director"
    assert d == "Aug 2022 – Present"


def test_split_isolated_month_year():
    title = "Project X (Feb 2026)"
    t, d = _split_title_and_date(title)
    assert t == "Project X"
    assert d == "Feb 2026"


def test_split_no_date_returns_none():
    title = "Senior Analyst"
    t, d = _split_title_and_date(title)
    assert t == "Senior Analyst"
    assert d is None


def test_split_bare_year_in_prose_not_matched():
    """'2024 Economic Survey' is a publication title, not a date. Bare
    years in prose (not in parens or pipes) must NOT be hijacked as date
    segments — that would put nonsense on the metadata line."""
    title = "2024 Economic Survey — Statistics Austria"
    t, d = _split_title_and_date(title)
    assert d is None
    assert t == title


def test_render_titled_block_no_date_returns_single_paragraph():
    """Regression guard: titles without dates must still render as a
    single paragraph, identical to pre-#42 behavior. Anything else is a
    behavior change for existing users."""
    styles = _build_styles()
    flowables = _render_titled_block(
        "Senior Analyst",
        styles["BlockTitle"],
        styles["BlockMetadata"],
    )
    assert len(flowables) == 1


def test_render_titled_block_with_date_returns_two_paragraphs():
    styles = _build_styles()
    flowables = _render_titled_block(
        "Senior Analyst — Deloitte (Aug 2022 – Aug 2025)",
        styles["BlockTitle"],
        styles["BlockMetadata"],
    )
    assert len(flowables) == 2


def test_sub_block_title_also_gets_split():
    """Sub-block titles get the same date-split treatment, with the
    metadata line indented to match the sub-block title's leftIndent
    so the date sits visually under its title rather than under the
    parent block."""
    styles = _build_styles()
    flowables = _render_titled_block(
        "Senior Director (Aug 2022 – Aug 2025)",
        styles["SubBlockTitle"],
        styles["SubBlockMetadata"],
    )
    assert len(flowables) == 2
    # The metadata paragraph must use the indented sub-block style so the
    # date aligns under its title, not under the parent company heading.
    metadata_para = flowables[1]
    assert metadata_para.style.leftIndent == 8


def test_ats_round_trip_dates_still_extractable_after_split(tmp_path: Path):
    """The whole point of #42: dates render as single-flow text that ATS
    parsers extract in document order. If the rendered PDF doesn't
    contain the date string in extractable form after the split, we've
    broken ATS safety — which would defeat the entire reason for the
    stacked-line approach over the Table approach we rejected."""
    md = (
        "# Test Person\n"
        "test@example.com | +1 555 0000 | linkedin.com/in/test | Vienna, Austria\n"
        "\n"
        "## Experience\n"
        "### Senior Analyst — Deloitte (Aug 2022 – Aug 2025)\n"
        "- Some bullet.\n"
        "### Teaching Assistant — School (2024-2025)\n"
        "- Another bullet.\n"
    )
    out = tmp_path / "stacked.pdf"
    render_resume_eu(md, out)
    assert_ats_roundtrip(str(out), [
        "Aug 2022 – Aug 2025",
        "2024-2025",
        "Senior Analyst",
        "Deloitte",
        "Teaching Assistant",
    ])


# ---------------------------------------------------------------------------
# Issue #42 correctness fixes: sub-block bullet indent + section separation.
#
# The stacked-date layout exposed two pre-existing layout issues that made
# multi-role tenures actively uglier than before-#42:
#   (1) Bullets under sub-blocks rendered with bullet-character to the LEFT
#       of the sub-block content above them (Bullet.bulletIndent=4 vs
#       SubBlockTitle.leftIndent=8). The new metadata line directly above
#       the bullet made the misalignment impossible to miss.
#   (2) SectionHeading.spaceBefore=10 was tight before; with blocks
#       gaining a metadata line, the heading-to-content rhythm felt
#       proportionally compressed and sections read as one continuous
#       chunk.
#
# Bundled with #42 because shipping the date-split alone would have been
# a regression. Correctness, not scope creep.
# ---------------------------------------------------------------------------


def test_sub_block_bullet_indent_is_past_sub_block_content():
    """SubBlockBullet must indent past SubBlockTitle.leftIndent so the
    bullet character renders to the RIGHT of (or aligned with) the
    sub-block title and metadata above it, not to the left of them."""
    styles = _build_styles()
    sub_title_indent = styles["SubBlockTitle"].leftIndent
    sub_bullet_left = styles["SubBlockBullet"].leftIndent
    sub_bullet_char = styles["SubBlockBullet"].bulletIndent
    # Both the bullet character position and the bullet text position
    # must be ≥ the sub-block title's indent. Otherwise the visual
    # hierarchy reads as "bullet outdents past its parent."
    assert sub_bullet_char >= sub_title_indent, (
        f"SubBlockBullet.bulletIndent ({sub_bullet_char}) must be ≥ "
        f"SubBlockTitle.leftIndent ({sub_title_indent}) so the bullet "
        f"character doesn't render to the left of the sub-block content."
    )
    assert sub_bullet_left >= sub_title_indent, (
        f"SubBlockBullet.leftIndent ({sub_bullet_left}) must be ≥ "
        f"SubBlockTitle.leftIndent ({sub_title_indent})."
    )


def test_top_level_bullet_indent_unchanged_regression_guard():
    """Regression guard: bullets at section level or directly under a
    block (not a sub-block) must still use the original Bullet style.
    Changing top-level bullet indent would shift every existing resume's
    layout — out of scope for #42."""
    styles = _build_styles()
    assert styles["Bullet"].leftIndent == 14
    assert styles["Bullet"].bulletIndent == 4


def test_section_heading_space_before_visibly_separates_sections():
    """SectionHeading.spaceBefore must be large enough that sections
    read as separate. 10pt was OK before #42's metadata lines added
    vertical density to each block; bumped to 14 to restore the
    proportional rhythm. Anything below ~12 starts to look cramped on
    real resumes."""
    styles = _build_styles()
    assert styles["SectionHeading"].spaceBefore >= 12, (
        f"SectionHeading.spaceBefore is {styles['SectionHeading'].spaceBefore}pt; "
        f"sections need ≥12pt above to read as visually separate after "
        f"#42's metadata lines added vertical density to blocks."
    )


def test_section_heading_followed_by_divider_in_resume(tmp_path: Path):
    """Each section heading must be followed by a horizontal rule
    (`HRFlowable`) so sections read as visually separated structural
    blocks rather than a wall of stacked text. The rule is the
    section-divider variant (thicker, darker than the soft in-content
    `---` markdown rule); `_section_divider` controls the params."""
    from reportlab.platypus.flowables import HRFlowable
    from scripts.render_pdf import (
        _build_resume_flowables,
        _section_divider,
        _section_order_eu,
    )
    md = (
        "# Test\n"
        "t@x.com | +1\n"
        "\n"
        "## Summary\n"
        "One sentence.\n"
        "\n"
        "## Experience\n"
        "### Role — Co (2024)\n"
        "- bullet\n"
        "\n"
        "## Education\n"
        "### Degree — School (2020-2022)\n"
        "- bullet\n"
    )
    doc = parse_resume_markdown(md)
    flow = _build_resume_flowables(
        doc,
        _build_styles(),
        section_order_fn=_section_order_eu,
        center_header=False,
        photo_path=None,
    )
    # Every SectionHeading paragraph must be immediately followed by an
    # HRFlowable. Walk pairs and assert.
    section_headings_seen = 0
    for i, item in enumerate(flow):
        # SectionHeading paragraphs carry the SectionHeading style.
        from reportlab.platypus import Paragraph
        if isinstance(item, Paragraph) and getattr(item.style, "name", None) == "SectionHeading":
            section_headings_seen += 1
            assert i + 1 < len(flow), (
                f"SectionHeading at end of flow with no divider after"
            )
            nxt = flow[i + 1]
            assert isinstance(nxt, HRFlowable), (
                f"SectionHeading at index {i} not followed by HRFlowable; "
                f"got {type(nxt).__name__}"
            )
    assert section_headings_seen >= 3, (
        f"Expected ≥3 sections in fixture, saw {section_headings_seen}"
    )


def test_section_divider_distinct_from_in_content_hr_rule():
    """The section divider (under each heading) and the in-content `---`
    rule serve different roles and should be visually distinguishable.
    Section dividers are slightly thicker and darker so they read as
    structural; in-content rules are softer to read as soft breaks."""
    from scripts.render_pdf import _section_divider
    div = _section_divider()
    # Section divider params: thicker than 0.5 (the in-content default)
    # and darker than #888888 (also in-content default). Specific values
    # are taste, but the inequality must hold.
    assert div.lineWidth >= 0.6, (
        f"Section divider thickness {div.lineWidth} should be ≥ 0.6 "
        f"(thicker than the in-content `---` rule)"
    )


def test_section_divider_does_not_break_ats_round_trip(tmp_path: Path):
    """HRFlowable produces no text in the PDF text layer, so adding a
    rule under each section heading must not affect ATS extraction.
    Regression guard: if reportlab ever changes HRFlowable to emit text
    or alt-text, this catches it."""
    md = (
        "# Test Person\n"
        "test@example.com | +1 555 0000 | linkedin.com/in/test | Vienna, Austria\n"
        "\n"
        "## Summary\n"
        "Short summary line.\n"
        "\n"
        "## Experience\n"
        "### Senior Analyst — Deloitte (Aug 2022 – Aug 2025)\n"
        "- A bullet about impact.\n"
        "\n"
        "## Skills\n"
        "- Python, R, SQL\n"
    )
    out = tmp_path / "with_dividers.pdf"
    render_resume_eu(md, out)
    assert_ats_roundtrip(str(out), [
        "Test Person",
        "Summary",
        "Short summary line",
        "Experience",
        "Senior Analyst",
        "Aug 2022 – Aug 2025",
        "A bullet about impact",
        "Skills",
        "Python, R, SQL",
    ])


def test_render_sub_block_bullets_extract_in_order(tmp_path: Path):
    """End-to-end: render a multi-role resume and assert the new bullet
    indent doesn't break ATS round-trip. Bullet text still appears in
    document order — just with deeper visual indent."""
    md = (
        "# Multi Role\n"
        "mr@example.com | +1 555 0000 | linkedin.com/in/mr | City\n"
        "\n"
        "## Experience\n"
        "### Big Co (2020 – Present)\n"
        "**Senior Director** (2023 – Present)\n"
        "- Led the org through hypergrowth.\n"
        "**Director** (2020 – 2023)\n"
        "- Built the team from scratch.\n"
    )
    out = tmp_path / "multi.pdf"
    render_resume_eu(md, out)
    assert_ats_roundtrip(str(out), [
        "Big Co",
        "2020 – Present",
        "Senior Director",
        "2023 – Present",
        "Led the org through hypergrowth",
        "Director",
        "2020 – 2023",
        "Built the team from scratch",
    ])


# ---------------------------------------------------------------------------
# Issue #42 polish tweaks: softer metadata color, block spacing,
# softer contact line, clickable links in contact line.
# ---------------------------------------------------------------------------


def _hex_to_avg_channel(hex_color: str) -> float:
    """`#555555` → 0.333 (each channel 0x55=85, 85/255≈0.333). Used to
    bound textColor values to a readable-but-soft range."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return (r + g + b) / (3 * 255)


def test_block_metadata_uses_softer_gray_color():
    """Date / location lines should recede as metadata. The exact hex
    is taste, but it must not be body-black or near-black — those
    compete with body text for hierarchy. reportlab stores textColor
    as the original string assigned (no Color coercion at style-build
    time), so we compare hex strings directly."""
    styles = _build_styles()
    hex_color = styles["BlockMetadata"].textColor
    # Must be a hex color string, not body-black.
    assert isinstance(hex_color, str) and hex_color.startswith("#"), (
        f"BlockMetadata.textColor expected to be a '#rrggbb' string, "
        f"got {hex_color!r}"
    )
    assert hex_color.lower() not in ("#000000", "#111111", "#222222"), (
        f"BlockMetadata.textColor is {hex_color}; needs to be lighter "
        f"than near-black so the metadata line recedes."
    )
    # Bound to a readable-but-soft range. Below ~0.2 reads as black;
    # above ~0.6 reads as nearly invisible on white.
    avg = _hex_to_avg_channel(hex_color)
    assert 0.2 < avg < 0.6, (
        f"BlockMetadata color {hex_color} (avg channel {avg:.2f}) "
        f"is outside the readable-but-soft range (0.2–0.6)"
    )


def test_sub_block_metadata_matches_block_metadata_color():
    """SubBlockMetadata should match BlockMetadata's color so the
    metadata-line treatment is uniform across both depths."""
    styles = _build_styles()
    assert styles["SubBlockMetadata"].textColor == \
           styles["BlockMetadata"].textColor


def test_contact_style_softer_and_smaller_than_body():
    """Contact line should recede into header territory. Smaller
    fontSize than Body and a softer-than-black color."""
    styles = _build_styles()
    assert styles["Contact"].fontSize < styles["Body"].fontSize, (
        f"Contact ({styles['Contact'].fontSize}pt) should be smaller "
        f"than Body ({styles['Body'].fontSize}pt)"
    )
    color_hex = styles["Contact"].textColor
    assert color_hex.lower() != "#000000", (
        f"Contact.textColor is {color_hex}; should be softer than "
        f"body-black to recede as header"
    )


def test_subhead_center_matches_contact():
    """SubheadCenter (US-style centered contact line) must match the
    Contact style's softer treatment so US and EU resumes look
    consistent at the header level."""
    styles = _build_styles()
    assert styles["SubheadCenter"].fontSize == styles["Contact"].fontSize
    assert styles["SubheadCenter"].textColor == styles["Contact"].textColor


def test_block_title_space_before_increased_for_block_separation():
    """BlockTitle.spaceBefore controls the gap between consecutive
    blocks within a section (e.g., Meta → Chief Data Scientist →
    Volunteer Translator). Pre-#42 polish was 4pt, which read tight;
    bumped to 6+ for natural breathing room between jobs."""
    styles = _build_styles()
    assert styles["BlockTitle"].spaceBefore >= 6, (
        f"BlockTitle.spaceBefore is {styles['BlockTitle'].spaceBefore}pt; "
        f"needs ≥6pt for blocks-within-a-section to feel separated."
    )


def test_linkify_email_wraps_in_mailto():
    out = _linkify_contact("ana.muller@example.com")
    assert '<a href="mailto:ana.muller@example.com">' in out
    assert "ana.muller@example.com</a>" in out


def test_linkify_linkedin_bare_domain_gets_https_prefix():
    """Students typically write `linkedin.com/in/X` without `https://`.
    Linkify must add the scheme to the href while keeping the displayed
    text bare-domain."""
    out = _linkify_contact("linkedin.com/in/anamuller")
    assert '<a href="https://linkedin.com/in/anamuller">' in out
    assert "linkedin.com/in/anamuller</a>" in out
    # Display text is the bare-domain form; the scheme is href-only.
    assert "https://linkedin" not in out.split("</a>")[0].split(">")[-1]


def test_linkify_github_bare_domain_gets_https_prefix():
    out = _linkify_contact("github.com/anamuller")
    assert '<a href="https://github.com/anamuller">' in out
    assert "github.com/anamuller</a>" in out


def test_linkify_explicit_https_url_preserved_verbatim():
    out = _linkify_contact("https://my-portfolio.example.com")
    assert '<a href="https://my-portfolio.example.com">' in out


def test_linkify_phone_and_location_pass_through_as_text():
    """Phone numbers and location text must not get linkified — they're
    not URIs and trying to mailto/tel them would be wrong."""
    text = "+43 664 1234567 | Vienna, Austria"
    out = _linkify_contact(text)
    assert "<a href" not in out, (
        f"Phone / location should not be wrapped in <a>: {out!r}"
    )
    # Text content preserved (HTML escape doesn't change ASCII chars).
    assert "+43 664 1234567" in out
    assert "Vienna, Austria" in out


def test_linkify_full_contact_line_mixes_links_and_text():
    """End-to-end: a typical contact line with email + phone + linkedin
    + location produces wrapped links for the URI parts and plain text
    for the rest, with pipe separators preserved."""
    text = "ana@example.com | +43 664 1234567 | linkedin.com/in/anamuller | Vienna, Austria"
    out = _linkify_contact(text)
    assert '<a href="mailto:ana@example.com">' in out
    assert '<a href="https://linkedin.com/in/anamuller">' in out
    assert "+43 664 1234567" in out  # untouched phone
    assert "Vienna, Austria" in out  # untouched location
    assert " | " in out  # separators preserved


def test_linkify_no_link_color_attribute_inherits_paragraph_color():
    """Modern resume convention: links inherit the surrounding text
    color rather than rendering as 1990s-blue underlined. The link
    annotation makes them clickable in the PDF; visual styling stays
    subtle. So our `<a>` tags must NOT carry an explicit `color="...".`"""
    out = _linkify_contact("ana@example.com")
    # `<a href="mailto:..."` only — no color attribute.
    assert 'color=' not in out, (
        f"Link tag should not carry an explicit color attribute (so it "
        f"inherits the paragraph's textColor); got: {out!r}"
    )


def test_rendered_pdf_actually_contains_clickable_link_annotations(tmp_path: Path):
    """End-to-end load-bearing test: the `<a href="...">` markup we
    emit must round-trip through reportlab into PDF Link annotations
    that PDF readers will actually open. If reportlab silently drops
    the annotation (or pdfminer can no longer find it), the
    "clickable link" promise quietly stops being true."""
    md = (
        "# Linkable Person\n"
        "test.linker@example.com | +1 555 0000 | linkedin.com/in/linker | github.com/linker | City\n"
        "\n"
        "## Summary\n"
        "Has links.\n"
    )
    out = tmp_path / "linked.pdf"
    render_resume_eu(md, out)

    # Walk the PDF and pull every Link annotation's URI.
    from pdfminer.pdfparser import PDFParser
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfpage import PDFPage

    uris: list[str] = []
    with open(out, "rb") as fp:
        parser_ = PDFParser(fp)
        pdf_doc = PDFDocument(parser_)
        for page in PDFPage.create_pages(pdf_doc):
            if not page.annots:
                continue
            for annot_ref in page.annots:
                annot = annot_ref.resolve()
                subtype = annot.get("Subtype")
                if subtype is None or subtype.name != "Link":
                    continue
                action = annot.get("A")
                if action is None:
                    continue
                action_resolved = (
                    action.resolve() if hasattr(action, "resolve") else action
                )
                uri = action_resolved.get("URI")
                if uri is not None:
                    uris.append(uri.decode("utf-8") if isinstance(uri, bytes) else uri)

    assert "mailto:test.linker@example.com" in uris, (
        f"Email link missing from PDF Link annotations. Got: {uris}"
    )
    assert "https://linkedin.com/in/linker" in uris, (
        f"LinkedIn link missing from PDF Link annotations. Got: {uris}"
    )
    assert "https://github.com/linker" in uris, (
        f"GitHub link missing from PDF Link annotations. Got: {uris}"
    )


def test_render_resume_eu_with_linked_contact_round_trips_for_ats(tmp_path: Path):
    """End-to-end ATS guard: even with link annotations in the contact
    line, pdfminer must extract the email, linkedin URL, and location
    text in order. PDF link annotations are a metadata layer — they
    don't replace the underlying text stream."""
    md = (
        "# Test Person\n"
        "test@example.com | +1 555 0000 | linkedin.com/in/test | Vienna, Austria\n"
        "\n"
        "## Summary\n"
        "Tested.\n"
    )
    out = tmp_path / "linked.pdf"
    render_resume_eu(md, out)
    assert_ats_roundtrip(str(out), [
        "Test Person",
        "test@example.com",
        "+1 555 0000",
        "linkedin.com/in/test",
        "Vienna, Austria",
    ])


# ---------------------------------------------------------------------------
# Issue #42 final fixes: body-wide URL linkification + KeepTogether
# granularity. Surfaced by real-run testing of the feature branch:
# (a) URLs in project titles ("Resumasher (github.com/earino/resumasher)")
#     and bullet text rendered as plain non-clickable text — only the
#     contact line was linkified.
# (b) Multi-sub-block tenures wrapped in a single big KeepTogether forced
#     reportlab to page-break BEFORE the whole block when it didn't fit
#     in the remaining page space, leaving the bottom of the page blank.
# ---------------------------------------------------------------------------


def test_linkify_text_alias_for_linkify_contact():
    """`_linkify_text` is the new general-purpose name; `_linkify_contact`
    is kept as a backwards-compatible alias. They must produce identical
    output."""
    text = "ana@example.com | linkedin.com/in/ana"
    assert _linkify_text(text) == _linkify_contact(text)


def test_linkify_text_does_not_swallow_trailing_backtick():
    """Real-run regression: tailor LLM sometimes wraps URLs in markdown
    code spans (`github.com/foo`). Pre-fix the regex greedily consumed
    the closing backtick into the URL match, producing href values like
    `https://github.com/foo\\`` that PDF readers can't open. Backtick
    must stay as non-link text after the closing `</a>`."""
    out = _linkify_text("see `github.com/me/foo`).")
    # The href must not contain a backtick.
    assert "github.com/me/foo`" not in out.split('"')[1], (
        f"Backtick leaked into href attribute: {out!r}"
    )
    # The closing backtick must appear in the rendered output as text.
    assert "</a>`)" in out


def test_linkify_text_handles_url_inside_parens():
    """Project titles like 'Resumasher (github.com/earino/resumasher)'
    must linkify the URL but NOT swallow the closing paren — the regex
    excludes ')' from URL paths so the trailing paren stays as text."""
    out = _linkify_text("Resumasher (github.com/earino/resumasher)")
    assert '<a href="https://github.com/earino/resumasher">' in out
    # Closing paren must NOT be inside the href.
    assert '<a href="https://github.com/earino/resumasher)">' not in out
    # And the closing paren must appear in the rendered output (as text).
    assert "</a>)" in out


def test_linkify_text_in_bullet_with_url():
    """Bullets that mention a URL should produce wrapped output with the
    URL clickable and the surrounding prose still readable."""
    out = _linkify_text("Built it; see https://example.com/docs for details")
    assert '<a href="https://example.com/docs">' in out
    assert "Built it; see " in out
    assert " for details" in out


def test_linkify_text_preserves_bold_markers():
    """`_linkify_text` supersets `_escape`, so `**bold**` markdown still
    becomes `<b>bold</b>` (via _escape on non-link parts)."""
    out = _linkify_text("**Senior Director** at github.com/me")
    assert "<b>Senior Director</b>" in out
    assert '<a href="https://github.com/me">' in out


def test_linkify_text_no_url_passes_through_as_escape():
    """Plain text without URLs round-trips identically to `_escape`."""
    text = "Just some prose with <html> & ampersand"
    from scripts.render_pdf import _escape
    assert _linkify_text(text) == _escape(text)


def test_render_resume_with_project_url_in_title_has_clickable_annotation(tmp_path: Path):
    """End-to-end: a project block titled with a github URL produces a
    PDF Link annotation that PDF readers actually open. Lock-in test for
    the body-wide linkification feature."""
    md = (
        "# Test Person\n"
        "test@example.com | linkedin.com/in/test | City\n"
        "\n"
        "## Projects\n"
        "### Resumasher (github.com/earino/resumasher)\n"
        "- A Claude Code skill for tailoring resumes.\n"
        "- See https://example.com/docs for the API.\n"
    )
    out = tmp_path / "with_project_link.pdf"
    render_resume_eu(md, out)

    from pdfminer.pdfparser import PDFParser
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfpage import PDFPage

    uris: list[str] = []
    with open(out, "rb") as fp:
        parser_ = PDFParser(fp)
        pdf_doc = PDFDocument(parser_)
        for page in PDFPage.create_pages(pdf_doc):
            if not page.annots:
                continue
            for annot_ref in page.annots:
                annot = annot_ref.resolve()
                subtype = annot.get("Subtype")
                if subtype is None or subtype.name != "Link":
                    continue
                action = annot.get("A")
                if action is None:
                    continue
                action_resolved = (
                    action.resolve() if hasattr(action, "resolve") else action
                )
                uri = action_resolved.get("URI")
                if uri is not None:
                    uris.append(uri.decode("utf-8") if isinstance(uri, bytes) else uri)

    assert "https://github.com/earino/resumasher" in uris, (
        f"Project-title URL missing from PDF Link annotations. Got: {uris}"
    )
    assert "https://example.com/docs" in uris, (
        f"Bullet URL missing from PDF Link annotations. Got: {uris}"
    )


def test_keep_together_per_sub_block_not_per_block(tmp_path: Path):
    """A multi-role block must produce MULTIPLE KeepTogether flowables —
    one for the block title (+ direct bullets) and one for each sub-block
    — instead of one giant KeepTogether wrapping the whole thing.

    Why this matters: when the entire block doesn't fit in the remaining
    page space, the giant-KeepTogether shape forces reportlab to page-
    break BEFORE the block, leaving page 1's bottom half blank. Per-sub-
    block KeepTogether lets reportlab break between sub-blocks, which is
    the natural typesetting boundary.
    """
    from reportlab.platypus import KeepTogether
    from scripts.render_pdf import _build_resume_flowables, _section_order_eu

    md = (
        "# Multi Role\n"
        "mr@example.com | City\n"
        "\n"
        "## Experience\n"
        "### Big Co (2020 – Present)\n"
        "**Senior Director** (2023 – Present)\n"
        "- Led the org through hypergrowth.\n"
        "**Director** (2021 – 2023)\n"
        "- Built the team from scratch.\n"
        "**Manager** (2020 – 2021)\n"
        "- Started as the first engineer.\n"
    )
    doc = parse_resume_markdown(md)
    flow = _build_resume_flowables(
        doc,
        _build_styles(),
        section_order_fn=_section_order_eu,
        center_header=False,
        photo_path=None,
    )
    # Count KeepTogether flowables. Pre-fix: exactly 1 (per block).
    # Post-fix: 4 (1 block-title group + 3 sub-block groups).
    keep_togethers = [f for f in flow if isinstance(f, KeepTogether)]
    assert len(keep_togethers) >= 4, (
        f"Expected ≥4 KeepTogether flowables (1 per block-title + 1 per "
        f"sub-block), got {len(keep_togethers)}. Pre-fix shape was 1 big "
        f"KeepTogether per block, which causes reportlab to leave the "
        f"bottom of a page blank when the block doesn't fit."
    )


def test_keep_together_block_with_no_sub_blocks_still_emits_one_group(tmp_path: Path):
    """Regression guard: blocks without sub-blocks (single-role jobs,
    education entries, projects) still emit exactly one KeepTogether
    each. The new granularity must not over-fragment."""
    from reportlab.platypus import KeepTogether
    from scripts.render_pdf import _build_resume_flowables, _section_order_eu

    md = (
        "# Single Role\n"
        "sr@example.com | City\n"
        "\n"
        "## Experience\n"
        "### Senior Analyst — Foo Co (2022 – 2024)\n"
        "- Did stuff.\n"
        "- Did other stuff.\n"
        "### Junior Analyst — Bar Co (2020 – 2022)\n"
        "- More stuff.\n"
    )
    doc = parse_resume_markdown(md)
    flow = _build_resume_flowables(
        doc,
        _build_styles(),
        section_order_fn=_section_order_eu,
        center_header=False,
        photo_path=None,
    )
    keep_togethers = [f for f in flow if isinstance(f, KeepTogether)]
    # Two blocks → two KeepTogether flowables. (Sub-block-less blocks
    # don't emit extra KT groups.)
    assert len(keep_togethers) == 2, (
        f"Expected 2 KeepTogether flowables (1 per block, no sub-blocks), "
        f"got {len(keep_togethers)}"
    )


def test_keep_together_sub_block_glues_sub_title_to_its_bullets(tmp_path: Path):
    """The whole point of KeepTogether is to prevent orphaning a title
    at the bottom of a page with its content on the next page. After
    the granularity change, each sub-block's title must still be glued
    to its own bullets."""
    from reportlab.platypus import KeepTogether, Paragraph
    from scripts.render_pdf import _build_resume_flowables, _section_order_eu

    md = (
        "# Test\n"
        "t@x.com | City\n"
        "\n"
        "## Experience\n"
        "### Big Co (2020 – Present)\n"
        "**Senior Director** (2023 – Present)\n"
        "- First bullet.\n"
        "- Second bullet.\n"
    )
    doc = parse_resume_markdown(md)
    flow = _build_resume_flowables(
        doc,
        _build_styles(),
        section_order_fn=_section_order_eu,
        center_header=False,
        photo_path=None,
    )
    keep_togethers = [f for f in flow if isinstance(f, KeepTogether)]
    # Find the sub-block KT (the second one — the first is the block
    # title group).
    assert len(keep_togethers) >= 2
    sub_kt = keep_togethers[1]
    contained = sub_kt._content
    # Sub-block group should have: SubBlockTitle + SubBlockMetadata +
    # at least one SubBlockBullet paragraph.
    style_names = [
        getattr(item.style, "name", None)
        for item in contained
        if isinstance(item, Paragraph)
    ]
    assert "SubBlockTitle" in style_names
    assert "SubBlockBullet" in style_names, (
        f"Sub-block KeepTogether must contain SubBlockBullet paragraphs "
        f"(otherwise the sub-title can orphan from its bullets at a page "
        f"break). Got styles: {style_names}"
    )
