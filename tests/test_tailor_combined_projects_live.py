"""Tier 3: live LLM regression test for issue #46.

Invokes `claude -p` against Opus 4.7 with the real tailor prompt and a
synthetic candidate whose folder summary contains TWO thematically-related
GitHub repos. Asserts the tailor LLM emits separate `### {Name} ({URL})`
H3 blocks for each repo, NOT a single combined heading like
`### foo + bar (github.com/me/foo, github.com/me/bar)`.

The bug surfaced in @earino's run on PR #43, where two related repos
(`prompt-harness` and `nonprofit.ai`) ended up under one combined H3.
The renderer's title-collapse logic only handles `Name (single URL)`, so
combined headings rendered as inline-URL fallback — visually
inconsistent with the other project entries and ATS-confusing.

The fix is a prompt-template change in `scripts/prompts.py` (the tailor
template's Projects section now contains an explicit "One project per
H3 heading" rule with concrete `+`/`/`/`&` combiner examples to avoid).
This live test verifies the prompt change actually changes Opus's
behavior on the exact shape of input that produced the bug.

## Auto-skip conditions

  - `claude` CLI not on PATH (CI runners, fresh clones — typical case)
  - `RESUMASHER_SKIP_LIVE=1` (manual escape hatch)

Cost: one Opus tailor pass per test. ~30-60s wall time, ~30K tokens —
zero on Claude Max, a few cents on pay-as-you-go.

## Companion tests

  - `tests/test_prompts.py::test_tailor_prompt_*` — structural assertions
    on the prompt text. Those gate against the rule being silently
    dropped from the template by a future edit. THIS test gates against
    the prompt change actually working at the LLM-behavior level.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.prompts import build_prompt

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tailor-combined-projects"

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="requires `claude` CLI on PATH (install Claude Code to run live-LLM tests)",
    ),
    pytest.mark.skipif(
        os.environ.get("RESUMASHER_SKIP_LIVE") == "1",
        reason="explicitly disabled via RESUMASHER_SKIP_LIVE=1",
    ),
]

# Both repos that the synthetic candidate has. The cache.txt explicitly
# describes them as related-but-distinct so the prompt has the maximum
# tailor-tempting "they're connected" context — if the tailor still
# emits two separate H3 blocks under THIS shape, it'll do the right
# thing on real candidates' less-tempting shapes too.
EXPECTED_REPO_NAMES = ("prompt-harness", "nonprofit-prompts")


def _build_tailor_prompt() -> str:
    """Build the tailor prompt by reading fixtures and calling
    build_prompt() directly. No subprocess, no venv dependency."""
    # The tailor needs a contact_info string. Use the standard formatted
    # form so the prompt is realistic.
    contact = (
        "sam.jones@example.com | +1 555 0123 | linkedin.com/in/samjones | Boston, MA"
    )
    return build_prompt(
        kind="tailor",
        contact_info=contact,
        resume_text=(FIXTURES / "resume.md").read_text(encoding="utf-8"),
        folder_summary=(FIXTURES / "cache.txt").read_text(encoding="utf-8"),
        jd_text=(FIXTURES / "jd.txt").read_text(encoding="utf-8"),
    )


def _run_claude_p_against_opus(prompt: str, workdir: Path) -> str:
    """Invoke `claude -p` with Opus (the model resumasher uses for tailoring)
    and return the assistant's text output (the tailored resume markdown).

    No tool use needed — tailor returns markdown inline. We use the simpler
    text output format and just collect stdout.
    """
    proc = subprocess.run(
        [
            "claude",
            "-p",
            "--model",
            "claude-opus-4-7",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "bypassPermissions",
        ],
        input=prompt,
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=600,  # 10 min ceiling — Opus tailoring is the slowest sub-agent
    )
    if proc.returncode != 0:
        pytest.fail(
            f"claude -p exited {proc.returncode}.\n"
            f"stderr:\n{proc.stderr}\n"
            f"stdout (first 500 chars):\n{proc.stdout[:500]}"
        )
    # Collect every text block from every assistant event into one string.
    chunks: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "assistant":
            continue
        msg = ev.get("message", {})
        for block in msg.get("content", []) or []:
            if block.get("type") == "text":
                chunks.append(block.get("text", ""))
    return "".join(chunks)


def _h3_headings(markdown: str) -> list[str]:
    """Return every line that starts with `### ` (the H3 headings) in
    the order they appear. Trailing whitespace stripped."""
    out = []
    for line in markdown.splitlines():
        if line.startswith("### "):
            out.append(line[4:].rstrip())
    return out


def _projects_section_h3s(markdown: str) -> list[str]:
    """Return only the H3 headings that fall under the `## Projects`
    section — that's where the combined-projects bug manifests."""
    in_projects = False
    out: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            in_projects = line.strip().lower() == "## projects"
            continue
        if in_projects and line.startswith("### "):
            out.append(line[4:].rstrip())
    return out


def test_tailor_emits_separate_h3_blocks_for_two_related_repos(tmp_path: Path):
    """The load-bearing live test. Build the tailor prompt with the
    combined-projects fixture, run Opus, parse the output, assert the
    Projects section has SEPARATE ### headings for `prompt-harness`
    and `nonprofit-prompts`, NOT a single combined heading."""
    prompt = _build_tailor_prompt()
    output = _run_claude_p_against_opus(prompt, tmp_path)

    project_h3s = _projects_section_h3s(output)

    # The tailor MAY decide to drop one of the projects entirely if it
    # judges the JD is better served by other content. That's a
    # different concern (judgment call) from the bug we're testing
    # (combining unrelated projects under one heading). What we're
    # testing: IF both repos appear, they appear as SEPARATE H3 blocks.

    repos_combined_in_one_h3 = [
        h for h in project_h3s
        if all(name in h for name in EXPECTED_REPO_NAMES)
    ]
    if repos_combined_in_one_h3:
        pytest.fail(
            f"Tailor combined two repos under one H3 heading "
            f"(issue #46 regressed at the prompt level):\n"
            f"  {repos_combined_in_one_h3}\n\n"
            f"All Projects-section H3 headings:\n"
            + "\n".join(f"  {h}" for h in project_h3s)
            + "\n\nFull tailored output:\n"
            + output
        )


def test_tailor_h3_headings_have_one_url_each(tmp_path: Path):
    """Independent of which repos appear, every H3 heading in the
    Projects section must have AT MOST ONE `github.com/...` URL in
    its parens. Multiple URLs in one heading is the symptom we forbid
    — the comma-separated `(URL1, URL2)` shape that the renderer's
    title-collapse logic can't handle."""
    prompt = _build_tailor_prompt()
    output = _run_claude_p_against_opus(prompt, tmp_path)

    project_h3s = _projects_section_h3s(output)

    # Count `github.com/` occurrences in each H3 heading. Each H3
    # should have 0 (no project URL — folder-path entry, rare in
    # this fixture but allowed) or 1 (the canonical shape). 2+ means
    # combined-repos.
    bad_h3s: list[str] = []
    for h in project_h3s:
        url_count = len(re.findall(r"github\.com/", h))
        if url_count >= 2:
            bad_h3s.append(f"{h}  ({url_count} URLs)")

    if bad_h3s:
        pytest.fail(
            f"H3 heading(s) contain multiple github.com URLs (issue #46 "
            f"regression — combined-repos shape):\n"
            + "\n".join(f"  {h}" for h in bad_h3s)
            + "\n\nAll Projects-section H3 headings:\n"
            + "\n".join(f"  {h}" for h in project_h3s)
        )
