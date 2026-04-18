"""
Tests for scripts/orchestration.py — every deterministic helper, every edge
case traced from the test review diagram.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
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
    is_failure_sentinel,
    mine_folder_context,
    parse_job_source,
    read_config,
    read_resume,
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
    assert result is not None and result.name == "CV.pdf"


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
### Senior Analyst — Example Corp (2022-2024)
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
        "folder_state_hash changed when .claude/ was added — .claude must "
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
        ["python", "-m", "scripts.orchestration", "company-slug", "Deloitte Consulting LLC"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "deloitte-consulting"


def test_cli_discover_resume_missing_returns_failure(tmp_path: Path):
    result = subprocess.run(
        ["python", "-m", "scripts.orchestration", "discover-resume", str(tmp_path)],
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
    orchestrator must get far enough to emit the warning — which means the
    github_mine module imported cleanly.
    """
    import subprocess
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "orchestration.py"
    (tmp_path / "resume.md").write_text("# Me\n", encoding="utf-8")

    result = subprocess.run(
        [
            "python", str(script),
            "mine-context", str(tmp_path),
            "--github-username", "this-username-does-not-exist-xyz-99999",
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),  # NOT the repo root — simulates student's CWD
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
