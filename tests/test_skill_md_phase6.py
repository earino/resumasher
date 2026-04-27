"""SKILL.md Phase 6 wording assertions (issue #29).

The Phase 6 instructions to the orchestrator were ambiguous — "Save the
outputs to $OUT_DIR/cover-letter.md and $OUT_DIR/interview-prep.md" left
room for a weaker orchestrator to interpret as "scan the filesystem for
files the sub-agent wrote and move them there." Issue #29 hardened the
wording to be explicit about taking the sub-agent's text response and
using Write to save it. These tests prevent regression of that wording.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_MD = Path(__file__).resolve().parent.parent / "SKILL.md"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def _phase_6_block(text: str) -> str:
    """Return the Phase 6 portion of SKILL.md (rough-cut: from the
    interview-coach build-prompt invocation through the cleanup scan)."""
    start_marker = "**Build the interview-coach prompt:**"
    end_marker = "### Phase 7"
    start = text.find(start_marker)
    end = text.find(end_marker, start)
    assert start != -1, f"could not locate {start_marker!r} in SKILL.md"
    assert end != -1, "could not locate Phase 7 boundary"
    return text[start:end]


def test_phase_6_instructs_orchestrator_to_use_write_or_heredoc_explicitly(skill_text: str):
    """The orchestrator must be told to save the sub-agent's text response
    via a specific shell-free or shell-safe mechanism — not the ambiguous
    'save the outputs' that caused issue #29.

    Two acceptable mechanisms:
    - The Write tool (Claude Code / OpenCode) — bypasses shell entirely.
    - A heredoc with a quoted delimiter (`<< 'HEREDOC'`) — byte-literal
      and immune to apostrophes, dollar signs, and backticks in the
      sub-agent's response. Required for hosts that don't ship a Write
      tool (Codex, Gemini), and a safe fallback everywhere.

    What's forbidden: assigning the response to a single-quoted shell
    variable and echoing it. That breaks on apostrophes (`Ana's capstone`,
    `client's request`) with `unmatched '` and produces empty files."""
    block = _phase_6_block(skill_text)
    has_write = "Write tool" in block
    has_heredoc = "<< 'HEREDOC'" in block or "heredoc" in block.lower()
    assert has_write and has_heredoc, (
        "Phase 6 must explicitly prescribe BOTH the Write tool AND a "
        "heredoc-with-quoted-delimiter as the two safe save mechanisms "
        "for sub-agent text responses. Single-quoted shell variables "
        "break on apostrophes (issue #29 + the qwen3.6-35b OpenCode runs)."
    )


def test_phase_6_warns_against_filesystem_scanning(skill_text: str):
    """The orchestrator must be told NOT to look on the filesystem for
    files the sub-agent may have written. That behavior IS the bug."""
    block = _phase_6_block(skill_text)
    assert "Do NOT scan the filesystem" in block, (
        "Phase 6 must warn the orchestrator not to scan the filesystem for "
        "rogue sub-agent writes. Without this, a 'recovery' orchestrator "
        "would normalize the rogue-write-then-scavenge pattern instead of "
        "ignoring the rogue file."
    )


def test_phase_6_invokes_cleanup_scan(skill_text: str):
    """Phase 6 must invoke the cleanup-stray-outputs subcommand as
    defense-in-depth."""
    block = _phase_6_block(skill_text)
    assert "cleanup-stray-outputs" in block, (
        "Phase 6 must invoke `orchestration cleanup-stray-outputs` after "
        "the sub-agents return. This is the belt that survives even if the "
        "prompt surgery (the suspenders) regresses on a future model."
    )


def test_phase_6_captures_dispatch_timestamp(skill_text: str):
    """The cleanup scan needs a 'files newer than this' cutoff. Phase 6
    must capture a dispatch timestamp before launching the sub-agents."""
    block = _phase_6_block(skill_text)
    assert "DISPATCH_TS" in block, (
        "Phase 6 must capture a dispatch timestamp before sub-agent "
        "dispatch and pass it to cleanup-stray-outputs --since-timestamp. "
        "Without it, the scan can't distinguish rogue writes from "
        "pre-existing student files."
    )


def test_phase_6_references_issue_29(skill_text: str):
    """Future maintainers reading the explicit wording deserve a pointer
    to the issue that motivated it, so they don't 'simplify' it back."""
    block = _phase_6_block(skill_text)
    assert "#29" in block or "issue 29" in block.lower(), (
        "Phase 6 must reference issue #29 so future edits don't "
        "accidentally revert the explicit wording."
    )
