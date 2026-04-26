"""Tests for the per-host SKILL.md template system (issue #55).

These tests guard three invariants:

1. **No drift**: running ``python tools/gen_skill_md.py`` produces files that
   are byte-equivalent to the committed copies. If a maintainer edits
   SKILL.md.tmpl without regenerating, CI fails here.

2. **Per-host shape**: each generated file contains ONLY its own host's
   tool names and dispatch primitives. The whole point of templating is to
   prevent weak models from picking the wrong host's value (run ses_235c
   on OpenCode under qwen3.6-35b mis-dispatched with Claude Code's
   ``general-purpose`` subagent_type).

3. **install.sh wiring**: the per-host detection block exists and references
   each ``SKILL-<host>.md`` filename, so a future refactor can't silently
   drop the swap step.

The drift test is the workhorse — if it passes, you know every per-host
file matches the template output and you don't need to scan each file
manually for content correctness.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Add tools/ to sys.path so we can import the generator + host config from
# tests. Both modules are pure Python with no install-time dependencies.
sys.path.insert(0, str(REPO_ROOT / "tools"))

from gen_skill_md import (  # noqa: E402  -- after sys.path manipulation
    HOST_ORDER,
    DEFAULT_HOST,
    TEMPLATE_PATH,
    output_path_for,
    default_skill_md_path,
    render,
    generate_all,
)
from host_config import HOSTS  # noqa: E402


# ── Drift / regeneration ────────────────────────────────────────────────────


def test_template_file_exists():
    """Sanity check: SKILL.md.tmpl is present in the repo. Without it, every
    other test would fail with a confusing ENOENT."""
    assert TEMPLATE_PATH.exists(), (
        f"{TEMPLATE_PATH} is missing — generator can't run without it. "
        f"If you intended to delete the template, also delete tools/gen_skill_md.py "
        f"and these tests."
    )


def test_generator_check_mode_passes():
    """The committed SKILL-<host>.md / SKILL.md files match what the
    generator would produce right now. If this fails, run
    `python tools/gen_skill_md.py` and commit the changes."""
    exit_code = generate_all(check=True)
    assert exit_code == 0, (
        "Per-host SKILL files are out of sync with SKILL.md.tmpl. "
        "Run `python tools/gen_skill_md.py` and commit the regenerated files."
    )


def test_generator_idempotent():
    """Running the generator twice in a row produces the same output. Catches
    accidental in-place mutation of the template buffer or non-deterministic
    dict iteration order."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for host in HOST_ORDER:
        a = render(template, host)
        b = render(template, host)
        assert a == b, (
            f"render() produced different output on consecutive calls for "
            f"host={host!r}. Generator is non-deterministic."
        )


def test_all_per_host_files_committed():
    """One SKILL-<host>.md per host plus the default SKILL.md. The presence
    check is independent of the drift check above so a missing file gives a
    targeted error rather than 'drift' confusion."""
    for host in HOST_ORDER:
        path = output_path_for(host)
        assert path.exists(), (
            f"{path.name} is missing. Run `python tools/gen_skill_md.py` to "
            f"regenerate per-host SKILL files."
        )
    assert default_skill_md_path().exists(), "Default SKILL.md is missing."


def test_default_skill_md_matches_default_host_variant():
    """SKILL.md (the file the source tree ships and IDEs read) must be
    byte-equivalent to SKILL-<DEFAULT_HOST>.md. Otherwise a clone-without-
    install user gets one variant while install.sh would have given them
    another — confusing, and breaks test fixtures that read SKILL.md."""
    default_content = default_skill_md_path().read_text(encoding="utf-8")
    host_variant = output_path_for(DEFAULT_HOST).read_text(encoding="utf-8")
    assert default_content == host_variant, (
        f"SKILL.md != SKILL-{DEFAULT_HOST}.md. Re-run "
        f"`python tools/gen_skill_md.py` to sync them."
    )


# ── Per-host shape ──────────────────────────────────────────────────────────


def _read(host: str) -> str:
    return output_path_for(host).read_text(encoding="utf-8")


def test_no_unrendered_template_markers():
    """No file should contain ``{{`` or ``}}`` after rendering. A leftover
    marker means the template references a variable not present in
    host_config, or a typo'd ``{{#if}}`` block."""
    for host in HOST_ORDER:
        content = _read(host)
        # Allow `{{` and `}}` if they appear inside fenced code blocks for
        # legitimate reasons (e.g. shell substitutions). We don't have any
        # in SKILL.md today; if you add them, keep this assertion strict
        # and exclude them inline rather than loosening this check.
        assert "{{" not in content, (
            f"SKILL-{host}.md contains an unrendered `{{{{` marker. "
            f"Either the template references a missing variable or a "
            f"conditional block has a typo."
        )
        assert "}}" not in content, (
            f"SKILL-{host}.md contains an unrendered `}}}}` marker."
        )


def test_each_host_advertises_its_own_question_tool():
    """Each per-host file must reference its own question_tool with backticks
    at least once, and must NOT have backtick references to any other host's
    question tool. Plain English mentions of words like 'question' are fine —
    we only flag tool-name references, which by convention live inside
    backticks."""
    own = {h: HOSTS[h]["question_tool"] for h in HOST_ORDER}
    for host in HOST_ORDER:
        content = _read(host)
        # Tool-name pattern: a backtick-fenced occurrence. This avoids the
        # false positives from English words like "question" or "ask_user"
        # (the latter appears in prose phrases like "ask the user").
        own_pat = f"`{own[host]}`"
        assert own_pat in content, (
            f"SKILL-{host}.md does not have a backtick-fenced reference to "
            f"its own question tool `{own[host]}`. Either the template lost "
            f"the reference or the per-host config drifted."
        )
        for other_host, tool in own.items():
            if other_host == host or tool == own[host]:
                continue
            other_pat = f"`{tool}`"
            # Exception: the docstring-level intro mentions other hosts'
            # tools in a single line that explains the per-host generation
            # approach. Templating preserves "this skill runs on Claude
            # Code, Codex CLI, Gemini CLI, OpenCode" prose for context but
            # never names the OTHER tools as instructions. So we look for
            # backtick references that ARE in instructional context.
            #
            # Codex's literal `request_user_input` appears in the error
            # message string `"request_user_input requires non-empty
            # options"` quoted as an InputValidationError example, which
            # IS shown only in the codex variant. So this is fine.
            if other_pat in content:
                pytest.fail(
                    f"SKILL-{host}.md has a backtick reference to `{tool}` "
                    f"(belongs to host {other_host!r}). Per-host files must "
                    f"contain only their own host's tools as instructional "
                    f"references."
                )


def test_each_host_advertises_its_own_subagent_type_only():
    """Claude's `general-purpose` and OpenCode's `general` are the two
    failure-prone values. The OpenCode variant must NOT contain
    `general-purpose` (run ses_235c bug); the Claude variant must NOT
    contain `general"` as a standalone subagent_type literal."""
    claude = _read("claude")
    opencode = _read("opencode")
    assert "general-purpose" in claude
    assert "general-purpose" not in opencode, (
        "SKILL-opencode.md still mentions `general-purpose`. The whole "
        "point of templating was to remove this — it's the value that "
        "tripped qwen3.6-35b in run ses_235c."
    )
    assert 'subagent_type="general"' in opencode, (
        "SKILL-opencode.md is missing `subagent_type=\"general\"`."
    )
    # Claude shouldn't claim subagent_type="general" (without -purpose).
    assert 'subagent_type="general"' not in claude, (
        "SKILL-claude.md mentions `subagent_type=\"general\"` (OpenCode's value)."
    )


def test_each_host_uses_its_own_skill_dir_in_path_prologue():
    """The path prologue must list ONLY this host's skill_dir. Cross-host
    paths in the prologue are the original sin we're replacing."""
    for host in HOST_ORDER:
        content = _read(host)
        own_dir = HOSTS[host]["skill_dir"]
        assert f"$HOME/{own_dir}/resumasher" in content, (
            f"SKILL-{host}.md missing its own skill-dir path "
            f"`$HOME/{own_dir}/resumasher` in the prologue."
        )
        for other in HOST_ORDER:
            if other == host:
                continue
            other_dir = HOSTS[other]["skill_dir"]
            assert f"$HOME/{other_dir}/resumasher" not in content, (
                f"SKILL-{host}.md leaks `{other_dir}` (belongs to host "
                f"{other!r}) in its path prologue. The whole point of "
                f"per-host generation is that each host's prologue only "
                f"lists its own paths."
            )


def test_opencode_variant_contains_host_specific_notes():
    """OpenCode has the most distinct host-specific content: parallel
    flake note, OPENCODE_ENABLE_EXA, and the websearch caveat. Pin them
    so they don't get accidentally collapsed into a generic statement."""
    content = _read("opencode")
    assert "OPENCODE_ENABLE_EXA" in content, (
        "OpenCode variant missing the `OPENCODE_ENABLE_EXA=1` note for "
        "the company-researcher web-search dependency."
    )
    assert "sst/opencode#14195" in content, (
        "OpenCode variant missing the parallel-dispatch flake reference."
    )


def test_codex_variant_explains_no_native_dispatch_tool():
    """Codex CLI doesn't have a Task-shaped tool — the per-host file must
    explicitly explain the prose-instruction workaround (otherwise a
    weak model on Codex would hunt for a tool that doesn't exist)."""
    content = _read("codex")
    assert "spawn a sub-agent" in content
    assert "does not expose a single named sub-agent tool" in content
    # And we should NOT have said "use the Task tool" in the codex variant.
    assert "`Task` tool" not in content, (
        "Codex variant mentions the `Task` tool, which Codex CLI doesn't ship."
    )


def test_gemini_variant_uses_at_generalist():
    content = _read("gemini")
    assert "@generalist" in content
    # Gemini should not claim subagent_type — its @generalist sub-agent
    # doesn't take that parameter.
    assert "general-purpose" not in content
    assert 'subagent_type="general"' not in content


# ── install.sh wiring ───────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def install_sh_text() -> str:
    return (REPO_ROOT / "install.sh").read_text(encoding="utf-8")


def test_install_sh_detects_each_host(install_sh_text: str):
    """install.sh must have a case statement that maps each canonical
    install path (.claude/.codex/.gemini/.opencode/skills/resumasher) to
    the matching DETECTED_HOST. A regression that drops one branch would
    silently install the Claude variant for the missing host's users."""
    for host in HOST_ORDER:
        skill_dir = HOSTS[host]["skill_dir"]
        # The case-statement pattern in install.sh
        pattern = f"*/{skill_dir}/resumasher) DETECTED_HOST={host}"
        assert pattern in install_sh_text, (
            f"install.sh is missing the host-detection branch for {host!r}. "
            f"Expected `{pattern} ;;` in the case statement."
        )


def test_install_sh_swaps_per_host_skill_md(install_sh_text: str):
    """install.sh must reference SKILL-<DETECTED_HOST>.md as the source
    file when copying the variant over SKILL.md. Without this, the
    detection above would set the variable but never act on it."""
    assert "SKILL-$DETECTED_HOST.md" in install_sh_text, (
        "install.sh detects the host but doesn't copy the matching variant. "
        "Expected `cp $SCRIPT_DIR/SKILL-$DETECTED_HOST.md $SCRIPT_DIR/SKILL.md`."
    )


def test_install_sh_default_falls_back_to_claude(install_sh_text: str):
    """A clone to a non-canonical path (e.g. /opt/resumasher/) should fall
    back to the Claude variant rather than failing or leaving SKILL.md
    untouched. The committed SKILL.md is already the Claude variant, so
    'do nothing' is correct fallback behavior — but this test pins the
    intent so a future refactor doesn't accidentally start failing here."""
    # The case statement's catch-all branch
    assert "*) DETECTED_HOST=claude" in install_sh_text, (
        "install.sh is missing the catch-all `*) DETECTED_HOST=claude` "
        "branch in its host-detection case statement. Without it, a clone "
        "to a non-standard path would leave DETECTED_HOST undefined."
    )


# ── Generator CLI sanity ────────────────────────────────────────────────────


def test_generator_check_runs_via_cli():
    """`python tools/gen_skill_md.py --check` exits 0 in a clean tree.
    Catches the case where the script itself is broken (import error,
    missing argparse setup, etc.) — separate from drift."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "gen_skill_md.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"gen_skill_md.py --check failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
