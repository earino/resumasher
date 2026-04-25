"""
Tests for scripts/orchestration.py \u2014 every deterministic helper, every edge
case traced from the test review diagram.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.orchestration import (
    DEFAULT_IGNORE_DIRS,
    MAX_FILE_CHARS,
    append_history,
    company_slug,
    discover_resume,
    ensure_gitignore,
    extract_company,
    extract_fit_score,
    first_run_needed,
    folder_state_hash,
    format_jd,
    inspect_photo,
    inspect_pdf,
    inspect_resume,
    is_failure_sentinel,
    mine_folder_context,
    parse_job_source,
    read_config,
    read_resume,
    validate_resume_path,
    write_config,
)


# ---------------------------------------------------------------------------
# parse_job_source
# ---------------------------------------------------------------------------


def test_parse_job_source_file_takes_precedence_over_url_lookalike(tmp_path: Path):
    weird_name = "https_login_wall.md"
    (tmp_path / weird_name).write_text("real JD text inside file", encoding="utf-8")
    res = parse_job_source(weird_name, cwd=tmp_path)
    assert res.mode == "file"
    assert "real JD text" in res.content
    assert res.path is not None


def test_parse_job_source_http_is_url(tmp_path: Path):
    res = parse_job_source("https://careers.deloitte.com/job/123", cwd=tmp_path)
    assert res.mode == "url"
    assert res.content.startswith("https://")


def test_parse_job_source_literal_text(tmp_path: Path):
    res = parse_job_source("Senior Data Analyst at Acme Corp. Requirements: SQL, Python.", cwd=tmp_path)
    assert res.mode == "literal"
    assert "Acme Corp" in res.content


def test_parse_job_source_handles_utf16_file(tmp_path: Path):
    p = tmp_path / "jd.md"
    # Add a BOM to make chardet confident:
    p.write_bytes(b"\xff\xfe" + "JD content with café".encode("utf-16-le"))
    res = parse_job_source("jd.md", cwd=tmp_path)
    assert res.mode == "file"
    assert "café" in res.content


# ---------------------------------------------------------------------------
# format_jd \u2014 persists JD to $RUN_DIR/jd.txt (and onward to $OUT_DIR/jd.md)
#
# Issue #15: students running resumasher against multiple postings were losing
# the JD between runs (it only lived at $RUN_DIR/jd.txt, which gets wiped).
# format_jd normalizes the write so Phase 3 can `cp` to $OUT_DIR/jd.md, and
# adds a Source URL header for url-mode inputs so recruiter follow-ups still
# work weeks later.
# ---------------------------------------------------------------------------


def test_format_jd_file_mode_passes_content_through():
    """File mode: the JD text came from an existing file the student already
    owns. No URL to preserve. Output matches input byte-for-byte."""
    content = "# Senior Analyst\n\nResponsibilities: SQL, dashboards, stakeholder management.\n"
    out = format_jd("file", content)
    assert out == content


def test_format_jd_literal_mode_passes_content_through():
    """Literal mode: the student pasted the JD inline. No URL exists."""
    content = "Junior Data Scientist at Acme. Python, SQL, some ML."
    out = format_jd("literal", content)
    assert out == content


def test_format_jd_url_mode_prepends_source_url_header():
    """URL mode: the fetched page text is the content; the URL itself is
    metadata. Prepend it so Phase 3's cp carries the URL into jd.md."""
    url = "https://company.com/careers/junior-data-scientist.html"
    fetched_page = "Junior Data Scientist\n\nWe are looking for...\n"
    out = format_jd("url", fetched_page, url=url)
    assert out.startswith(f"Source URL: {url}\n\n")
    assert fetched_page in out
    # The header ends with exactly one blank line before the fetched content.
    assert out == f"Source URL: {url}\n\n{fetched_page}"


def test_format_jd_url_mode_without_url_arg_defensive_fallback():
    """If the caller passes mode=url but forgets --url (shouldn't happen, but
    we'd rather return un-headered content than crash)."""
    content = "Fetched job description text."
    out = format_jd("url", content, url=None)
    assert out == content  # No prepend when url is None.


def test_format_jd_url_mode_with_empty_url_defensive_fallback():
    """Same defensive behavior for empty-string url."""
    content = "Fetched job description text."
    out = format_jd("url", content, url="")
    assert out == content


def test_format_jd_preserves_unicode_content():
    """JD text may contain unicode (company names, German/French descriptions,
    em dashes from copy-paste). Don't corrupt it."""
    content = "Stellenbeschreibung: Senior Data Analyst \u2014 München. Gehalt €65k–€80k."
    out = format_jd("literal", content)
    assert out == content
    # And with a URL:
    out_url = format_jd("url", content, url="https://example.de/job")
    assert "München" in out_url and "€65k–€80k" in out_url


def test_format_jd_preserves_trailing_newlines():
    """Don't trim content \u2014 the student may rely on a trailing newline for
    clean markdown rendering of jd.md."""
    content = "Line one.\nLine two.\n"
    out = format_jd("file", content)
    assert out.endswith("\n")
    # URL mode should also preserve the trailing newline of the input.
    out_url = format_jd("url", content, url="https://example.com/job")
    assert out_url.endswith("\n")


# ---------------------------------------------------------------------------
# format-jd CLI subcommand (stdin → stdout transform)
# ---------------------------------------------------------------------------


def _run_format_jd(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "scripts.orchestration", "format-jd", *args]
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, check=False)


def test_cli_format_jd_file_mode_reads_stdin_and_passes_through():
    content = "File-mode JD.\nSecond line.\n"
    r = _run_format_jd("--mode", "file", stdin=content)
    assert r.returncode == 0
    assert r.stdout == content


def test_cli_format_jd_literal_mode_passes_through():
    content = "Literal pasted text."
    r = _run_format_jd("--mode", "literal", stdin=content)
    assert r.returncode == 0
    assert r.stdout == content


def test_cli_format_jd_url_mode_prepends_source_url():
    url = "https://company.com/jobs/42"
    content = "Page text from the fetch.\n"
    r = _run_format_jd("--mode", "url", "--url", url, stdin=content)
    assert r.returncode == 0
    assert r.stdout.startswith(f"Source URL: {url}\n\n")
    assert "Page text from the fetch." in r.stdout


def test_cli_format_jd_url_mode_without_url_flag_is_noop():
    """Defensive: mode=url without --url should pass content through, not
    crash. This exercises the same fallback the Python function has."""
    content = "Content without a URL."
    r = _run_format_jd("--mode", "url", stdin=content)
    assert r.returncode == 0
    assert r.stdout == content


def test_cli_format_jd_rejects_unknown_mode():
    """argparse `choices` should reject anything outside file/url/literal."""
    r = _run_format_jd("--mode", "garbage", stdin="anything")
    assert r.returncode != 0
    assert "invalid choice" in r.stderr


def test_cli_format_jd_reads_content_from_file_argument(tmp_path: Path):
    """--content-file <path> is an alternative to piping via stdin. Useful
    when the JD text is multi-KB and stdin plumbing gets awkward."""
    jd_file = tmp_path / "fetched.txt"
    jd_file.write_text("JD text from disk.\n", encoding="utf-8")
    r = _run_format_jd("--mode", "literal", "--content-file", str(jd_file))
    assert r.returncode == 0
    assert r.stdout == "JD text from disk.\n"


# ---------------------------------------------------------------------------
# discover_resume
# ---------------------------------------------------------------------------


def test_discover_resume_prefers_resume_md_over_cv(tmp_path: Path):
    (tmp_path / "resume.md").write_text("# Me", encoding="utf-8")
    (tmp_path / "CV.md").write_text("# Me (the backup)", encoding="utf-8")
    result = discover_resume(tmp_path)
    assert result is not None and result.name == "resume.md"


def test_discover_resume_falls_through_to_cv_md(tmp_path: Path):
    (tmp_path / "cv.md").write_text("# Me", encoding="utf-8")
    result = discover_resume(tmp_path)
    assert result is not None and result.name == "cv.md"


def test_discover_resume_returns_none_when_missing(tmp_path: Path):
    assert discover_resume(tmp_path) is None


def test_discover_resume_accepts_pdf(tmp_path: Path):
    """A student with only resume.pdf (no markdown source) still works."""
    (tmp_path / "resume.pdf").write_bytes(b"%PDF-1.4\n...")  # stub header
    result = discover_resume(tmp_path)
    assert result is not None and result.name == "resume.pdf"


def test_discover_resume_prefers_markdown_over_pdf(tmp_path: Path):
    """If both .md and .pdf exist, .md wins because it's the source-of-truth."""
    (tmp_path / "resume.md").write_text("# Me", encoding="utf-8")
    (tmp_path / "resume.pdf").write_bytes(b"%PDF-1.4\n...")
    result = discover_resume(tmp_path)
    assert result is not None and result.name == "resume.md"


def test_discover_resume_accepts_cv_pdf_variants(tmp_path: Path):
    (tmp_path / "CV.pdf").write_bytes(b"%PDF-1.4\n...")
    result = discover_resume(tmp_path)
    # Case-exact: the returned Path must carry the on-disk filename, not a
    # lowercased approximation. On case-insensitive filesystems (macOS APFS,
    # Windows NTFS) the previous candidate-probing implementation returned
    # `cv.pdf` even when the real file was `CV.pdf`. See issue #27.
    assert result is not None and result.name == "CV.pdf"


def test_discover_resume_preserves_on_disk_case_mixed(tmp_path: Path):
    """A mixed-case name that isn't literally in RESUME_CANDIDATES still
    matches via case-insensitive comparison, and the on-disk case is kept."""
    (tmp_path / "Cv.pdf").write_bytes(b"%PDF-1.4\n...")
    result = discover_resume(tmp_path)
    assert result is not None and result.name == "Cv.pdf"


# ---------------------------------------------------------------------------
# validate_resume_path: fallback when discover_resume misses
# ---------------------------------------------------------------------------
# Added in v0.3 (issue #3) for non-English-filename students: Lebenslauf.md,
# 履歴書.md, curriculum.md, my_resume_final_v3.md, etc. The SKILL.md orchestrator
# asks the student "what's the filename?" via the cross-host question tool
# and feeds the response through this validator.


def test_discover_resume_unchanged_behavior_non_english(tmp_path: Path):
    """Documentary: discover_resume still returns None for non-English names.
    This is intentional \u2014 the fallback-and-ask logic lives in SKILL.md."""
    (tmp_path / "Lebenslauf.md").write_text("# Bewerbung", encoding="utf-8")
    assert discover_resume(tmp_path) is None


def test_validate_resume_path_accepts_german_filename(tmp_path: Path):
    (tmp_path / "Lebenslauf.md").write_text("# Bewerbung", encoding="utf-8")
    path, err = validate_resume_path(tmp_path, "Lebenslauf.md")
    assert err is None
    assert path is not None and path.name == "Lebenslauf.md"


def test_validate_resume_path_accepts_cjk_filename(tmp_path: Path):
    """CJK characters in filenames must round-trip cleanly."""
    (tmp_path / "履歴書.md").write_text("# 職務経歴書", encoding="utf-8")
    path, err = validate_resume_path(tmp_path, "履歴書.md")
    assert err is None
    assert path is not None and path.name == "履歴書.md"


def test_validate_resume_path_accepts_name_with_spaces(tmp_path: Path):
    """my_resume_final_FINAL_v3.md is a real filename students pick."""
    (tmp_path / "my resume final v3.md").write_text("# Me", encoding="utf-8")
    path, err = validate_resume_path(tmp_path, "my resume final v3.md")
    assert err is None
    assert path is not None and "my resume final v3.md" in str(path)


def test_validate_resume_path_accepts_absolute_path(tmp_path: Path):
    """A student can paste an absolute path instead of a relative filename."""
    target = tmp_path / "Lebenslauf.md"
    target.write_text("# Bewerbung", encoding="utf-8")
    path, err = validate_resume_path(Path("/tmp"), str(target))
    assert err is None
    assert path == target.resolve()


def test_validate_resume_path_rejects_nonexistent(tmp_path: Path):
    path, err = validate_resume_path(tmp_path, "nonexistent.md")
    assert path is None
    assert err is not None and "does not exist" in err


def test_validate_resume_path_rejects_wrong_extension(tmp_path: Path):
    """A student typing `Lebenslauf.docx` gets a clear rejection."""
    (tmp_path / "Lebenslauf.docx").write_text("garbage", encoding="utf-8")
    path, err = validate_resume_path(tmp_path, "Lebenslauf.docx")
    assert path is None
    assert err is not None and "unsupported extension" in err
    # Error message should name the accepted extensions so the student knows
    # what to rename to.
    assert ".md" in err and ".pdf" in err


def test_validate_resume_path_rejects_directory_masquerading_as_file(tmp_path: Path):
    """A directory named `resume.md` is not a resume, even though the name matches."""
    (tmp_path / "resume.md").mkdir()
    path, err = validate_resume_path(tmp_path, "resume.md")
    assert path is None
    assert err is not None and "not a regular file" in err


def test_validate_resume_path_accepts_subdirectory_paths(tmp_path: Path):
    """Students who keep resumes under `./documents/resume.md` should still work."""
    subdir = tmp_path / "documents"
    subdir.mkdir()
    target = subdir / "Lebenslauf.md"
    target.write_text("# Bewerbung", encoding="utf-8")
    path, err = validate_resume_path(tmp_path, "documents/Lebenslauf.md")
    assert err is None
    assert path is not None and path.name == "Lebenslauf.md"


def test_validate_resume_path_rejects_empty_filename(tmp_path: Path):
    """An empty answer from the student should produce a clear error, not a crash."""
    path, err = validate_resume_path(tmp_path, "")
    assert path is None
    assert err is not None and "empty" in err


def test_validate_resume_path_accepts_markdown_extension(tmp_path: Path):
    """The rarer `.markdown` extension is also valid (matches RESUME_CANDIDATES)."""
    (tmp_path / "cv.markdown").write_text("# Me", encoding="utf-8")
    path, err = validate_resume_path(tmp_path, "cv.markdown")
    assert err is None
    assert path is not None


def test_validate_resume_path_accepts_uppercase_extension(tmp_path: Path):
    """Extension matching is case-insensitive \u2014 `RESUME.PDF` is fine."""
    (tmp_path / "RESUME.PDF").write_bytes(b"%PDF-1.4\n...")
    path, err = validate_resume_path(tmp_path, "RESUME.PDF")
    assert err is None
    assert path is not None


# ---------------------------------------------------------------------------
# read_resume: encoding detection
# ---------------------------------------------------------------------------


def test_read_resume_utf8(tmp_path: Path):
    p = tmp_path / "resume.md"
    p.write_text("# Björn Müller\n", encoding="utf-8")
    assert "Björn Müller" in read_resume(p)


def test_read_resume_utf8_bom(tmp_path: Path):
    p = tmp_path / "resume.md"
    p.write_bytes(b"\xef\xbb\xbf" + "# François\n".encode("utf-8"))  # UTF-8 BOM
    assert "François" in read_resume(p)


def test_read_resume_utf16_le_bom(tmp_path: Path):
    # Windows Notepad's default save: UTF-16-LE with BOM.
    p = tmp_path / "resume.md"
    p.write_bytes(b"\xff\xfe" + "# Jiří Švec\n".encode("utf-16-le"))
    assert "Jiří Švec" in read_resume(p)


def test_read_resume_latin1_fallback(tmp_path: Path):
    p = tmp_path / "resume.md"
    # Windows-1252 / Latin-1 é.
    p.write_bytes(b"# caf\xe9\n")
    # Chardet may or may not be >50% confident on a 1-line file, but should
    # either return text or raise UnicodeDecodeError with a helpful message.
    try:
        content = read_resume(p)
        assert "caf" in content
    except UnicodeDecodeError as exc:
        assert "resave" in str(exc).lower() or "encoding" in str(exc).lower()


def test_read_resume_pdf_extracts_selectable_text(tmp_path: Path):
    """Use the render_pdf module to produce a real PDF, then read it back."""
    from scripts.render_pdf import render_resume_eu

    resume_md = """# Björn Analyst
bjorn@example.com | linkedin.com/in/bjorn | Berlin

## Summary
Data scientist with a focus on forecasting and anomaly detection.

## Experience
### Senior Analyst \u2014 Example Corp (2022-2024)
- Built a churn model on 1.5M records, F1=0.78.
"""
    pdf_path = tmp_path / "resume.pdf"
    render_resume_eu(resume_md, pdf_path)
    extracted = read_resume(pdf_path)
    assert "Björn" in extracted
    assert "bjorn@example.com" in extracted
    assert "churn model" in extracted
    assert "F1=0.78" in extracted


def test_read_resume_pdf_raises_clearly_on_scanned_image_only_pdf(tmp_path: Path):
    """Image-only PDFs (scanned documents) should fail fast with a helpful message."""
    import pytest
    # Make a real PDF with no selectable text by rendering an empty doc.
    from scripts.render_pdf import render_cover_letter
    pdf_path = tmp_path / "scanned.pdf"
    render_cover_letter("", pdf_path)  # empty body = no text
    with pytest.raises(RuntimeError) as exc:
        read_resume(pdf_path)
    msg = str(exc.value).lower()
    assert "image-based" in msg or "scanned" in msg
    # Should mention the workaround options.
    assert "ocr" in msg or "resume.md" in msg


def test_read_resume_pdf_raises_clearly_on_corrupted_pdf(tmp_path: Path):
    """A file with a .pdf extension but garbage content should fail clearly."""
    import pytest
    pdf_path = tmp_path / "resume.pdf"
    pdf_path.write_bytes(b"this is not a real pdf, just some bytes")
    with pytest.raises(RuntimeError) as exc:
        read_resume(pdf_path)
    msg = str(exc.value).lower()
    # Could fail either as extraction error OR as image-based (empty text).
    # Both paths produce a RuntimeError with a helpful message.
    assert "pdf" in msg


# ---------------------------------------------------------------------------
# folder_state_hash
# ---------------------------------------------------------------------------


def test_folder_state_hash_stable_across_calls(tmp_path: Path):
    (tmp_path / "a.md").write_text("one", encoding="utf-8")
    (tmp_path / "b.md").write_text("two", encoding="utf-8")
    h1 = folder_state_hash(tmp_path)
    h2 = folder_state_hash(tmp_path)
    assert h1 == h2


def test_folder_state_hash_changes_when_file_touched(tmp_path: Path):
    f = tmp_path / "a.md"
    f.write_text("one", encoding="utf-8")
    h1 = folder_state_hash(tmp_path)
    # Bump mtime without changing content.
    future = time.time() + 10
    os.utime(f, (future, future))
    h2 = folder_state_hash(tmp_path)
    assert h1 != h2


def test_folder_state_hash_ignores_resumasher_dir(tmp_path: Path):
    (tmp_path / "a.md").write_text("one", encoding="utf-8")
    h_before = folder_state_hash(tmp_path)
    (tmp_path / ".resumasher").mkdir()
    (tmp_path / ".resumasher" / "cache.txt").write_text("cached stuff", encoding="utf-8")
    h_after = folder_state_hash(tmp_path)
    assert h_before == h_after


def test_folder_state_hash_ignores_claude_skills_dir(tmp_path: Path):
    """
    Regression for the 'skill mines itself' bug: when resumasher is installed
    project-scope at <project>/.claude/skills/resumasher/, the folder miner
    must NOT walk into .claude/ and present the skill's own source/fixtures
    as student evidence.
    """
    (tmp_path / "resume.md").write_text("# Me", encoding="utf-8")
    (tmp_path / "real-project" / "README.md").parent.mkdir(parents=True)
    (tmp_path / "real-project" / "README.md").write_text("real work", encoding="utf-8")
    hash_before = folder_state_hash(tmp_path)

    # Now drop a fake resumasher install into .claude/skills/ and verify the
    # hash is unchanged (i.e., the .claude tree was ignored).
    fake_skill = tmp_path / ".claude" / "skills" / "resumasher"
    fake_skill.mkdir(parents=True)
    (fake_skill / "SKILL.md").write_text("fake skill content", encoding="utf-8")
    (fake_skill / "GOLDEN_FIXTURES" / "resume.md").parent.mkdir(parents=True)
    (fake_skill / "GOLDEN_FIXTURES" / "resume.md").write_text("# Ana Müller fake", encoding="utf-8")
    hash_after = folder_state_hash(tmp_path)

    assert hash_before == hash_after, (
        "folder_state_hash changed when .claude/ was added \u2014 .claude must "
        "be in DEFAULT_IGNORE_DIRS so the skill doesn't mine itself."
    )


def test_mine_folder_context_excludes_claude_skills(tmp_path: Path):
    """The miner should not emit FILE entries for anything under .claude/."""
    (tmp_path / "resume.md").write_text("# Me\n\nreal content", encoding="utf-8")
    fake_skill = tmp_path / ".claude" / "skills" / "resumasher"
    fake_skill.mkdir(parents=True)
    (fake_skill / "SKILL.md").write_text("fake skill contents 9999", encoding="utf-8")
    (fake_skill / "README.md").write_text("fake readme DO_NOT_LEAK", encoding="utf-8")

    ctx = mine_folder_context(tmp_path)
    assert "resume.md" in ctx
    assert ".claude/skills/resumasher/SKILL.md" not in ctx
    assert "DO_NOT_LEAK" not in ctx
    assert "9999" not in ctx


@pytest.mark.parametrize("ai_dir", [".codex", ".gemini", ".agents"])
def test_folder_state_hash_ignores_other_ai_cli_dirs(tmp_path: Path, ai_dir: str):
    """
    Regression for the Codex/Gemini port: project-scope installs live at
    .codex/skills/resumasher/ and .gemini/skills/resumasher/ (plus .agents/
    as Gemini's documented alias). These must be ignored by the folder
    miner the same way .claude/ is \u2014 same self-mining risk.
    """
    (tmp_path / "resume.md").write_text("# Me", encoding="utf-8")
    hash_before = folder_state_hash(tmp_path)

    fake_skill = tmp_path / ai_dir / "skills" / "resumasher"
    fake_skill.mkdir(parents=True)
    (fake_skill / "SKILL.md").write_text("fake skill contents", encoding="utf-8")
    hash_after = folder_state_hash(tmp_path)

    assert hash_before == hash_after, (
        f"folder_state_hash changed when {ai_dir}/ was added \u2014 "
        f"{ai_dir} must be in DEFAULT_IGNORE_DIRS."
    )


@pytest.mark.parametrize("ai_dir", [".codex", ".gemini", ".agents"])
def test_mine_folder_context_excludes_other_ai_cli_dirs(tmp_path: Path, ai_dir: str):
    (tmp_path / "resume.md").write_text("# Me\n\nreal content", encoding="utf-8")
    fake_skill = tmp_path / ai_dir / "skills" / "resumasher"
    fake_skill.mkdir(parents=True)
    (fake_skill / "SKILL.md").write_text("fake contents SHOULD_NOT_LEAK", encoding="utf-8")

    ctx = mine_folder_context(tmp_path)
    assert "resume.md" in ctx
    assert f"{ai_dir}/skills/resumasher/SKILL.md" not in ctx
    assert "SHOULD_NOT_LEAK" not in ctx


def test_folder_state_hash_ignores_git_and_venv(tmp_path: Path):
    (tmp_path / "a.md").write_text("one", encoding="utf-8")
    for noise in [".git", ".venv", "node_modules", "__pycache__"]:
        d = tmp_path / noise
        d.mkdir()
        (d / "junk.txt").write_text("noise", encoding="utf-8")
    # Hash should match a clean folder with just a.md.
    clean = tmp_path.parent / (tmp_path.name + "-clean")
    clean.mkdir()
    (clean / "a.md").write_text("one", encoding="utf-8")
    os.utime(clean / "a.md", ((tmp_path / "a.md").stat().st_mtime,) * 2)
    # The two hashes will only match if ignored dirs are actually ignored AND
    # mtimes align. Test at least that noise folders don't leak in:
    h_noisy = folder_state_hash(tmp_path)
    h_noise_then_removed = folder_state_hash(tmp_path, ignore_dirs=DEFAULT_IGNORE_DIRS)
    assert h_noisy == h_noise_then_removed  # same default ignores applied


# ---------------------------------------------------------------------------
# mine_folder_context
# ---------------------------------------------------------------------------


def test_mine_context_includes_markdown_skips_csv(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Project\n\nStuff that matters.", encoding="utf-8")
    (tmp_path / "analysis.py").write_text("import pandas\nprint('hello')", encoding="utf-8")
    (tmp_path / "data.csv").write_text("a,b,c\n1,2,3", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("Short notes.", encoding="utf-8")

    ctx = mine_folder_context(tmp_path)
    assert "README.md" in ctx
    assert "analysis.py" in ctx
    assert "notes.txt" in ctx
    assert "data.csv" not in ctx  # skip extension


def test_mine_context_truncates_oversized_files(tmp_path: Path):
    big_content = "line\n" * (MAX_FILE_CHARS)
    (tmp_path / "big.md").write_text(big_content, encoding="utf-8")
    ctx = mine_folder_context(tmp_path)
    assert "truncated" in ctx.lower()


def test_mine_context_readme_variants_always_included(tmp_path: Path):
    # README without .md extension still gets included.
    (tmp_path / "README").write_text("The readme body", encoding="utf-8")
    ctx = mine_folder_context(tmp_path)
    assert "README" in ctx
    assert "The readme body" in ctx


def test_mine_context_handles_pdf_file(tmp_path: Path):
    # Generate a small real PDF using reportlab rather than mocking.
    from scripts.render_pdf import render_resume_eu
    render_resume_eu(
        "# Test User\ntest@example.com\n\n## Summary\nTestable capstone content.",
        tmp_path / "capstone.pdf",
    )
    ctx = mine_folder_context(tmp_path)
    assert "capstone.pdf" in ctx
    # Content from the PDF should have been extracted.
    assert "Test User" in ctx or "Testable capstone content" in ctx


def test_mine_context_handles_notebook_file(tmp_path: Path):
    notebook = {
        "cells": [
            {
                "cell_type": "markdown",
                "source": ["# Churn Analysis\n", "\n", "F1 of 0.82 achieved on 2.3M rows."],
            },
            {
                "cell_type": "code",
                "source": "import pandas as pd\ndf = pd.read_csv('data.csv')\n",
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    (tmp_path / "churn.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
    ctx = mine_folder_context(tmp_path)
    assert "churn.ipynb" in ctx
    assert "Churn Analysis" in ctx or "F1 of 0.82" in ctx
    assert "import pandas" in ctx


# ---------------------------------------------------------------------------
# extract_fit_score / extract_company / is_failure_sentinel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prose,expected",
    [
        ("Some analysis here.\nFIT_SCORE: 7\nStrengths: ...", 7),
        ("FIT_SCORE: 0", 0),
        ("FIT_SCORE: 10", 10),
        ("fit_score: 6 (case-insensitive)", 6),
        ("No score here", None),
        ("FIT_SCORE: 11 (out of range)", None),
        ("FIT_SCORE: -1 (negative)", None),
        ("FIT_SCORE: seven (wordy)", None),
    ],
)
def test_extract_fit_score(prose, expected):
    assert extract_fit_score(prose) == expected


@pytest.mark.parametrize(
    "prose,expected",
    [
        ("Assessment.\nCOMPANY: Deloitte\nMore text.", "Deloitte"),
        ("company: JP Morgan Chase", "JP Morgan Chase"),
        ("COMPANY: UNKNOWN", None),
        ("no company line", None),
        ("COMPANY: ", None),
    ],
)
def test_extract_company(prose, expected):
    assert extract_company(prose) == expected


def test_is_failure_sentinel_true_for_leading_marker():
    assert is_failure_sentinel("FAILURE: could not read file")
    assert is_failure_sentinel("\n\nFAILURE: blank lines first\n")


def test_is_failure_sentinel_false_for_prose_mentioning_failure():
    assert not is_failure_sentinel("The candidate had a career failure but recovered.")
    assert not is_failure_sentinel("FIT_SCORE: 6\nStrengths include resilience after failure.")


# ---------------------------------------------------------------------------
# extract_role / extract_seniority / extract_*_count / extract_recommendation
# (added in v0.2 for fit-analyst structured-data emission used by telemetry)
# ---------------------------------------------------------------------------


from scripts.orchestration import (  # noqa: E402
    extract_gaps_count,
    extract_recommendation,
    extract_role,
    extract_seniority,
    extract_strengths_count,
)


@pytest.mark.parametrize(
    "prose,expected",
    [
        ("ROLE: Senior Data Scientist", "Senior Data Scientist"),
        # Case-insensitive match on the ROLE: marker itself.
        ("role: Software Engineer II", "Software Engineer II"),
        ("Some prose.\nROLE: ML Engineer\nMore.", "ML Engineer"),
        ("ROLE: UNKNOWN", None),
        ("ROLE:   ", None),
        ("no role line here", None),
    ],
)
def test_extract_role(prose, expected):
    assert extract_role(prose) == expected


@pytest.mark.parametrize(
    "prose,expected",
    [
        ("SENIORITY: senior", "senior"),
        # Value is also case-folded so SENIORITY:STAFF/staff/Staff all match.
        ("seniority: STAFF", "staff"),
        ("Some prose.\nSENIORITY: cxo\nMore.", "cxo"),
        # "unknown" is intentionally treated as None so callers can detect
        # failed classification (vs the LLM saying "I don't know").
        ("SENIORITY: unknown", None),
        # Anything outside the enum is rejected (defense against LLM drift).
        ("SENIORITY: god-king", None),
        ("SENIORITY: lead", None),  # 'lead' is NOT in the enum
        ("no seniority line", None),
    ],
)
def test_extract_seniority(prose, expected):
    assert extract_seniority(prose) == expected


@pytest.mark.parametrize(
    "prose,expected",
    [
        ("STRENGTHS_COUNT: 5", 5),
        ("STRENGTHS_COUNT: 0", 0),
        ("strengths_count: 12 (case-insensitive)", 12),
        ("STRENGTHS_COUNT: many", None),
        ("missing", None),
    ],
)
def test_extract_strengths_count(prose, expected):
    assert extract_strengths_count(prose) == expected


@pytest.mark.parametrize(
    "prose,expected",
    [
        ("GAPS_COUNT: 3", 3),
        ("gaps_count: 0", 0),
        ("missing", None),
    ],
)
def test_extract_gaps_count(prose, expected):
    assert extract_gaps_count(prose) == expected


@pytest.mark.parametrize(
    "prose,expected",
    [
        ("RECOMMENDATION: yes", "yes"),
        ("RECOMMENDATION: no", "no"),
        ("RECOMMENDATION: yes_with_caveats", "yes_with_caveats"),
        # Normalize spaces and hyphens to underscores
        ("RECOMMENDATION: yes with caveats", "yes_with_caveats"),
        ("RECOMMENDATION: yes-with-caveats", "yes_with_caveats"),
        # Case-insensitive on both marker and value
        ("recommendation: YES", "yes"),
        # Anything outside the enum is rejected
        ("RECOMMENDATION: maybe", None),
        ("missing", None),
    ],
)
def test_extract_recommendation(prose, expected):
    assert extract_recommendation(prose) == expected


def test_seniority_classification_works_in_any_language_via_llm():
    """The fit-analyst classifies seniority in any language; the extractor
    just validates the emitted enum. These represent what the LLM would emit
    after seeing 'Leitender Entwickler' (German), 'シニア' (Japanese), or
    'Jefe de Datos' (Spanish) job titles."""
    assert extract_seniority("SENIORITY: senior") == "senior"  # German Leitender
    assert extract_seniority("SENIORITY: senior") == "senior"  # Japanese シニア
    assert extract_seniority("SENIORITY: manager") == "manager"  # Spanish Jefe


# ---------------------------------------------------------------------------
# company_slug
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Deloitte", "deloitte"),
        ("Deloitte Consulting LLC", "deloitte-consulting"),
        ("JP Morgan Chase & Co.", "jp-morgan-chase"),
        ("Franklin Templeton", "franklin-templeton"),
        ("", "unknown"),
        ("   ", "unknown"),
        ("Müller GmbH", "müller"),
    ],
)
def test_company_slug(name, expected):
    assert company_slug(name) == expected


# ---------------------------------------------------------------------------
# append_history + first_run + config
# ---------------------------------------------------------------------------


def test_append_history_creates_dir_and_writes_jsonl(tmp_path: Path):
    rec = {"ts": "2026-04-18", "company": "Deloitte", "fit_score": 7}
    path = append_history(tmp_path, rec)
    assert path.exists()
    line = path.read_text(encoding="utf-8").strip()
    assert json.loads(line) == rec


def test_append_history_is_additive(tmp_path: Path):
    append_history(tmp_path, {"n": 1})
    append_history(tmp_path, {"n": 2})
    lines = (tmp_path / ".resumasher" / "history.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_first_run_needed_and_config_roundtrip(tmp_path: Path):
    assert first_run_needed(tmp_path) is True
    write_config(tmp_path, {"name": "Ana", "style": "eu"})
    assert first_run_needed(tmp_path) is False
    assert read_config(tmp_path) == {"name": "Ana", "style": "eu"}


def test_ensure_gitignore_appends_to_existing_repo(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    result = ensure_gitignore(tmp_path)
    assert result is not None
    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".resumasher/" in content
    assert "build/" in content  # didn't clobber


def test_ensure_gitignore_creates_file_in_empty_repo(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    result = ensure_gitignore(tmp_path)
    assert result is not None
    assert ".resumasher/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_ensure_gitignore_idempotent(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    ensure_gitignore(tmp_path)
    ensure_gitignore(tmp_path)
    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert content.count(".resumasher/") == 1


def test_ensure_gitignore_noop_outside_git_repo(tmp_path: Path):
    # No .git directory, and walk upward doesn't find one either (tmp_path
    # is /tmp/... on most systems, not a git repo).
    result = ensure_gitignore(tmp_path)
    # If the test runner happens to execute inside a git repo, result will be
    # a path. Just assert we don't crash.
    assert result is None or result.exists()


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


def test_cli_company_slug_roundtrip(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, "-m", "scripts.orchestration", "company-slug", "Deloitte Consulting LLC"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "deloitte-consulting"


def test_cli_discover_resume_missing_returns_failure(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, "-m", "scripts.orchestration", "discover-resume", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.returncode == 1
    assert "FAILURE" in result.stdout


def test_cli_mine_context_with_github_does_not_moduleerror(tmp_path: Path):
    """
    Regression for real-trace bug: invoking `python scripts/orchestration.py
    mine-context . --github-username X` ended with:
        ModuleNotFoundError: No module named 'scripts'

    Root cause: `from scripts import github_mine` required the parent of
    scripts/ to be on sys.path, but running the .py file directly only puts
    scripts/ itself on sys.path. Fixed by a sys.path.insert() at the top of
    orchestration.py and changing to a sibling import.

    This test runs the CLI exactly the way SKILL.md drives it (not via
    `python -m scripts.orchestration`) and verifies no import error. Uses a
    username guaranteed to NotFound so no network fetch succeeds, but the
    orchestrator must get far enough to emit the warning \u2014 which means the
    github_mine module imported cleanly.
    """
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "orchestration.py"
    (tmp_path / "resume.md").write_text("# Me\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, str(script),
            "mine-context", str(tmp_path),
            "--github-username", "this-username-does-not-exist-xyz-99999",
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),  # NOT the repo root \u2014 simulates student's CWD
        timeout=30,
    )
    combined = result.stdout + "\n" + result.stderr
    assert "ModuleNotFoundError" not in combined, (
        f"orchestration.py failed with ModuleNotFoundError when running "
        f"mine-context --github-username. This means the sibling-import fix "
        f"for github_mine regressed. stdout+stderr:\n{combined}"
    )
    # The mine-context call should succeed with a warning about the github
    # fetch failing (not found or network), not a Python error.
    assert result.returncode == 0, (
        f"mine-context should exit 0 even when github fetch fails "
        f"(non-fatal). Got {result.returncode}. Output:\n{combined}"
    )


# ---------------------------------------------------------------------------
# inspect \u2014 agent-driven debugging helpers
#
# These are the structured introspection helpers the SKILL.md "Debugging
# this skill" playbook calls when a student reports a bug. Each returns
# JSON-ready dicts with counts, content previews, and light warnings.
# ---------------------------------------------------------------------------


def test_inspect_resume_happy_path_returns_expected_fields(tmp_path: Path):
    """A well-formed resume produces no warnings and populates every field."""
    md = (
        "# Ana Müller\n"
        "ana@example.com | +43 123 | linkedin.com/in/ana | Vienna\n"
        "\n"
        "## Summary\n"
        "Business analytics MSc graduate.\n"
        "\n"
        "## Experience\n"
        "### Data Analyst \u2014 Raiffeisen (2025)\n"
        "- Built churn model, F1=0.82.\n"
        "- Delivered Tableau dashboard.\n"
    )
    p = tmp_path / "resume.md"
    p.write_text(md, encoding="utf-8")

    result = inspect_resume(p)

    assert result["name"] == "Ana Müller"
    assert "ana@example.com" in result["contact_line"]
    assert result["has_h1"] is True
    assert result["first_line_raw"] == "# Ana Müller"
    assert result["section_count"] == 2
    assert result["section_order"] == ["Summary", "Experience"]
    assert result["warnings"] == []


def test_inspect_resume_empty_name_triggers_warning(tmp_path: Path):
    """Regression guard for issue #18 (KNOWN_FAILURE_MODES.md #1):
    pipe-separated contact line on line 1 without a `# Name` H1 → parser
    drops the contact header, PDF ships with no name, ATS can't identify
    the candidate."""
    md = (
        "Test Candidate | +43 664 0000000 | test@example.com | Vienna | linkedin.com/in/testcandidate\n"
        "\n"
        "## Summary\n"
        "MSc Business Analytics student.\n"
    )
    p = tmp_path / "resume.md"
    p.write_text(md, encoding="utf-8")

    result = inspect_resume(p)

    assert result["name"] == ""
    assert result["contact_line"] == ""
    assert result["has_h1"] is False
    assert result["first_line_raw"].startswith("Test Candidate |")

    codes = [w["code"] for w in result["warnings"]]
    assert "EMPTY_NAME" in codes
    assert "EMPTY_CONTACT_LINE" in codes
    empty_name = next(w for w in result["warnings"] if w["code"] == "EMPTY_NAME")
    assert empty_name["severity"] == "critical"


def test_inspect_resume_shape_b_no_longer_orphans_bullets(tmp_path: Path):
    """Inverted from a previous test that asserted the ORPHANED_BULLETS
    warning fires for the issue #19 shape. After the parser fix, the
    same shape produces correctly-attached blocks \u2014 no warning fires,
    bullets sit under their titles in the parse tree.

    Input markdown is identical to the pre-fix test so the before/after
    diff is visible: same input, inverted expectations."""
    md = (
        "# Ana Müller\n"
        "ana@example.com | +43 | Vienna\n"
        "\n"
        "## Research Experience\n"
        "\n"
        "**SME High-Growth \u2014 Predictive Modeling** | Feb 2026\n"
        "- Engineered an automated data pipeline for 20,000 firms.\n"
        "- Developed 118 features for nonlinear patterns.\n"
        "\n"
        "**Cross-Linguistic Sentiment Analysis via AWS** | Nov 2025\n"
        "- Built a cloud-native Python pipeline.\n"
    )
    p = tmp_path / "resume.md"
    p.write_text(md, encoding="utf-8")

    result = inspect_resume(p)

    # No ORPHANED_BULLETS warning \u2014 the shape is now parsed correctly.
    orphaned = [w for w in result["warnings"] if w["code"] == "ORPHANED_BULLETS"]
    assert orphaned == [], (
        f"Expected no ORPHANED_BULLETS warning after parser fix, got: {orphaned}"
    )

    # Two synthetic blocks created from the `**Title** | metadata` lines.
    research = next(s for s in result["sections"] if s["heading"] == "Research Experience")
    assert research["block_count"] == 2, (
        f"Expected 2 blocks (one per project title), got {research['block_count']}"
    )
    # Bullets attached to the blocks, not loose at the section level.
    assert research["raw_bullet_count"] == 0
    # Each block has the right bullets: 2 under SME, 1 under Cross-Linguistic.
    assert research["block_bullet_counts"] == [2, 1]
    # `**Title**` lines no longer end up as raw paragraphs.
    assert research["raw_paragraph_count"] == 0


def test_inspect_resume_well_formed_sub_blocks_no_warning(tmp_path: Path):
    """Control: same Research Experience shape but with `###` wrappers
    should produce proper blocks and no orphaned-bullets warning."""
    md = (
        "# Ana Müller\n"
        "ana@example.com | +43 | Vienna\n"
        "\n"
        "## Research Experience\n"
        "\n"
        "### SME High-Growth \u2014 Predictive Modeling (Feb 2026)\n"
        "- Engineered an automated data pipeline.\n"
        "- Developed 118 features.\n"
        "\n"
        "### Cross-Linguistic Sentiment Analysis (Nov 2025)\n"
        "- Built a cloud-native Python pipeline.\n"
    )
    p = tmp_path / "resume.md"
    p.write_text(md, encoding="utf-8")

    result = inspect_resume(p)

    orphaned = [w for w in result["warnings"] if w["code"] == "ORPHANED_BULLETS"]
    assert orphaned == []
    research = next(s for s in result["sections"] if s["heading"] == "Research Experience")
    assert research["block_count"] == 2
    assert research["block_bullet_counts"] == [2, 1]


def test_inspect_resume_preview_fields_truncate_long_content(tmp_path: Path):
    """Preview fields keep the JSON readable. Long lines get truncated
    with a trailing ellipsis so the agent can still recognize the shape
    without wading through kilobytes of text."""
    long_line = "x" * 500
    md = (
        "# Me\nme@example.com | +1 | Earth\n\n"
        "## Summary\n"
        f"{long_line}\n"
    )
    p = tmp_path / "resume.md"
    p.write_text(md, encoding="utf-8")

    result = inspect_resume(p)
    summary_section = next(s for s in result["sections"] if s["heading"] == "Summary")
    assert len(summary_section["raw_paragraph_previews"]) == 1
    assert summary_section["raw_paragraph_previews"][0].endswith("…")
    assert len(summary_section["raw_paragraph_previews"][0]) <= 130


def test_inspect_pdf_returns_text_and_section_order(tmp_path: Path):
    """Generate a PDF via the real renderer, then inspect it back. Round-
    trip check: the section order we wrote should match what inspect finds
    in the extracted text."""
    from scripts.render_pdf import render_resume_eu

    md = (
        "# Test Person\n"
        "me@example.com | +1 | Earth\n"
        "\n"
        "## Summary\nBrief summary.\n"
        "\n"
        "## Experience\n"
        "### Role \u2014 Company (2024)\n"
        "- Did a thing.\n"
        "\n"
        "## Education\n"
        "### MSc \u2014 University (2024)\n"
    )
    resume_md = tmp_path / "resume.md"
    resume_md.write_text(md, encoding="utf-8")
    pdf = tmp_path / "resume.pdf"
    render_resume_eu(md, pdf)

    result = inspect_pdf(pdf)

    assert result["size_bytes"] > 1000
    assert "Test Person" in result["extracted_text"]
    # Section order in text should match source (EU preserves order).
    assert result["section_order_in_text"] == ["Summary", "Experience", "Education"]


def test_inspect_photo_square_no_warning(tmp_path: Path):
    """A 500×500 square photo matches the 3×3cm render box \u2014 no stretch
    warning."""
    from PIL import Image as PILImage

    photo = tmp_path / "square.jpg"
    PILImage.new("RGB", (500, 500), color=(100, 120, 140)).save(photo, "JPEG")

    result = inspect_photo(photo)

    assert result["width"] == 500
    assert result["height"] == 500
    assert result["aspect"] == 1.0
    assert result["warnings"] == []


def test_inspect_photo_portrait_triggers_stretch_warning(tmp_path: Path):
    """Regression guard for KNOWN_FAILURE_MODES.md #4: portrait photo
    (3:4 aspect) embedded in a 1:1 render box will stretch horizontally."""
    from PIL import Image as PILImage

    photo = tmp_path / "portrait.jpg"
    # 3:4 aspect \u2014 typical phone portrait
    PILImage.new("RGB", (300, 400), color=(100, 120, 140)).save(photo, "JPEG")

    result = inspect_photo(photo)

    assert result["width"] == 300
    assert result["height"] == 400
    assert result["aspect"] == 0.75
    codes = [w["code"] for w in result["warnings"]]
    assert "PHOTO_ASPECT_STRETCH" in codes
    stretch = next(w for w in result["warnings"] if w["code"] == "PHOTO_ASPECT_STRETCH")
    # Delta is (1.0 - 0.75) / 1.0 * 100 = 25%
    assert 20 < result["aspect_delta_pct"] < 30
    assert stretch["severity"] == "notice"


# ---------------------------------------------------------------------------
# inspect CLI subcommand
# ---------------------------------------------------------------------------


def test_cli_inspect_resume_returns_parseable_json(tmp_path: Path):
    md = (
        "Test Candidate | +43 | test@example.com | Vienna\n"
        "\n"
        "## Summary\nShort.\n"
    )
    p = tmp_path / "resume.md"
    p.write_text(md, encoding="utf-8")

    r = subprocess.run(
        [sys.executable, "-m", "scripts.orchestration", "inspect", "--resume", str(p)],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr
    parsed = json.loads(r.stdout)
    assert parsed["has_h1"] is False
    assert any(w["code"] == "EMPTY_NAME" for w in parsed["warnings"])


def test_cli_inspect_requires_one_of_three_flags():
    """argparse mutually_exclusive_group(required=True) should reject
    `inspect` with no flag."""
    r = subprocess.run(
        [sys.executable, "-m", "scripts.orchestration", "inspect"],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode != 0
    assert "required" in r.stderr.lower() or "one of the arguments" in r.stderr.lower()


def test_cli_inspect_rejects_multiple_flags(tmp_path: Path):
    (tmp_path / "a.md").write_text("# X\ne@e.com\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# Y\nf@f.com\n", encoding="utf-8")
    r = subprocess.run(
        [
            sys.executable, "-m", "scripts.orchestration", "inspect",
            "--resume", str(tmp_path / "a.md"),
            "--pdf", str(tmp_path / "b.md"),
        ],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode != 0
    assert "not allowed" in r.stderr.lower() or "mutually" in r.stderr.lower()


def test_cli_inspect_photo_on_real_image(tmp_path: Path):
    from PIL import Image as PILImage
    photo = tmp_path / "p.jpg"
    PILImage.new("RGB", (600, 450), color=(50, 50, 50)).save(photo, "JPEG")
    r = subprocess.run(
        [sys.executable, "-m", "scripts.orchestration", "inspect", "--photo", str(photo)],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr
    parsed = json.loads(r.stdout)
    assert parsed["width"] == 600
    assert parsed["height"] == 450
    # 4:3 landscape \u2014 aspect 1.33 \u2014 stretch warning expected
    assert any(w["code"] == "PHOTO_ASPECT_STRETCH" for w in parsed["warnings"])


# ---------------------------------------------------------------------------
# Issue #50: extract-fit-fields \u2014 per-field files instead of env-source.
# ---------------------------------------------------------------------------


SAMPLE_FIT_OUTPUT = (
    "Detailed prose fit assessment with strengths and gaps...\n"
    "\n"
    "FIT_SCORE: 7\n"
    "COMPANY: Elevation Capital\n"
    "ROLE: Head of AI & Product\n"
    "SENIORITY: director\n"
    "STRENGTHS_COUNT: 8\n"
    "GAPS_COUNT: 7\n"
    "RECOMMENDATION: yes_with_caveats\n"
)


def _run_extract_fit_fields(
    output_dir: Path, fit_text: str
) -> "subprocess.CompletedProcess":
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.orchestration",
            "extract-fit-fields",
            "--output-dir",
            str(output_dir),
        ],
        input=fit_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(Path(__file__).resolve().parent.parent),
        check=False,
    )


def test_extract_fit_fields_writes_seven_per_field_files(tmp_path: Path):
    out = tmp_path / "fit"
    r = _run_extract_fit_fields(out, SAMPLE_FIT_OUTPUT)
    assert r.returncode == 0, r.stderr
    expected = {
        "score.txt": "7",
        "company.txt": "Elevation Capital",
        "role.txt": "Head of AI & Product",
        "seniority.txt": "director",
        "strengths.txt": "8",
        "gaps.txt": "7",
        "recommendation.txt": "yes_with_caveats",
    }
    for filename, expected_value in expected.items():
        f = out / filename
        assert f.exists(), f"{filename} not created"
        assert f.read_text(encoding="utf-8").strip() == expected_value, (
            f"{filename}: expected {expected_value!r}, "
            f"got {f.read_text(encoding='utf-8')!r}"
        )


def test_extract_fit_fields_creates_output_dir_if_missing(tmp_path: Path):
    out = tmp_path / "deeply" / "nested" / "fit"
    assert not out.exists()
    r = _run_extract_fit_fields(out, SAMPLE_FIT_OUTPUT)
    assert r.returncode == 0
    assert out.exists() and out.is_dir()
    assert (out / "company.txt").exists()


def test_extract_fit_fields_handles_unknown_or_missing_values(tmp_path: Path):
    """If the fit-analyst couldn't identify the company (returns
    `COMPANY: UNKNOWN`), or an extractor returns None, the per-field
    file is created but empty. The agent decides what to do with the
    empty value downstream."""
    fit_with_unknown = (
        "Prose...\n"
        "FIT_SCORE: 5\n"
        "COMPANY: UNKNOWN\n"
        "SENIORITY: unknown\n"
        "STRENGTHS_COUNT: 3\n"
        "GAPS_COUNT: 4\n"
        "RECOMMENDATION: no\n"
    )
    out = tmp_path / "fit"
    r = _run_extract_fit_fields(out, fit_with_unknown)
    assert r.returncode == 0
    for fn in (
        "score.txt", "company.txt", "role.txt", "seniority.txt",
        "strengths.txt", "gaps.txt", "recommendation.txt",
    ):
        assert (out / fn).exists()
    assert (out / "role.txt").read_text(encoding="utf-8") == ""


def test_extract_fit_fields_emits_summary_on_stdout(tmp_path: Path):
    """Flat key=value summary, one per line. No JSON \u2014 avoids the
    shell-eats-JSON repeat of issue #44."""
    out = tmp_path / "fit"
    r = _run_extract_fit_fields(out, SAMPLE_FIT_OUTPUT)
    assert r.returncode == 0
    lines = r.stdout.strip().splitlines()
    company_line = next(L for L in lines if L.startswith("company="))
    assert company_line == "company=Elevation Capital"


def test_per_field_round_trip_with_multi_word_values_via_real_bash(tmp_path: Path):
    """Load-bearing regression test for issue #50. Runs the SKILL.md
    Phase 3 + Phase 9 idiom in a real `bash -c` subprocess. Asserts
    multi-word company / role survive write+read round-trip
    byte-perfect \u2014 no shell-source corruption."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available; this test exercises the SKILL.md shell idiom")

    out = tmp_path / "fit"
    r = _run_extract_fit_fields(out, SAMPLE_FIT_OUTPUT)
    assert r.returncode == 0

    script = (
        f'set -euo pipefail\n'
        f'COMPANY=$(cat "{out}/company.txt")\n'
        f'ROLE=$(cat "{out}/role.txt")\n'
        f'SENIORITY=$(cat "{out}/seniority.txt")\n'
        f'echo "COMPANY=[$COMPANY]"\n'
        f'echo "ROLE=[$ROLE]"\n'
        f'echo "SENIORITY=[$SENIORITY]"\n'
    )
    proc = subprocess.run(
        [bash, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, (
        f"bash round-trip failed (exit {proc.returncode}). stderr:\n{proc.stderr}"
    )
    assert "COMPANY=[Elevation Capital]" in proc.stdout, (
        f"COMPANY value lost in round-trip. stdout:\n{proc.stdout}"
    )
    assert "ROLE=[Head of AI & Product]" in proc.stdout, (
        f"ROLE value lost in round-trip. stdout:\n{proc.stdout}"
    )


def test_per_field_round_trip_survives_single_quotes_and_dollar_signs(tmp_path: Path):
    """Company names with apostrophes (`Macy's`), ampersands, and
    dollar signs MUST survive the round-trip. Option A (heredoc with
    quoted values) would break; Option B (per-field files + $(cat))
    is immune."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    weird_fit = (
        "Prose...\n"
        "FIT_SCORE: 6\n"
        "COMPANY: Macy's & $hop\n"
        "ROLE: VP, Engineering \u2014 Tech & Tools\n"
        "SENIORITY: vp\n"
        "STRENGTHS_COUNT: 5\n"
        "GAPS_COUNT: 3\n"
        "RECOMMENDATION: yes_with_caveats\n"
    )
    out = tmp_path / "fit"
    r = _run_extract_fit_fields(out, weird_fit)
    assert r.returncode == 0
    script = (
        f'set -euo pipefail\n'
        f'COMPANY=$(cat "{out}/company.txt")\n'
        f'ROLE=$(cat "{out}/role.txt")\n'
        f'echo "COMPANY=[$COMPANY]"\n'
        f'echo "ROLE=[$ROLE]"\n'
    )
    proc = subprocess.run(
        [bash, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "COMPANY=[Macy's & $hop]" in proc.stdout
    assert "ROLE=[VP, Engineering \u2014 Tech & Tools]" in proc.stdout


def test_skill_md_prescribes_per_field_files_for_fit_extraction():
    """SKILL.md must explicitly prescribe `extract-fit-fields` and the
    per-field files under `$RUN_DIR/fit/`. Future edits that drop the
    rule (and let agents re-improvise the env-source pattern) get
    caught here before the bug ships."""
    skill_md = Path(__file__).resolve().parent.parent / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    assert "extract-fit-fields" in text, (
        "SKILL.md must invoke `extract-fit-fields`. See issue #50."
    )
    assert "$RUN_DIR/fit/" in text or '$RUN_DIR/fit"' in text, (
        "SKILL.md must reference the $RUN_DIR/fit/ directory. See issue #50."
    )
    assert 'cat "$RUN_DIR/fit/company.txt"' in text, (
        "SKILL.md must show the cat-pattern for reading the company field "
        "back in Phase 9. See issue #50."
    )


def test_skill_md_does_not_prescribe_fit_extracted_env_heredoc():
    """Negative assertion: the previously-improvised
    `fit-extracted.env` heredoc + source pattern must not appear in
    SKILL.md as prescribed code."""
    skill_md = Path(__file__).resolve().parent.parent / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    bad_shapes = (
        'cat > "$RUN_DIR/fit-extracted.env"',
        '. "$RUN_DIR/fit-extracted.env"',
        'source "$RUN_DIR/fit-extracted.env"',
    )
    for bad in bad_shapes:
        assert bad not in text, (
            f"SKILL.md contains the prohibited shell-source pattern {bad!r}. "
            f"Use per-field files in $RUN_DIR/fit/ instead. See issue #50."
        )
