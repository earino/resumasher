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
