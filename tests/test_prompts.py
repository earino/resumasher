"""
Tests for scripts.prompts — the sub-agent prompt builder.

Every assertion here defends against the bug class that broke Gemini's
fit-analyst: a sub-agent dispatch going out with a prompt that contains
unfilled ``{resume_text}`` / ``{folder_summary}`` / ``{jd_text}`` tokens.
Previously these substitutions were the orchestrator LLM's responsibility.
Now they're deterministic Python, and these tests prove it.

The schema blocks inside several prompts contain literal template markers
like ``{Full Name}``, ``{Company}``, ``{Role Title}`` that are instructions
to the LLM to fill in its own output — NOT variables we substitute. The
build function uses targeted ``str.replace`` (not ``.format()``) so those
literals pass through untouched. There's an explicit test for that below.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.prompts import PROMPT_KINDS, build_prompt


# ---------------------------------------------------------------------------
# Basic substitution coverage — one test per kind
# ---------------------------------------------------------------------------


def test_folder_miner_substitutes_folder_context():
    p = build_prompt("folder-miner", folder_context="FOLDER_CONTEXT_MARKER")
    assert "FOLDER_CONTEXT_MARKER" in p
    assert "{folder_context}" not in p


def test_fit_analyst_substitutes_all_three():
    p = build_prompt(
        "fit-analyst",
        resume_text="RESUME_MARKER",
        folder_summary="EVIDENCE_MARKER",
        jd_text="JD_MARKER",
    )
    assert "RESUME_MARKER" in p
    assert "EVIDENCE_MARKER" in p
    assert "JD_MARKER" in p
    assert "{resume_text}" not in p
    assert "{folder_summary}" not in p
    assert "{jd_text}" not in p


def test_company_researcher_substitutes_company():
    p = build_prompt("company-researcher", company="Acme Corp")
    assert "Acme Corp" in p
    assert "{company}" not in p


def test_tailor_substitutes_all_three():
    p = build_prompt(
        "tailor",
        resume_text="RESUME_MARKER",
        folder_summary="EVIDENCE_MARKER",
        jd_text="JD_MARKER",
    )
    assert "RESUME_MARKER" in p
    assert "EVIDENCE_MARKER" in p
    assert "JD_MARKER" in p
    assert "{resume_text}" not in p
    assert "{folder_summary}" not in p
    assert "{jd_text}" not in p


def test_cover_letter_substitutes_all_three():
    p = build_prompt(
        "cover-letter",
        tailored_resume="TAILORED_MARKER",
        jd_text="JD_MARKER",
        company_research="RESEARCH_MARKER",
    )
    assert "TAILORED_MARKER" in p
    assert "JD_MARKER" in p
    assert "RESEARCH_MARKER" in p
    assert "{tailored_resume}" not in p
    assert "{jd_text}" not in p
    assert "{company_research}" not in p


def test_interview_coach_substitutes_all_three():
    p = build_prompt(
        "interview-coach",
        tailored_resume="TAILORED_MARKER",
        folder_summary="EVIDENCE_MARKER",
        jd_text="JD_MARKER",
    )
    assert "TAILORED_MARKER" in p
    assert "EVIDENCE_MARKER" in p
    assert "JD_MARKER" in p
    assert "{tailored_resume}" not in p
    assert "{folder_summary}" not in p
    assert "{jd_text}" not in p


# ---------------------------------------------------------------------------
# The crucial invariant: schema-marker literals pass through untouched
# ---------------------------------------------------------------------------
#
# The tailor's schema block contains things like ``{Full Name}``, ``{Title}``,
# ``{Company}``. The interview-coach's structure contains ``{Role Title}``,
# ``{question 1 title}``, etc. These are NOT variables we substitute; they
# are instructions to the downstream LLM to fill in its own output. If a
# naive ``.format()`` call ever sneaks into build_prompt, these tests blow
# up and stop the bug at CI.


def test_tailor_preserves_schema_literals():
    p = build_prompt("tailor", resume_text="R", folder_summary="E", jd_text="J")
    # These must survive untouched — they're LLM output schema markers.
    for literal in ("{Full Name}", "{Title}", "{Company}", "{Degree}", "{Institution}"):
        assert literal in p, f"schema literal {literal!r} was eaten by build_prompt"


def test_cover_letter_preserves_schema_literals():
    p = build_prompt(
        "cover-letter",
        tailored_resume="R",
        jd_text="J",
        company_research="C",
    )
    # The greeting line template: "# Dear {Company} Hiring Team,"
    assert "{Company}" in p


def test_interview_coach_preserves_schema_literals():
    p = build_prompt(
        "interview-coach",
        tailored_resume="R",
        folder_summary="E",
        jd_text="J",
    )
    for literal in ("{Role Title}", "{Company}", "{question 1 title}", "{question 2 title}"):
        assert literal in p, f"schema literal {literal!r} was eaten by build_prompt"


# ---------------------------------------------------------------------------
# Safety rails: every prompt kind renders without literal unfilled tokens
# ---------------------------------------------------------------------------


KIND_FIXTURES: dict[str, dict[str, str]] = {
    "folder-miner": {"folder_context": "FOLDER"},
    "fit-analyst": {"resume_text": "R", "folder_summary": "E", "jd_text": "J"},
    "company-researcher": {"company": "Acme"},
    "tailor": {"resume_text": "R", "folder_summary": "E", "jd_text": "J"},
    "cover-letter": {"tailored_resume": "R", "jd_text": "J", "company_research": "C"},
    "interview-coach": {"tailored_resume": "R", "folder_summary": "E", "jd_text": "J"},
}


@pytest.mark.parametrize("kind", sorted(KIND_FIXTURES))
def test_no_required_var_leaks(kind: str):
    """
    After build_prompt returns, none of the DECLARED input-variable tokens
    for that kind should still appear. The regression we're preventing is
    Gemini's fit-analyst receiving a literal "{resume_text}" in its prompt.
    """
    p = build_prompt(kind, **KIND_FIXTURES[kind])
    spec = PROMPT_KINDS[kind]
    for var in spec.required_vars:
        token = "{" + var + "}"
        assert token not in p, (
            f"Required variable {var!r} was not substituted in kind {kind!r}. "
            f"Token {token!r} still present in output."
        )


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_unknown_kind_raises():
    with pytest.raises(ValueError, match="Unknown prompt kind"):
        build_prompt("not-a-real-kind", resume_text="R")


def test_missing_required_var_raises():
    with pytest.raises(ValueError, match="requires"):
        build_prompt("fit-analyst", resume_text="R", folder_summary="E")
        # jd_text missing


def test_empty_string_is_allowed_not_missing():
    """
    Distinguish 'variable not supplied' (None → error) from 'variable is
    empty' (empty string → substituted cleanly). An empty JD is weird but
    not malformed; only a None supply is actionable-error territory.
    """
    p = build_prompt(
        "fit-analyst",
        resume_text="R",
        folder_summary="E",
        jd_text="",
    )
    assert "{jd_text}" not in p


# ---------------------------------------------------------------------------
# CLI end-to-end: the build-prompt subcommand reads files, substitutes, prints
# ---------------------------------------------------------------------------


@pytest.fixture
def skill_tree(tmp_path: Path) -> Path:
    """
    Set up a minimal student CWD that build-prompt can read from:
      cwd/
        .resumasher/
          run/
            resume.txt
            context.txt
            jd.txt
          cache.txt
        applications/biohub-20260418/
          company-research.md
          tailored-resume.md
    """
    run = tmp_path / ".resumasher" / "run"
    run.mkdir(parents=True)
    (run / "resume.txt").write_text("RESUME_FILE_CONTENT", encoding="utf-8")
    (run / "context.txt").write_text("CONTEXT_FILE_CONTENT", encoding="utf-8")
    (run / "jd.txt").write_text("JD_FILE_CONTENT", encoding="utf-8")

    cache = tmp_path / ".resumasher" / "cache.txt"
    cache.write_text("CACHE_FILE_CONTENT", encoding="utf-8")

    out = tmp_path / "applications" / "biohub-20260418"
    out.mkdir(parents=True)
    (out / "company-research.md").write_text("RESEARCH_FILE_CONTENT", encoding="utf-8")
    (out / "tailored-resume.md").write_text("TAILORED_FILE_CONTENT", encoding="utf-8")

    return tmp_path


def _run_build_prompt(*argv: str) -> subprocess.CompletedProcess[str]:
    """Invoke the orchestration.py CLI as a subprocess."""
    cmd = [sys.executable, "-m", "scripts.orchestration", "build-prompt", *argv]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_cli_build_prompt_fit_analyst(skill_tree: Path):
    r = _run_build_prompt("--kind", "fit-analyst", "--cwd", str(skill_tree))
    assert r.returncode == 0, r.stderr
    assert "RESUME_FILE_CONTENT" in r.stdout
    assert "CACHE_FILE_CONTENT" in r.stdout
    assert "JD_FILE_CONTENT" in r.stdout
    assert "{resume_text}" not in r.stdout


def test_cli_build_prompt_folder_miner(skill_tree: Path):
    r = _run_build_prompt("--kind", "folder-miner", "--cwd", str(skill_tree))
    assert r.returncode == 0, r.stderr
    assert "CONTEXT_FILE_CONTENT" in r.stdout
    assert "{folder_context}" not in r.stdout


def test_cli_build_prompt_company_researcher(skill_tree: Path):
    r = _run_build_prompt(
        "--kind", "company-researcher",
        "--cwd", str(skill_tree),
        "--company", "Biohub",
    )
    assert r.returncode == 0, r.stderr
    assert "Biohub" in r.stdout
    assert "{company}" not in r.stdout


def test_cli_build_prompt_cover_letter(skill_tree: Path):
    r = _run_build_prompt(
        "--kind", "cover-letter",
        "--cwd", str(skill_tree),
        "--out-dir", str(skill_tree / "applications" / "biohub-20260418"),
    )
    assert r.returncode == 0, r.stderr
    assert "TAILORED_FILE_CONTENT" in r.stdout
    assert "JD_FILE_CONTENT" in r.stdout
    assert "RESEARCH_FILE_CONTENT" in r.stdout


def test_cli_build_prompt_interview_coach(skill_tree: Path):
    r = _run_build_prompt(
        "--kind", "interview-coach",
        "--cwd", str(skill_tree),
        "--out-dir", str(skill_tree / "applications" / "biohub-20260418"),
    )
    assert r.returncode == 0, r.stderr
    assert "TAILORED_FILE_CONTENT" in r.stdout
    assert "CACHE_FILE_CONTENT" in r.stdout
    assert "JD_FILE_CONTENT" in r.stdout


def test_cli_build_prompt_tailor(skill_tree: Path):
    r = _run_build_prompt("--kind", "tailor", "--cwd", str(skill_tree))
    assert r.returncode == 0, r.stderr
    assert "RESUME_FILE_CONTENT" in r.stdout
    assert "CACHE_FILE_CONTENT" in r.stdout
    assert "JD_FILE_CONTENT" in r.stdout


def test_cli_build_prompt_missing_file_exits_2(tmp_path: Path):
    """
    If a required file doesn't exist, the CLI should exit 2 with a clear
    error message naming the missing file and the phase that produces it.
    Exit code 2 (not 1) so orchestrator scripts can distinguish "file not
    ready" from "unknown kind" or other errors.
    """
    r = _run_build_prompt("--kind", "fit-analyst", "--cwd", str(tmp_path))
    assert r.returncode == 2
    assert "FAILURE" in r.stderr
    assert "resume.txt" in r.stderr


def test_cli_build_prompt_missing_company_exits_2(skill_tree: Path):
    r = _run_build_prompt("--kind", "company-researcher", "--cwd", str(skill_tree))
    assert r.returncode == 2
    assert "--company" in r.stderr


def test_cli_build_prompt_missing_out_dir_exits_2(skill_tree: Path):
    r = _run_build_prompt("--kind", "cover-letter", "--cwd", str(skill_tree))
    assert r.returncode == 2
    assert "--out-dir" in r.stderr
