"""SKILL.md prescriptions added in response to weak-model failure modes
observed under qwen3.6-35b on OpenCode (run ses_236d, issue #53 spike).

Each test pins one specific guidance string in SKILL.md so future edits
that drop the guidance fail loudly in CI rather than silently regressing
to the broken state. The bugs themselves were:

1. Phase 0 first-run setup wrote `cat > .resumasher/config.json` without
   a prior `mkdir -p .resumasher/` — the parent directory doesn't exist
   on a fresh student folder, so the redirect failed (`zsh: no such file
   or directory: ...`) and the orchestrator continued with an empty config.

2. Phase 2 cache-the-summary attempted `FOLDER_SUMMARY='...Ana\\'s capstone...'`
   — single-quoted shell strings cannot contain a literal `'` (no `\\'`
   escape inside `'...'`), so zsh died with `unmatched '` and `cache.txt`
   never got written. Fit-analyst then crashed with `FAILURE: ... requires
   variable 'folder_summary'`. Heredoc with a quoted delimiter is immune.

Same root-cause class as issues #44/#45/#46/#50 — SKILL.md prescribes a
shell pattern that didn't survive contact with real sub-agent text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL_MD = Path(__file__).resolve().parent.parent / "SKILL.md"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def test_phase_0_prescribes_mkdir_before_config_json_write(skill_text: str):
    """A fresh student folder has no `.resumasher/` directory; the
    Phase 0 example must show `mkdir -p` before the `cat >` redirect.
    Without it, the redirect fails on a fresh folder and the next phase
    runs against an empty config."""
    # Locate the relevant Phase 0 section by anchor.
    anchor = "Write `.resumasher/config.json` with those values"
    idx = skill_text.find(anchor)
    assert idx != -1, f"Phase 0 anchor {anchor!r} not found in SKILL.md"
    # The 600-char window after the anchor should contain both the
    # mkdir prescription AND the cat heredoc that follows.
    window = skill_text[idx:idx + 800]
    assert 'mkdir -p "$STUDENT_CWD/.resumasher"' in window, (
        "Phase 0 must prescribe `mkdir -p \"$STUDENT_CWD/.resumasher\"` "
        "before the config.json write. Weak models (qwen3.6-35b on "
        "OpenCode, run ses_236d) issued the redirect without the mkdir "
        "and zsh failed silently with 'no such file or directory'."
    )
    assert "<< 'CONFIGEOF'" in window, (
        "Phase 0 example should use a quoted-delimiter heredoc for the "
        "config.json body so embedded quotes/dollar-signs in name or "
        "location values pass through byte-literal."
    )


def test_phase_2_cache_save_uses_heredoc_not_single_quoted_var(skill_text: str):
    """Saving the folder-miner sub-agent's text response into cache.txt
    must use either the Write tool or a heredoc with a quoted delimiter.
    `var='content'; echo "$var" > file` breaks on apostrophes (e.g.
    'Ana's capstone') because single-quoted shell assignment cannot
    contain a literal `'`.
    """
    # Anchor: the "Cache the successful summary" subsection in Phase 2.
    anchor = "Cache the successful summary"
    idx = skill_text.find(anchor)
    assert idx != -1, f"Phase 2 anchor {anchor!r} not found in SKILL.md"
    window = skill_text[idx:idx + 1500]
    # Must prescribe heredoc form (quoted delimiter is byte-literal).
    assert "<< 'HEREDOC'" in window, (
        "Phase 2 cache save must show the `cat > file << 'HEREDOC'` "
        "pattern. The quoted delimiter makes the body byte-literal "
        "and immune to apostrophes / dollar-signs / backticks."
    )
    # Must explicitly forbid the broken pattern by name so future edits
    # don't accidentally reintroduce it.
    assert "FOLDER_SUMMARY='" in window, (
        "Phase 2 should reference the broken `FOLDER_SUMMARY='...'` "
        "anti-pattern by example so a future maintainer can see WHY "
        "we use the heredoc form. Caught under qwen3.6-35b on OpenCode "
        "(run ses_236d) — same bug class as #44/#45/#46/#50."
    )
    # Must explain the WHY (the apostrophe failure mode) so the
    # guidance is durable across paraphrasing edits.
    assert "apostrophe" in window.lower() or "unmatched" in window.lower(), (
        "Phase 2 cache save guidance must call out the apostrophe / "
        "unmatched-quote failure mode so the reasoning survives edits."
    )
