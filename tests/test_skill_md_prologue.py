"""
Drift-detection for the SKILL.md path prologue.

SKILL.md contains exactly one skill-root discovery prologue, at the top of
the Workflow section. Every Bash tool call the orchestrator issues must
begin with that prologue (shell state does not persist across calls).

The prologue MUST check both the POSIX (`.venv/bin/python`) and Windows
(`.venv/Scripts/python.exe`) venv layouts. Issue #32 was a Windows install
that broke because the prologue only checked the POSIX path. This test
pins both checks so the regression can't ship.

Historical note: prior to issue #58 (the v0.5 simplification PR) there
were TWO prologues in SKILL.md — one for the main pipeline and one inside
a separate "Re-rendering PDFs" section that students hit when asking the
agent to re-render after manual edits. Re-rendering was folded into
Phase 8, eliminating the duplicate prologue and the cross-prologue drift
class of bug. The test was rewritten to assert ONE prologue accordingly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL_MD = Path(__file__).resolve().parent.parent / "SKILL.md"

# The fingerprint of the path prologue: the candidate-scan loop header.
# Nothing else in SKILL.md should match this exact line.
PROLOGUE_LOOP_SENTINEL = "for c in \\"


def _extract_prologues(text: str) -> list[str]:
    """Return every prologue block in SKILL.md.

    A prologue is the block from ``for c in \\`` through the closing
    ``done`` line.
    """
    blocks: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip() == PROLOGUE_LOOP_SENTINEL:
            start = i
            j = i + 1
            while j < len(lines) and lines[j].strip() != "done":
                j += 1
            if j == len(lines):
                pytest.fail(
                    f"SKILL.md prologue starting at line {start + 1} has no "
                    f"closing 'done' — malformed prologue block."
                )
            blocks.append("\n".join(lines[start : j + 1]))
            i = j + 1
        else:
            i += 1
    return blocks


def test_skill_md_has_exactly_one_prologue():
    """One prologue, used by the orchestrator for every Bash tool call.
    If a second prologue is reintroduced (e.g., a future maintainer copy-
    pastes it into a new section), update this test consciously rather than
    silently. The whole-file `for c in \\` sentinel is unique enough to
    catch accidental duplication."""
    text = SKILL_MD.read_text(encoding="utf-8")
    prologues = _extract_prologues(text)
    assert len(prologues) == 1, (
        f"Expected exactly 1 path prologue in SKILL.md; found {len(prologues)}. "
        f"If you added or removed a prologue, update this test to match."
    )


def test_prologue_checks_posix_venv_python():
    """POSIX layout: `.venv/bin/python`. Required on macOS/Linux."""
    text = SKILL_MD.read_text(encoding="utf-8")
    [block] = _extract_prologues(text)
    assert ".venv/bin/python" in block, (
        f"SKILL.md prologue is missing the `.venv/bin/python` check.\n\n"
        f"Block was:\n{block}"
    )


def test_prologue_checks_windows_venv_python():
    """Windows layout: `.venv/Scripts/python.exe`. Required on Git Bash
    (regression guard for issue #32)."""
    text = SKILL_MD.read_text(encoding="utf-8")
    [block] = _extract_prologues(text)
    assert ".venv/Scripts/python.exe" in block, (
        f"SKILL.md prologue is missing the `.venv/Scripts/python.exe` "
        f"check. Required so Git Bash installs keep working "
        f"(regression guard for issue #32).\n\nBlock was:\n{block}"
    )
