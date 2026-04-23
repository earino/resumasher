"""
Drift-detection for the two SKILL.md path prologue blocks.

SKILL.md contains the skill-root discovery prologue in two places: once at
the top (for every Bash call the main pipeline makes) and once inside the
"Re-rendering PDFs after manual edits" section (for re-render flows that
enter SKILL.md without going through the main pipeline). Shell state does
not persist between Bash tool calls, so both entry points need their own
bootstrapping block.

The two copies are *not* required to be byte-identical — Copy A carries a
friendly "venv missing → run install.sh" error branch that Copy B
intentionally omits (re-render is always triggered after a successful
install, so the error case is less interesting there). But they MUST stay
aligned on the venv-Python discovery itself. Issue #32 exposed what
happens when they don't: a Windows venv-layout fix has to be applied to
both, and "forget the second copy" is a silent-break class of bug.

This test pins the invariant: both prologues check both venv-Python paths
(POSIX `.venv/bin/python` AND Windows `.venv/Scripts/python.exe`). Any
future cross-cutting change to the discovery logic that touches one copy
without the other will fail here instead of shipping.

If one day the two prologues get factored into a shared helper (a shell
function, a `bin/resumasher-locate` script, whatever), delete this test —
it will have done its job.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_MD = Path(__file__).resolve().parent.parent / "SKILL.md"

# The fingerprint of a path prologue: the candidate-scan loop header. Both
# copies start with this exact line, and nothing else in SKILL.md does.
PROLOGUE_LOOP_SENTINEL = 'for c in \\'


def _extract_prologues(text: str) -> list[str]:
    """Return the two prologue blocks as strings.

    A prologue is the block from ``for c in \\`` through the closing
    ``done`` line. We slice forward from each sentinel until we hit the
    loop body's terminator.
    """
    blocks: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip() == PROLOGUE_LOOP_SENTINEL:
            start = i
            # Walk forward until we find the `done` that closes the loop.
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


def test_skill_md_has_exactly_two_prologues():
    """If this count changes, the duplication assumption in this test
    file is out of date. Update the test; don't silently paper over it."""
    text = SKILL_MD.read_text(encoding="utf-8")
    prologues = _extract_prologues(text)
    assert len(prologues) == 2, (
        f"Expected exactly 2 path prologues in SKILL.md; found {len(prologues)}. "
        f"If you added or removed a prologue, update this test to match."
    )


def test_both_prologues_check_posix_venv_python():
    """POSIX layout: .venv/bin/python. Required on macOS/Linux."""
    text = SKILL_MD.read_text(encoding="utf-8")
    for idx, block in enumerate(_extract_prologues(text)):
        assert ".venv/bin/python" in block, (
            f"SKILL.md prologue #{idx + 1} is missing the .venv/bin/python "
            f"check. Both prologues must check the POSIX venv layout.\n\n"
            f"Block was:\n{block}"
        )


def test_both_prologues_check_windows_venv_python():
    """Windows layout: .venv/Scripts/python.exe. Required on Git Bash
    (see issue #32)."""
    text = SKILL_MD.read_text(encoding="utf-8")
    for idx, block in enumerate(_extract_prologues(text)):
        assert ".venv/Scripts/python.exe" in block, (
            f"SKILL.md prologue #{idx + 1} is missing the "
            f".venv/Scripts/python.exe check. Both prologues must check the "
            f"Windows venv layout so Git Bash installs keep working "
            f"(regression guard for issue #32).\n\nBlock was:\n{block}"
        )


def _extract_candidate_roots(block: str) -> tuple[str, ...]:
    """Parse the ``for c in ... ; do`` header of a prologue and return the
    ordered tuple of candidate install directories. Stops at ``; do`` so
    loop-body variables like ``$c`` aren't mistaken for candidates."""
    lines = block.splitlines()
    # Find the terminator: the line containing `; do` closes the for-list.
    header_end = None
    for idx, line in enumerate(lines):
        if "; do" in line:
            header_end = idx
            break
    assert header_end is not None, (
        f"Prologue loop header has no '; do' terminator:\n{block}"
    )
    header = "\n".join(lines[: header_end + 1])
    return tuple(re.findall(r'"(\$[^"]+)"', header))


def test_both_prologues_scan_same_candidate_roots():
    """The list of candidate install locations (user-scope + project-scope
    across Claude / Codex / Gemini CLIs) must be identical between the two
    prologues. If the main pipeline can find a skill at
    ``$HOME/.gemini/skills/resumasher`` but the re-render path can't, the
    student gets mysterious behavior — a re-render that "can't find the
    skill" on an install that clearly works."""
    text = SKILL_MD.read_text(encoding="utf-8")
    prologues = _extract_prologues(text)
    candidate_sets = [_extract_candidate_roots(block) for block in prologues]
    assert candidate_sets[0] == candidate_sets[1], (
        "The two SKILL.md prologues scan DIFFERENT sets of candidate "
        "install directories. They must stay aligned or the re-render "
        "flow will diverge from the main pipeline.\n\n"
        f"Copy A candidates: {candidate_sets[0]}\n"
        f"Copy B candidates: {candidate_sets[1]}"
    )
