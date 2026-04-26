"""Per-host metadata used by tools/gen_skill_md.py.

resumasher runs on four AI CLI hosts (Claude Code, Codex CLI, Gemini CLI,
OpenCode). Each host has its own tool names and dispatch primitives, so the
SKILL.md.tmpl template is rendered into one SKILL-<host>.md per host so the
running model only sees its own host's tools.

Adding a fifth host is the litmus test for whether this lives in the right
place: append one row to HOSTS, run `python tools/gen_skill_md.py`, and the
new SKILL-<host>.md falls out. install.sh's detection block also needs the
host's skill_dir suffix added — see install.sh for that side.
"""

from __future__ import annotations


HOSTS: dict[str, dict[str, str | bool]] = {
    "claude": {
        "id": "claude",
        # Display name as it appears in prose (e.g. "This skill runs on Claude Code")
        "display_name": "Claude Code",
        # Telemetry literal value passed via --host
        "host_id": "claude_code",
        # Skill directory suffix (under $HOME / $PWD / $REPO_ROOT)
        "skill_dir": ".claude/skills",
        # The interactive question tool's name (no backticks)
        "question_tool": "AskUserQuestion",
        # Sub-agent dispatch primitive (the tool name)
        "task_tool": "Task",
        # Value to pass as subagent_type. Empty string means N/A for this host
        # (Gemini and Codex don't use a subagent_type argument).
        "subagent_type": "general-purpose",
        # Web tools — empty string means "no native tool, use a workaround"
        "webfetch_tool": "WebFetch",
        "websearch_tool": "WebSearch",
        # Model identifier examples. `model_default` is the recommended one;
        # `model_alts` is a comma-separated list of alternates shown in prose.
        "model_default": "claude-opus-4-7",
        "model_alts": "claude-sonnet-4-6, claude-haiku-4-5",
        # True if the host has a Write tool (used for shell-free file writes).
        # Currently Claude Code and OpenCode; Codex and Gemini route writes
        # through Bash/shell. The cache-summary section uses this distinction.
        "has_write_tool": True,
    },
    "codex": {
        "id": "codex",
        "display_name": "Codex CLI",
        "host_id": "codex_cli",
        "skill_dir": ".codex/skills",
        "question_tool": "request_user_input",
        # Codex doesn't have a single named "task" tool — dispatching a sub-agent
        # is done by instructing the model in prose. The token here is the closest
        # thing to a primitive name that prose can use; the dispatch bullet for
        # codex (in SKILL.md.tmpl) explains the prose-instruction shape.
        "task_tool": "(prose: spawn a sub-agent)",
        "subagent_type": "",  # N/A — codex uses prose instructions
        # Codex conflates fetch + search in a single curl-via-Bash workaround;
        # there's no native fetch tool name. Prose at the call-sites explains.
        "webfetch_tool": "curl-via-Bash",
        "websearch_tool": "web_search",
        "model_default": "gpt-5-codex",
        "model_alts": "gpt-5, gpt-5-mini",
        "has_write_tool": False,
    },
    "gemini": {
        "id": "gemini",
        "display_name": "Gemini CLI",
        "host_id": "gemini_cli",
        "skill_dir": ".gemini/skills",
        "question_tool": "ask_user",
        "task_tool": "@generalist",
        "subagent_type": "",  # N/A — gemini's @generalist is preconfigured
        "webfetch_tool": "web_fetch",
        "websearch_tool": "web_search",
        "model_default": "gemini-2.5-pro",
        "model_alts": "gemini-2.5-flash",
        "has_write_tool": False,
    },
    "opencode": {
        "id": "opencode",
        "display_name": "OpenCode",
        "host_id": "opencode_cli",
        "skill_dir": ".opencode/skills",
        "question_tool": "question",
        "task_tool": "task",
        "subagent_type": "general",
        "webfetch_tool": "webfetch",
        "websearch_tool": "websearch",
        # OpenCode uses a provider/model format. The default below picks the
        # Anthropic Opus model since most OpenCode users on the cohort end up
        # there; the model_alts string says "or whichever provider/model
        # you're configured against" inline in the per-host SKILL.md.
        "model_default": "anthropic/claude-opus-4-7",
        "model_alts": "or whichever provider/model you're configured against — OpenCode uses the `provider/model` format",
        "has_write_tool": True,
    },
}


# Listed in the order they appear in cross-host enumerations / docs / install
# detection. Generator iterates this to know which files to emit.
HOST_ORDER = ["claude", "codex", "gemini", "opencode"]


# The host whose generated SKILL.md content also becomes the source-tree
# SKILL.md (the "default" the repo ships if a user clones without running
# install.sh). Claude Code is by far the most common cohort host and the
# original target.
DEFAULT_HOST = "claude"
