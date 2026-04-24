"""Tier 3: live LLM regression test for issue #29.

Invokes `claude -p` against Haiku 4.5 with the real interview-coach prompt
(built via `resumasher orchestration build-prompt`) and asserts:
  1. Haiku does NOT call the Write tool during its response.
  2. Haiku does NOT plant any stray markdown file in the simulated student
     working directory.

Auto-skips when:
  - the `claude` CLI isn't installed (CI runners, fresh clones)
  - `RESUMASHER_SKIP_LIVE=1` is set (manual escape hatch)

The test uses your existing Claude Code subscription via `claude -p`, so
running it costs nothing beyond a Haiku response on a 10KB prompt.

Companion to:
  - tests/test_cleanup_stray_outputs.py — proves the BELT (cleanup scan)
    catches rogue files even if Haiku misbehaves.
  - tests/test_interview_coach_prompt.py — structural assertions on the
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

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "interview-coach"
REPO_ROOT = Path(__file__).resolve().parent.parent
RESUMASHER_EXEC = REPO_ROOT / "bin" / "resumasher-exec"

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


def _materialize_fixture_workspace(workdir: Path) -> dict[str, Path]:
    """Lay out the directory shape `build-prompt --kind interview-coach`
    expects, populated from the synthetic-persona fixtures."""
    run_dir = workdir / ".resumasher" / "run"
    out_dir = workdir / "applications" / "test-run"
    cache_dir = workdir / ".resumasher"
    run_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    # Original resume — sits in cwd like a real student's setup
    (workdir / "resume.md").write_text(
        (FIXTURES / "resume.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    # JD goes under .resumasher/run/
    (run_dir / "jd.txt").write_text(
        (FIXTURES / "jd.txt").read_text(encoding="utf-8"), encoding="utf-8"
    )
    # Folder-miner's prose summary
    (cache_dir / "cache.txt").write_text(
        (FIXTURES / "cache.txt").read_text(encoding="utf-8"), encoding="utf-8"
    )
    # Tailor sub-agent's output
    (out_dir / "tailored-resume.md").write_text(
        (FIXTURES / "tailored-resume.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    return {"run_dir": run_dir, "out_dir": out_dir, "cache_dir": cache_dir}


def _build_interview_coach_prompt(workdir: Path, out_dir: Path) -> str:
    """Build the prompt via bin/resumasher-exec, NOT via [sys.executable, "-m"
    scripts.orchestration]. The wrapper self-locates the venv python (POSIX
    .venv/bin/python or Windows .venv/Scripts/python.exe) — sys.executable
    can resolve to a base python without the project's deps when pytest is
    invoked through certain venv layouts (e.g., conda + venv where .venv is
    site-packages-only). Using the wrapper also exercises the same code
    path the SKILL.md orchestrator runs."""
    if not RESUMASHER_EXEC.exists():
        pytest.skip(f"bin/resumasher-exec not found at {RESUMASHER_EXEC}")
    result = subprocess.run(
        [
            str(RESUMASHER_EXEC),
            "orchestration",
            "build-prompt",
            "--kind",
            "interview-coach",
            "--cwd",
            str(workdir),
            "--out-dir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"build-prompt failed: stderr={result.stderr}"
    )
    return result.stdout


def _run_claude_p_against_haiku(prompt: str, workdir: Path) -> list[dict]:
    """Invoke `claude -p` with Haiku, Read+Write tools allowed (we WANT to
    detect Write calls), permissions bypassed for test-tmpdir isolation.
    Returns the parsed stream-json events."""
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
    paths = _materialize_fixture_workspace(tmp_path)
    prompt = _build_interview_coach_prompt(tmp_path, paths["out_dir"])

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
    """Even if Haiku DID call Write, no stray .md files should appear in
    the simulated student CWD root (post-bypass-permissions, the file
    would have been written for real). This is a directory-state check
    that's complementary to the tool-use check."""
    paths = _materialize_fixture_workspace(tmp_path)
    pre_existing = {p.name for p in tmp_path.iterdir() if p.is_file()}
    prompt = _build_interview_coach_prompt(tmp_path, paths["out_dir"])

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
