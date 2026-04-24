"""
Regression tests for UTF-8 stdout/stderr reconfiguration at CLI entry points.

Windows Python defaults stdout/stderr to the system ANSI code page (typically
CP1252) when not attached to a TTY — which is exactly the shape every CI run
and every `resumasher-exec` invocation takes. Prompt templates, rendered
markdown, and user-facing output routinely include `→`, `…`, curly quotes,
and non-ASCII names that CP1252 cannot encode. Without the reconfigure calls
at each `if __name__ == "__main__":` block, those writes raise
UnicodeEncodeError and the pipeline dies mid-phase.

Windows CI (#34) surfaced this class of bug in `test_prompts.py`, but the
problem is production-path — any Windows student running orchestration,
render_pdf, or github_mine hits it the moment output includes a `→`.

These tests simulate the Windows behavior on any OS by invoking the CLIs
with `PYTHONIOENCODING=cp1252`, which makes CPython's TextIOWrapper use the
same CP1252 encoding Windows picks by default. Without the reconfigure
fix, the CLIs raise UnicodeEncodeError; with the fix, they emit the UTF-8
bytes into the stdout pipe regardless of what encoding the environment
asked for.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _setup_minimal_skill_tree(root: Path) -> None:
    """Populate the minimum file tree that `build-prompt --kind tailor` needs.

    Mirrors the `skill_tree` fixture from test_prompts.py (run/resume.txt,
    run/context.txt, run/jd.txt, .resumasher/config.json). Inlined here
    because cross-file pytest fixtures require conftest.py plumbing, and
    this test module has no other consumers of the tree.
    """
    run = root / ".resumasher" / "run"
    run.mkdir(parents=True)
    (run / "resume.txt").write_text("RESUME_FILE_CONTENT", encoding="utf-8")
    (run / "context.txt").write_text("CONTEXT_FILE_CONTENT", encoding="utf-8")
    (run / "jd.txt").write_text("JD_FILE_CONTENT", encoding="utf-8")
    (root / ".resumasher" / "cache.txt").write_text(
        "CACHE_FILE_CONTENT", encoding="utf-8"
    )
    (root / ".resumasher" / "config.json").write_text(
        '{"name": "Test Student", "email": "test@example.com"}',
        encoding="utf-8",
    )


def _run_under_cp1252(
    args: list[str],
    cwd: Path | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess:
    """Invoke a Python CLI with PYTHONIOENCODING=cp1252 to simulate Windows."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    # Byte-level capture — we're testing the encoding layer itself. If the CLI
    # reconfigures stdout to UTF-8, we'll see UTF-8 bytes here; if it doesn't,
    # we'll see a UnicodeEncodeError traceback on stderr and no stdout.
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(cwd) if cwd else str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=False,
        input=stdin.encode("utf-8") if stdin is not None else None,
        timeout=30,
    )


def test_orchestration_build_prompt_survives_cp1252_stdout(tmp_path):
    """orchestration build-prompt must not crash when stdout is CP1252.

    Pre-fix: sys.stdout.write(prompt) raises UnicodeEncodeError on `→` in
    the tailor prompt template.
    Post-fix: sys.stdout.reconfigure(encoding="utf-8") in the __main__ block
    forces UTF-8 regardless of what the environment requested; writes succeed.
    """
    # Minimal config so build-prompt has what it needs. The prompt template
    # itself emits `→` (section separators, arrow glyphs), which is the
    # character that triggered the Windows CI failure.
    _setup_minimal_skill_tree(tmp_path)

    result = _run_under_cp1252(
        [
            "-m",
            "scripts.orchestration",
            "build-prompt",
            "--kind",
            "tailor",
            "--cwd",
            str(tmp_path),
        ],
    )

    # A crash would leave rc != 0, empty stdout, and a UnicodeEncodeError
    # traceback on stderr. Assert the inverse: clean exit, non-empty stdout,
    # and specifically no encoding traceback.
    stderr_text = result.stderr.decode("utf-8", errors="replace")
    assert result.returncode == 0, (
        f"build-prompt crashed under CP1252 stdout:\n"
        f"returncode={result.returncode}\nstderr:\n{stderr_text}"
    )
    assert result.stdout, "build-prompt produced no output under CP1252 stdout"
    assert "UnicodeEncodeError" not in stderr_text, (
        f"build-prompt raised UnicodeEncodeError under CP1252 stdout:\n"
        f"{stderr_text}"
    )


def test_orchestration_build_prompt_emits_utf8_bytes_for_arrow_glyph(tmp_path):
    """Confirm the bytes on the wire are UTF-8, not a lossy transcode.

    It's not enough for the CLI to "not crash" — a silent errors="replace"
    would satisfy the crash-free assertion while producing `?` instead of
    `→`. Decode the stdout bytes as UTF-8 and verify the arrow glyph round-
    trips intact.
    """
    _setup_minimal_skill_tree(tmp_path)

    result = _run_under_cp1252(
        [
            "-m",
            "scripts.orchestration",
            "build-prompt",
            "--kind",
            "tailor",
            "--cwd",
            str(tmp_path),
        ],
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    # stdout should be decodable as UTF-8. The tailor prompt template contains
    # at least one `→`; if the fix works, the byte sequence \xe2\x86\x92 is
    # present verbatim.
    stdout_utf8 = result.stdout.decode("utf-8")
    assert "→" in stdout_utf8, (
        "Arrow glyph missing from stdout — output may have been transcoded "
        "lossily rather than emitted as UTF-8 bytes."
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "The sanity simulation uses a Linux-style minimal PATH "
        "(/usr/bin:/bin) and relies on PYTHONIOENCODING overriding a "
        "default that, on Windows, is already CP1252 — the simulation is "
        "moot there. Windows already exercises the real CP1252 behavior "
        "on every stdout write in CI, so regressions of the reconfigure "
        "fix surface in the two tests above. Skip here to avoid a false "
        "red from env shape alone."
    ),
)
def test_sanity_cp1252_simulation_reproduces_crash_without_fix():
    """Sanity check: PYTHONIOENCODING=cp1252 actually reproduces the crash.

    If this test ever stops reproducing the crash for an unrelated CPython
    change (e.g. CPython ships a default stdout encoding override that
    ignores PYTHONIOENCODING), the two tests above become silent no-ops —
    they'd pass because the environment never crashed in the first place,
    not because our fix held. Pin the simulation's validity here.
    """
    r = subprocess.run(
        [sys.executable, "-c", "import sys; sys.stdout.write('arrow: →')"],
        env={"PYTHONIOENCODING": "cp1252", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0, (
        "PYTHONIOENCODING=cp1252 no longer reproduces the Windows stdout "
        "encoding crash on this platform — the other tests in this module "
        "have become no-ops. Update the simulation strategy."
    )
    assert "UnicodeEncodeError" in r.stderr, (
        "Expected UnicodeEncodeError but got a different failure:\n"
        f"{r.stderr}"
    )
