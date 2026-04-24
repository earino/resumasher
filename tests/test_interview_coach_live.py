"""Tier 3: live LLM regression test for issue #29.

Invokes `claude -p` against Haiku 4.5 with the real interview-coach prompt
and asserts:
  1. Haiku does NOT call the Write tool during its response.
  2. Haiku does NOT plant any stray markdown file in the simulated student
     working directory.

## Why this test is venv-independent

`scripts.prompts.build_prompt()` is pure-stdlib (only `dataclasses` and
`typing`). Calling it in-process avoids subprocessing into the project's
`.venv`, which would otherwise tie this test to the host platform that
created the venv. The only subprocess we need is `claude -p` itself.

This decoupling means the test runs on whatever Python pytest itself is
using — your host's system Python, conda env, sandbox venv, all fine. As
long as `pytest` and the `claude` CLI are reachable, the test runs.

## Auto-skip conditions

  - `claude` CLI not on PATH (CI runners, fresh clones — typical case)
  - `RESUMASHER_SKIP_LIVE=1` (manual escape hatch when you don't want to
    burn token budget on a noisy iteration loop)

Cost: one Haiku response per test. On any paid Claude plan that's
effectively zero per run. On pay-as-you-go: a couple of cents.

## Companion tests

  - `tests/test_cleanup_stray_outputs.py` — proves the BELT (the post-Phase-6
    cleanup scan) catches rogue files even if Haiku misbehaves.
  - `tests/test_interview_coach_prompt.py` — structural assertions on the
    prompt text that gate against future regressions of the framing.

This live test is the one that actually proves the SUSPENDERS — the prompt
surgery itself stops Haiku from calling Write in the first place.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.prompts import build_prompt

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "interview-coach"

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


def _build_prompt_in_process() -> str:
    """Build the interview-coach prompt by reading fixtures and calling
    build_prompt() directly. No subprocess, no venv dependency."""
    return build_prompt(
        kind="interview-coach",
        tailored_resume=(FIXTURES / "tailored-resume.md").read_text(encoding="utf-8"),
        folder_summary=(FIXTURES / "cache.txt").read_text(encoding="utf-8"),
        jd_text=(FIXTURES / "jd.txt").read_text(encoding="utf-8"),
    )


def _run_claude_p_against_haiku(prompt: str, workdir: Path) -> list[dict]:
    """Invoke `claude -p` with Haiku, Read+Write tools allowed (we WANT to
    detect Write calls), permissions bypassed for test-tmpdir isolation.
    Returns the parsed stream-json events (one dict per line)."""
    proc = subprocess.run(
        [
            "claude",
            "-p",
            "--model",
            "claude-haiku-4-5",
            "--output-format",
            "stream-json",
            "--verbose",  # required when output-format=stream-json
            "--permission-mode",
            "bypassPermissions",
            "--allowedTools",
            "Read,Write",
            prompt,
        ],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=300,  # 5 min ceiling
    )
    if proc.returncode != 0:
        pytest.skip(
            f"claude -p exited {proc.returncode}; not a fix regression. "
            f"stderr={proc.stderr[:500]}"
        )
    events = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _write_tool_calls(events: list[dict]) -> list[dict]:
    """Extract every Write tool_use block from a stream-json event list."""
    calls = []
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        msg = ev.get("message", {})
        for block in msg.get("content", []) or []:
            if block.get("type") == "tool_use" and block.get("name") == "Write":
                calls.append(block)
    return calls


def test_haiku_does_not_call_write_tool_with_new_prompt(tmp_path: Path):
    """The actual Tier 3 check: with the post-#29 prompt, Haiku must
    return the interview-prep document inline, not call Write."""
    prompt = _build_prompt_in_process()
    events = _run_claude_p_against_haiku(prompt, tmp_path)
    write_calls = _write_tool_calls(events)

    if write_calls:
        details = "\n".join(
            f"  - Write({json.dumps(c.get('input', {}), indent=None)[:200]}...)"
            for c in write_calls
        )
        pytest.fail(
            f"Haiku called the Write tool {len(write_calls)} time(s):\n{details}\n"
            f"This means the prompt surgery (issue #29) regressed. The cleanup "
            f"scan is still your safety net (see test_cleanup_stray_outputs.py), "
            f"but the prompt itself is no longer keeping Haiku from creating "
            f"stray files."
        )


def test_haiku_leaves_student_cwd_clean(tmp_path: Path):
    """Even if Haiku DID call Write, no stray .md files should appear in the
    simulated student CWD root after the run. Directory-state check that's
    complementary to the tool-use check."""
    pre_existing = {p.name for p in tmp_path.iterdir() if p.is_file()}
    prompt = _build_prompt_in_process()

    _run_claude_p_against_haiku(prompt, tmp_path)

    post = {p.name for p in tmp_path.iterdir() if p.is_file()}
    new_files = post - pre_existing
    new_md = {n for n in new_files if n.lower().endswith(".md")}

    if new_md:
        pytest.fail(
            f"Haiku planted stray markdown file(s) in the student CWD root: "
            f"{sorted(new_md)}. Issue #29 regressed at the prompt level. "
            f"The cleanup scan would still remove these in production, but "
            f"the prompt is no longer enough on its own."
        )
