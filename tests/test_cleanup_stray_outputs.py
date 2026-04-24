"""Regression tests for the post-Phase-6 cleanup scan (issue #29).

The scan removes rogue interview-prep-shaped markdown files that a
misbehaving sub-agent (e.g. Haiku 4.5 ignoring "do not write to disk")
plants in $STUDENT_CWD. Tests cover:

- DELETE path: canonical interview-prep.md exists → rogue file is pollution
- MOVE path: canonical missing/empty → rogue file's content is recovered
- mtime gate: pre-existing student files are never touched
- Name pattern: only files matching interview/prep/bundle are candidates
- Protected names: documented filenames (resume.md, jd.md, etc.) are immune
- Top-level only: subdirectories are never scanned
- Empty / missing inputs: non-crashing behavior
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.orchestration import (
    INTERVIEW_PREP_NAME_PATTERNS,
    PROTECTED_NAMES_LOWER,
    CleanupAction,
    cleanup_stray_outputs,
)


def _set_mtime(path: Path, ts: float) -> None:
    os.utime(path, (ts, ts))


# ---------------------------------------------------------------------------
# DELETE path: canonical exists with content → rogue is pollution
# ---------------------------------------------------------------------------


def test_deletes_rogue_when_canonical_exists_with_content(tmp_path: Path):
    out_dir = tmp_path / "applications" / "test-run"
    out_dir.mkdir(parents=True)
    (out_dir / "interview-prep.md").write_text("# Real interview prep\n\n...", encoding="utf-8")

    rogue = tmp_path / "Ana_Muller_Interview_Prep_Bundle.md"
    rogue.write_text("# Rogue content from misbehaving sub-agent", encoding="utf-8")

    actions = cleanup_stray_outputs(
        cwd=tmp_path, out_dir=out_dir, since_timestamp=time.time() - 60
    )

    assert not rogue.exists(), "rogue file must be removed"
    assert (out_dir / "interview-prep.md").read_text(encoding="utf-8").startswith(
        "# Real"
    ), "canonical file must be untouched"
    assert len(actions) == 1
    assert actions[0].action == "deleted"
    assert actions[0].path == rogue


# ---------------------------------------------------------------------------
# MOVE path: canonical missing → rogue's content is salvaged
# ---------------------------------------------------------------------------


def test_moves_rogue_when_canonical_missing(tmp_path: Path):
    out_dir = tmp_path / "applications" / "test-run"
    out_dir.mkdir(parents=True)
    # canonical interview-prep.md does not exist

    rogue = tmp_path / "Ana_Muller_Interview_Prep_Bundle.md"
    rogue_content = "# Rogue content but actually correct\n\nReal interview prep here."
    rogue.write_text(rogue_content, encoding="utf-8")

    actions = cleanup_stray_outputs(
        cwd=tmp_path, out_dir=out_dir, since_timestamp=time.time() - 60
    )

    assert not rogue.exists(), "rogue file must be removed from cwd"
    canonical = out_dir / "interview-prep.md"
    assert canonical.exists(), "rogue's content must be recovered to canonical path"
    assert canonical.read_text(encoding="utf-8") == rogue_content
    assert len(actions) == 1
    assert actions[0].action == "moved"
    assert actions[0].destination == canonical


def test_moves_rogue_when_canonical_exists_but_empty(tmp_path: Path):
    """An empty canonical file is treated as missing (the orchestrator's
    Write didn't deliver content, so we'd rather have the rogue's content
    than an empty stub)."""
    out_dir = tmp_path / "applications" / "test-run"
    out_dir.mkdir(parents=True)
    canonical = out_dir / "interview-prep.md"
    canonical.write_text("", encoding="utf-8")  # zero bytes

    rogue = tmp_path / "Interview_Prep.md"
    rogue.write_text("# Real content", encoding="utf-8")

    actions = cleanup_stray_outputs(
        cwd=tmp_path, out_dir=out_dir, since_timestamp=time.time() - 60
    )

    assert not rogue.exists()
    assert canonical.exists()
    assert canonical.read_text(encoding="utf-8") == "# Real content"
    assert actions[0].action == "moved"


# ---------------------------------------------------------------------------
# mtime gate: pre-existing files (older than dispatch timestamp) are immune
# ---------------------------------------------------------------------------


def test_does_not_touch_files_older_than_since_timestamp(tmp_path: Path):
    """Student's own pre-existing notes named 'interview-prep.md' must not
    be deleted just because the cleanup scan ran."""
    out_dir = tmp_path / "applications" / "test-run"
    out_dir.mkdir(parents=True)
    (out_dir / "interview-prep.md").write_text("# Real", encoding="utf-8")

    # A file that matches the name heuristic but pre-dates the run.
    student_notes = tmp_path / "my_interview_thoughts.md"
    student_notes.write_text("# My pre-existing notes", encoding="utf-8")
    _set_mtime(student_notes, time.time() - 86400)  # 1 day old

    dispatch_ts = time.time() - 60  # run started 1 min ago

    actions = cleanup_stray_outputs(
        cwd=tmp_path, out_dir=out_dir, since_timestamp=dispatch_ts
    )

    assert student_notes.exists(), "pre-existing student file must survive"
    assert student_notes.read_text(encoding="utf-8") == "# My pre-existing notes"
    assert actions == []


# ---------------------------------------------------------------------------
# Name pattern: files that don't match interview/prep/bundle are immune
# ---------------------------------------------------------------------------


def test_does_not_touch_unrelated_markdown_files(tmp_path: Path):
    out_dir = tmp_path / "applications" / "test-run"
    out_dir.mkdir(parents=True)
    (out_dir / "interview-prep.md").write_text("# Real", encoding="utf-8")

    # All freshly created (newer than dispatch_ts) but no interview-pattern match.
    for name in ("notes.md", "todo.md", "README.md", "shopping-list.md"):
        (tmp_path / name).write_text("# innocent student file", encoding="utf-8")

    actions = cleanup_stray_outputs(
        cwd=tmp_path, out_dir=out_dir, since_timestamp=time.time() - 60
    )

    for name in ("notes.md", "todo.md", "README.md", "shopping-list.md"):
        assert (tmp_path / name).exists(), f"{name} must be left alone"
    assert actions == []


@pytest.mark.parametrize(
    "rogue_name",
    [
        "Interview_Prep.md",
        "interview_prep.md",
        "INTERVIEW-PREP.md",
        "Ana_Muller_Interview_Prep_Bundle.md",
        "PrepDoc.md",  # matches "prep"
        "candidate-bundle.md",  # matches "bundle"
        "MyInterviewQuestions.md",  # matches "interview"
    ],
)
def test_name_heuristic_matches_observed_shapes(tmp_path: Path, rogue_name: str):
    out_dir = tmp_path / "applications" / "test-run"
    out_dir.mkdir(parents=True)
    (out_dir / "interview-prep.md").write_text("# Real", encoding="utf-8")

    rogue = tmp_path / rogue_name
    rogue.write_text("# rogue", encoding="utf-8")

    actions = cleanup_stray_outputs(
        cwd=tmp_path, out_dir=out_dir, since_timestamp=time.time() - 60
    )

    assert not rogue.exists(), f"name {rogue_name!r} should match the cleanup heuristic"
    assert len(actions) == 1
    assert actions[0].action == "deleted"


# ---------------------------------------------------------------------------
# Protected names: documented filenames are immune
# ---------------------------------------------------------------------------


def test_protected_names_are_never_touched(tmp_path: Path):
    """Even a fresh file named e.g. 'cover-letter.md' is documented and
    must not be deleted by an interview-coach cleanup pass."""
    out_dir = tmp_path / "applications" / "test-run"
    out_dir.mkdir(parents=True)
    (out_dir / "interview-prep.md").write_text("# Real", encoding="utf-8")

    for protected in PROTECTED_NAMES_LOWER:
        path = tmp_path / protected
        path.write_text(f"# protected: {protected}", encoding="utf-8")

    actions = cleanup_stray_outputs(
        cwd=tmp_path, out_dir=out_dir, since_timestamp=time.time() - 60
    )

    for protected in PROTECTED_NAMES_LOWER:
        path = tmp_path / protected
        assert path.exists(), f"protected file {protected} must survive"
    assert actions == []


# ---------------------------------------------------------------------------
# Top-level only: subdirectories are never scanned
# ---------------------------------------------------------------------------


def test_subdirectory_rogue_files_are_not_touched(tmp_path: Path):
    """If a student keeps interview-prep notes in a subdir, that's their
    business. The scan is top-level-only by design."""
    out_dir = tmp_path / "applications" / "test-run"
    out_dir.mkdir(parents=True)
    (out_dir / "interview-prep.md").write_text("# Real", encoding="utf-8")

    nested_dir = tmp_path / "my-notes"
    nested_dir.mkdir()
    nested_rogue = nested_dir / "Interview_Prep_Bundle.md"
    nested_rogue.write_text("# nested student notes", encoding="utf-8")

    actions = cleanup_stray_outputs(
        cwd=tmp_path, out_dir=out_dir, since_timestamp=time.time() - 60
    )

    assert nested_rogue.exists(), "subdirectory file must not be scanned"
    assert actions == []


# ---------------------------------------------------------------------------
# Defensive: missing inputs don't crash
# ---------------------------------------------------------------------------


def test_returns_empty_when_cwd_missing(tmp_path: Path):
    actions = cleanup_stray_outputs(
        cwd=tmp_path / "does-not-exist",
        out_dir=tmp_path / "out",
        since_timestamp=0,
    )
    assert actions == []


def test_returns_empty_when_cwd_is_a_file(tmp_path: Path):
    f = tmp_path / "regular-file"
    f.write_text("not a dir")
    actions = cleanup_stray_outputs(
        cwd=f, out_dir=tmp_path / "out", since_timestamp=0
    )
    assert actions == []


# ---------------------------------------------------------------------------
# CLI integration: subcommand returns valid JSON and exit 0
# ---------------------------------------------------------------------------


def test_cli_emits_valid_json_summary(tmp_path: Path):
    out_dir = tmp_path / "applications" / "test-run"
    out_dir.mkdir(parents=True)
    (out_dir / "interview-prep.md").write_text("# Real", encoding="utf-8")
    rogue = tmp_path / "Ana_Interview_Bundle.md"
    rogue.write_text("# rogue", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.orchestration",
            "cleanup-stray-outputs",
            "--cwd",
            str(tmp_path),
            "--out-dir",
            str(out_dir),
            "--since-timestamp",
            str(time.time() - 60),
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["deleted"] == 1
    assert summary["moved"] == 0
    assert summary["skipped"] == 0
    assert len(summary["actions"]) == 1
    assert summary["actions"][0]["action"] == "deleted"
    assert not rogue.exists()


def test_cli_no_crash_on_empty_cwd(tmp_path: Path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.orchestration",
            "cleanup-stray-outputs",
            "--cwd",
            str(tmp_path),
            "--out-dir",
            str(out_dir),
            "--since-timestamp",
            "0",
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["actions"] == []
