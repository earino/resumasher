"""OpenCode slash-command shim assertions.

OpenCode resolves `/resumasher <args>` by reading
`~/.config/opencode/commands/resumasher.md` and substituting `$ARGUMENTS`
into its body. Without the shim, OpenCode falls back to pasting the full
SKILL.md as a user message and silently drops the argument — observed
under qwen3.6-35b in run ses_235c, where the model replied "I've loaded
the resumasher skill. What would you like me to do?" instead of
executing.

These tests pin (a) the shim file exists at the canonical repo location,
(b) it has correct YAML frontmatter, (c) the body invokes the skill and
references `$ARGUMENTS`, and (d) install.sh installs it when OpenCode is
detected. Future edits that drop any of these break the slash-command
wiring on OpenCode and a regression appears as "loading the skill but
not running it" — silent degradation that's hard to spot in a transcript.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIM_PATH = REPO_ROOT / "commands" / "resumasher.md"
INSTALL_SH = REPO_ROOT / "install.sh"


def test_shim_file_exists():
    """The shim must live at <repo>/commands/resumasher.md so install.sh
    can find it via $SCRIPT_DIR/commands/resumasher.md."""
    assert SHIM_PATH.exists(), (
        f"Slash-command shim missing at {SHIM_PATH}. OpenCode users will "
        "type /resumasher and OpenCode will paste SKILL.md as a user "
        "message instead of invoking the skill — silent degradation."
    )


def test_shim_has_yaml_frontmatter():
    """OpenCode's command-file parser requires YAML frontmatter with a
    `description` field — without it, the file isn't recognized as a
    command and the slash command falls back to skill auto-discovery."""
    text = SHIM_PATH.read_text(encoding="utf-8")
    assert text.startswith("---\n"), (
        "Shim must open with `---\\n` YAML frontmatter delimiter. "
        "Without it, OpenCode treats the file as plain markdown and "
        "the slash command isn't registered."
    )
    # description: line must appear in the frontmatter (between the
    # opening and closing `---`).
    head = text.split("\n---\n", 1)[0]
    assert "description:" in head, (
        "Shim YAML frontmatter must include a `description:` field. "
        "OpenCode displays this in the command palette."
    )


def test_shim_body_invokes_skill():
    """The body must explicitly tell the model to use the resumasher
    skill. Without this instruction, the model receives the body as
    a generic prompt and may not load the skill at all."""
    body = SHIM_PATH.read_text(encoding="utf-8")
    # Must mention the skill by name.
    assert "resumasher" in body.lower(), (
        "Shim body must reference the resumasher skill by name."
    )
    # Must use `$ARGUMENTS` (OpenCode's argument substitution token) so
    # the JD path / URL / literal text the user typed gets passed through.
    assert "$ARGUMENTS" in body, (
        "Shim body must reference `$ARGUMENTS` so OpenCode substitutes "
        "the user's job-source argument into the body. Without it, "
        "the user's `/resumasher jd.md` argument is dropped."
    )


def test_shim_has_no_argument_fallback():
    """Per Phase 0 docs, the skill should prompt the student if it has
    no JD source. The shim should explicitly handle the empty-argument
    case so the model knows to ask rather than fail."""
    body = SHIM_PATH.read_text(encoding="utf-8")
    body_lower = body.lower()
    # Body should mention prompting or asking when arguments are empty.
    assert any(kw in body_lower for kw in ["empty", "prompt", "ask the student"]), (
        "Shim body should handle the no-argument case (e.g., `/resumasher` "
        "with no JD source) by prompting the student rather than silently "
        "running against an undefined source."
    )


def test_install_sh_installs_shim_when_opencode_detected():
    """install.sh must (a) source the shim from $SCRIPT_DIR/commands/resumasher.md,
    (b) write to $XDG_CONFIG_HOME/opencode/commands/ (default ~/.config/),
    (c) gate on `command -v opencode` so it's a no-op when OpenCode isn't
    installed, (d) be idempotent (no-op if already installed)."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    # Reads the shim from the repo
    assert "commands/resumasher.md" in text, (
        "install.sh must reference `commands/resumasher.md` so the shim "
        "from this repo is the source of truth."
    )
    # Writes to the OpenCode commands directory
    assert "opencode/commands" in text, (
        "install.sh must write to `<config>/opencode/commands/` "
        "(OpenCode's command-file directory)."
    )
    # Honors XDG_CONFIG_HOME (Linux desktop convention) with a $HOME fallback
    assert "XDG_CONFIG_HOME" in text and "$HOME/.config" in text, (
        "install.sh must honor XDG_CONFIG_HOME with a $HOME/.config "
        "fallback so it works on both Linux desktops and macOS."
    )
    # Gates on opencode being available (silent skip otherwise)
    assert "command -v opencode" in text, (
        "install.sh must gate the shim install on `command -v opencode` "
        "so users without OpenCode aren't surprised by writes outside "
        "the skill directory."
    )


def test_install_sh_warns_on_low_opencode_tool_output_cap():
    """install.sh must READ (never write) the user's opencode config and
    warn if `tool_output.max_bytes` is smaller than SKILL.md's size.
    OpenCode's default cap is 51,200 bytes; SKILL.md is ~82KB. Without
    the warning, students on weak local models hit silent truncation
    of the back half of SKILL.md (Phases 7-9), shipping wrong PDF
    filenames + missing artifacts — see samples-issue42/session-ses_2359.md.
    """
    text = INSTALL_SH.read_text(encoding="utf-8")
    # The check itself: reads max_bytes, references the OpenCode default
    assert "tool_output" in text, (
        "install.sh must reference `tool_output` (OpenCode's config key)."
    )
    assert "max_bytes" in text, (
        "install.sh must read `max_bytes` specifically — not `max_lines`, "
        "since the truncation we hit is byte-bounded (SKILL.md fits the "
        "line cap easily but not the byte cap)."
    )
    assert "51200" in text, (
        "install.sh must reference 51200 as the documented OpenCode "
        "default. Hardcoding the default lets us warn even when the user "
        "has no opencode.json yet — they're still at 51200 by inheritance."
    )
    # The fix it suggests
    assert "102400" in text, (
        "install.sh must suggest 102400 (100KB) as the recommended "
        "value — double the default, fits SKILL.md plus growth headroom."
    )
    # Read-only contract: no jq install, no config write, no merge logic
    assert "cp " not in _opencode_config_block(text), (
        "install.sh must NOT copy/write to the opencode config file. "
        "Read-only is the user-respecting contract for this detection."
    )
    assert ">$OPENCODE_CONFIG" not in text and "> \"$OPENCODE_CONFIG\"" not in text, (
        "install.sh must NOT redirect output INTO the opencode config "
        "file under any branch."
    )


def _opencode_config_block(text: str) -> str:
    """Extract the section of install.sh that handles the OpenCode
    tool_output cap detection — bounded by `OPENCODE_CONFIG=` and the
    next blank-line-terminated `fi`."""
    start = text.find("OPENCODE_CONFIG=")
    assert start != -1, "could not locate OPENCODE_CONFIG= marker in install.sh"
    end_marker = text.find("\nfi\n", start)
    assert end_marker != -1, "could not locate end of OpenCode config block"
    return text[start:end_marker]
