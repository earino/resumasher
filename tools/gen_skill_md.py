#!/usr/bin/env python3
"""Generate per-host SKILL-<host>.md files from SKILL.md.tmpl.

Why this exists
---------------
resumasher runs on four AI CLI hosts (Claude Code, Codex CLI, Gemini CLI,
OpenCode). Each one has a different name for the question tool, the dispatch
primitive, the web tools, and the subagent_type value. The original SKILL.md
enumerated all four at every cross-host call-site (~10-15 places), which:

1. Costs ~15-20% of the token budget on weak local models that can't ignore
   the irrelevant bullets.
2. Tempts the model to pick the FIRST listed value even when it's the wrong
   host's value — observed in run ses_235c (qwen3.6-35b on OpenCode), which
   defaulted to Claude Code's `general-purpose` subagent_type and got
   rejected before self-correcting to OpenCode's `general`.

Templating fixes both: each host gets a SKILL-<host>.md containing only its
own tool names. install.sh detects the host on install and copies the right
file over SKILL.md.

Template syntax (intentionally minimal)
---------------------------------------
- ``{{var}}`` — variable substitution from HOSTS[host][var]
- ``{{#if claude}}...{{/if}}`` — block included only when host == claude
- ``{{#if claude,opencode}}...{{/if}}`` — multi-host (any of the listed)
- ``{{#unless codex}}...{{/unless}}`` — block included when host != codex
- ``{{#unless codex,gemini}}...{{/unless}}`` — host is none of the listed

No nesting, no loops, no Jinja2 dependency. The full grammar fits in 50
lines of Python. If you find yourself reaching for nested conditionals,
that's a sign the cross-host content has diverged enough that you should
flatten it instead.

Usage
-----
- ``python tools/gen_skill_md.py`` — regenerate all per-host files in place.
- ``python tools/gen_skill_md.py --check`` — assert generated files are
  byte-equivalent to the committed copies. Used by CI / the drift test.
- ``python tools/gen_skill_md.py --host claude`` — emit only one host
  (useful when iterating on the template).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Allow running this script as `python tools/gen_skill_md.py` even though
# tools/ has no __init__.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from host_config import HOSTS, HOST_ORDER, DEFAULT_HOST  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = REPO_ROOT / "SKILL.md.tmpl"


# ── Template engine ─────────────────────────────────────────────────────────

# Conditional block: {{#if host}}...{{/if}} or {{#if host1,host2}}...{{/if}}
# Negative form: {{#unless host}}...{{/unless}}
# DOTALL so the body can span lines.
_BLOCK_RE = re.compile(
    r"\{\{#(?P<kind>if|unless)\s+(?P<hosts>[a-z, ]+)\}\}"
    r"(?P<body>.*?)"
    r"\{\{/(?P=kind)\}\}",
    re.DOTALL,
)

# Variable substitution: {{varname}}. Only lowercase letters and underscores —
# don't accidentally chomp `{{HOST}}` or `{{...}}` shell-doc placeholders.
_VAR_RE = re.compile(r"\{\{(?P<name>[a-z_]+)\}\}")


def _split_hosts(raw: str) -> set[str]:
    """Parse `claude,opencode` into {'claude', 'opencode'}, validate names."""
    hosts = {h.strip() for h in raw.split(",") if h.strip()}
    unknown = hosts - set(HOSTS.keys())
    if unknown:
        raise ValueError(
            f"Unknown host(s) in template conditional: {sorted(unknown)}. "
            f"Valid: {sorted(HOSTS.keys())}"
        )
    return hosts


def render(template: str, host: str) -> str:
    """Render the template for one host. Pure function — no I/O.

    Conditionals are processed first (so a kept block's variables get
    substituted; a dropped block's variables are simply discarded). Then
    variable substitution runs on what remains.
    """
    if host not in HOSTS:
        raise ValueError(f"Unknown host: {host!r}. Valid: {list(HOSTS.keys())}")

    def _block_repl(match: re.Match[str]) -> str:
        kind = match.group("kind")
        hosts = _split_hosts(match.group("hosts"))
        body = match.group("body")
        keep = host in hosts if kind == "if" else host not in hosts
        return body if keep else ""

    # Iterate until no more conditional blocks (handles non-overlapping
    # but adjacent blocks; we don't allow nesting). One pass is enough
    # since _BLOCK_RE matches the SHORTEST body via .*?, and we don't
    # nest, so re.sub fully expands in one go.
    out = _BLOCK_RE.sub(_block_repl, template)

    # Variable substitution.
    def _var_repl(match: re.Match[str]) -> str:
        name = match.group("name")
        cfg = HOSTS[host]
        if name not in cfg:
            raise KeyError(
                f"Template references unknown variable {{{{{name}}}}} for host "
                f"{host!r}. Add it to tools/host_config.py."
            )
        return str(cfg[name])

    out = _VAR_RE.sub(_var_repl, out)

    # Collapse runs of 3+ newlines down to 2. When a {{#if}}...{{/if}} block
    # is the only thing on its line and the block drops out, the surrounding
    # newlines stack up and produce visible double-blank-line gaps in
    # markdown. This is a mild normalization, not a structural change.
    out = re.sub(r"\n{3,}", "\n\n", out)

    # Sanity check: no template syntax should survive a successful render.
    if "{{" in out or "}}" in out:
        # Find the first stray marker for a useful error.
        idx = out.find("{{")
        if idx == -1:
            idx = out.find("}}")
        ctx_start = max(0, idx - 40)
        ctx_end = min(len(out), idx + 80)
        raise RuntimeError(
            f"Unrendered template marker in output for host={host!r}: "
            f"...{out[ctx_start:ctx_end]!r}..."
        )

    return out


# ── File output ─────────────────────────────────────────────────────────────


def output_path_for(host: str) -> Path:
    """Where the generated SKILL-<host>.md goes in the source tree."""
    return REPO_ROOT / f"SKILL-{host}.md"


def default_skill_md_path() -> Path:
    """The 'default' SKILL.md the repo ships with — same content as the
    DEFAULT_HOST variant. Kept identical so a clone-without-install user
    still gets a working SKILL.md for their (most likely) host."""
    return REPO_ROOT / "SKILL.md"


_AUTOGEN_HEADER = (
    "<!-- AUTO-GENERATED by tools/gen_skill_md.py from SKILL.md.tmpl -->\n"
    "<!-- Regenerate: python tools/gen_skill_md.py -->\n"
    "<!-- Host: {host} -->\n"
)


def _wrap_with_header(rendered: str, host: str) -> str:
    return _AUTOGEN_HEADER.format(host=host) + rendered


def generate_all(check: bool = False, only_host: str | None = None) -> int:
    """Render every host (or just one with --host). When check=True, asserts
    each on-disk file matches the freshly rendered output and exits non-zero
    on drift. Returns a process exit code."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    hosts = [only_host] if only_host else HOST_ORDER
    drift_count = 0

    for host in hosts:
        rendered = _wrap_with_header(render(template, host), host)
        targets = [output_path_for(host)]
        if host == DEFAULT_HOST:
            targets.append(default_skill_md_path())

        for target in targets:
            if check:
                if not target.exists():
                    print(
                        f"DRIFT: {target.relative_to(REPO_ROOT)} is missing — "
                        f"run `python tools/gen_skill_md.py` to regenerate.",
                        file=sys.stderr,
                    )
                    drift_count += 1
                    continue
                on_disk = target.read_text(encoding="utf-8")
                if on_disk != rendered:
                    print(
                        f"DRIFT: {target.relative_to(REPO_ROOT)} differs from "
                        f"template output. Run `python tools/gen_skill_md.py` "
                        f"to regenerate.",
                        file=sys.stderr,
                    )
                    drift_count += 1
            else:
                target.write_text(rendered, encoding="utf-8")
                print(f"wrote {target.relative_to(REPO_ROOT)} ({len(rendered):,} bytes)")

    return 1 if drift_count else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Assert generated files match the template output; exit 1 on drift.",
    )
    parser.add_argument(
        "--host",
        choices=HOST_ORDER,
        help="Generate only the named host (default: all).",
    )
    args = parser.parse_args()
    return generate_all(check=args.check, only_host=args.host)


if __name__ == "__main__":
    sys.exit(main())
