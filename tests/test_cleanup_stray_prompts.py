"""Regression tests for the post-Phase-8 prompt-staging cleanup scan (issue #45).

The scan deletes prompt-staging files (`<kind>-prompt.{txt,md}` /
`<kind>_prompt.{txt,md}` for any registered prompt kind) that a sub-agent
improvised into `/tmp/` instead of `$RUN_DIR/prompts/`. These files
contain student PII (resume + JD + project content) and on macOS sit
world-readable in /tmp until reboot.

Tests cover:
- Pattern match: each registered prompt kind in `<kind>-prompt.txt` and
  `<kind>_prompt.txt` form is matched and deleted
- mtime gate: pre-existing files (older than --since-timestamp) are
  never touched, regardless of name
- Suffix gate: only `.txt` and `.md` files are candidates; an
  improperly-named `folder-miner-prompt.json` is left alone (defensive)
- Top-level only: a `tmp/subdir/folder-miner-prompt.txt` is never
  touched; only the immediate top of the scan dir is scanned
- Generic /tmp files (`bash_history`, `screenshot.png`, `random.txt`)
  are never touched even if mtime is newer
- Empty / missing scan dir: returns empty, never raises
- Action: every match is `deleted` (no MOVE path; these are
  unrecoverable transient intermediates)
- CLI: subcommand emits the expected JSON summary, exits 0
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
    CleanupAction,
    cleanup_stray_prompts,
    _registered_prompt_kinds,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_old_file(path: Path, content: str = "old content") -> Path:
    """Write a file and stamp its mtime back ~10 seconds so the mtime
    gate treats it as pre-existing."""
    path.write_text(content, encoding="utf-8")
    past = time.time() - 10.0
    os.utime(path, (past, past))
    return path


def _make_new_file(path: Path, content: str = "new content") -> Path:
    """Write a file with a current mtime so the mtime gate treats it as
    newer than `since_timestamp`."""
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Pattern-match: every registered kind, both separators, both suffixes
# ---------------------------------------------------------------------------


def test_each_registered_kind_with_dash_separator_gets_deleted(tmp_path: Path):
    """For every kind in _PROMPT_KINDS, a file named `<kind>-prompt.txt`
    in the scan dir gets deleted by the scan."""
    since = time.time() - 5.0
    expected_paths = []
    for kind in _registered_prompt_kinds():
        p = _make_new_file(tmp_path / f"{kind}-prompt.txt", f"PII for {kind}")
        expected_paths.append(p)

    actions = cleanup_stray_prompts(since_timestamp=since, scan_dir=tmp_path)

    assert all(a.action == "deleted" for a in actions), [
        (str(a.path), a.action) for a in actions
    ]
    assert {a.path.name for a in actions} == {p.name for p in expected_paths}
    for p in expected_paths:
        assert not p.exists(), f"{p} should have been deleted"


def test_each_registered_kind_with_underscore_separator_gets_deleted(tmp_path: Path):
    """Variants like `folder_miner_prompt.txt` (underscore separator —
    different agent improvisations vary) also match."""
    since = time.time() - 5.0
    for kind in _registered_prompt_kinds():
        kind_underscore = kind.replace("-", "_")
        _make_new_file(tmp_path / f"{kind_underscore}_prompt.txt", "PII")

    actions = cleanup_stray_prompts(since_timestamp=since, scan_dir=tmp_path)

    deleted_names = {a.path.name for a in actions if a.action == "deleted"}
    # We expect at least one per kind (some kinds with dashes also match
    # both forms; underscore variant is what we're testing here).
    assert len(deleted_names) == len(_registered_prompt_kinds())


def test_md_suffix_also_matches(tmp_path: Path):
    """`.md` is accepted in addition to `.txt` — agents have been seen
    using both as the prompt-staging extension."""
    since = time.time() - 5.0
    p = _make_new_file(tmp_path / "tailor-prompt.md", "PII")

    actions = cleanup_stray_prompts(since_timestamp=since, scan_dir=tmp_path)

    assert len(actions) == 1
    assert actions[0].action == "deleted"
    assert actions[0].path == p
    assert not p.exists()


# ---------------------------------------------------------------------------
# mtime gate: pre-existing files are never touched
# ---------------------------------------------------------------------------


def test_pre_existing_prompt_file_left_alone_by_mtime_gate(tmp_path: Path):
    """A file with the canonical name BUT mtime older than `since` is
    not from this run — leave it alone. Pre-existing-file safety net."""
    since = time.time()  # right now
    pre_existing = _make_old_file(tmp_path / "folder-miner-prompt.txt", "old")

    actions = cleanup_stray_prompts(since_timestamp=since, scan_dir=tmp_path)

    assert actions == []
    assert pre_existing.exists()


# ---------------------------------------------------------------------------
# Suffix / extension gate
# ---------------------------------------------------------------------------


def test_unsupported_suffix_left_alone(tmp_path: Path):
    """A file with the right stem but a non-text extension is not from
    a prompt-staging path — agents stage as .txt or .md, never .json /
    .pickle / etc. Defensive: don't touch."""
    since = time.time() - 5.0
    weird = _make_new_file(tmp_path / "tailor-prompt.json", '{"some": "json"}')

    actions = cleanup_stray_prompts(since_timestamp=since, scan_dir=tmp_path)

    assert actions == []
    assert weird.exists()


# ---------------------------------------------------------------------------
# Generic /tmp files are immune
# ---------------------------------------------------------------------------


def test_generic_tmp_files_immune_to_scan(tmp_path: Path):
    """Other tools' /tmp files (bash_history, screenshots, random.txt)
    must never be touched even with new mtimes. The pattern match is
    strict — basename must match a registered kind."""
    since = time.time() - 5.0
    _make_new_file(tmp_path / "bash_history", "shell history")
    _make_new_file(tmp_path / "screenshot.png", "fake png bytes")
    _make_new_file(tmp_path / "random.txt", "some other tool's output")
    _make_new_file(tmp_path / "notes.md", "user notes unrelated")
    _make_new_file(tmp_path / "tailor.txt", "missing -prompt suffix")  # near-miss

    actions = cleanup_stray_prompts(since_timestamp=since, scan_dir=tmp_path)

    assert actions == []
    # All files still present.
    for p in tmp_path.iterdir():
        assert p.exists()


def test_near_miss_names_not_matched(tmp_path: Path):
    """`tailor.txt` (no -prompt suffix), `prompt.txt` (no kind prefix),
    and `tailor-prompt-backup.txt` (extra suffix) all stay."""
    since = time.time() - 5.0
    near_misses = [
        tmp_path / "tailor.txt",
        tmp_path / "prompt.txt",
        tmp_path / "tailor-prompt-backup.txt",
        tmp_path / "tailor-prompt.txt.bak",
    ]
    for p in near_misses:
        _make_new_file(p, "near miss")

    actions = cleanup_stray_prompts(since_timestamp=since, scan_dir=tmp_path)

    assert actions == []
    for p in near_misses:
        assert p.exists()


# ---------------------------------------------------------------------------
# Top-level only: subdirectories are never scanned
# ---------------------------------------------------------------------------


def test_subdirectory_files_never_touched(tmp_path: Path):
    """A `<scan_dir>/subdir/tailor-prompt.txt` must not be deleted —
    the scan is top-level only by design (other tools' subdirs in /tmp
    have their own conventions)."""
    since = time.time() - 5.0
    sub = tmp_path / "subdir"
    sub.mkdir()
    nested = _make_new_file(sub / "tailor-prompt.txt", "nested PII")

    actions = cleanup_stray_prompts(since_timestamp=since, scan_dir=tmp_path)

    assert actions == []
    assert nested.exists()


# ---------------------------------------------------------------------------
# Defensive non-crashing behavior
# ---------------------------------------------------------------------------


def test_missing_scan_dir_returns_empty_actions(tmp_path: Path):
    """A scan_dir that doesn't exist returns empty actions, never raises.
    Some constrained runtimes don't have /tmp; treat that as no-op."""
    nonexistent = tmp_path / "definitely-not-here"
    actions = cleanup_stray_prompts(since_timestamp=0.0, scan_dir=nonexistent)
    assert actions == []


def test_scan_dir_pointing_at_a_file_returns_empty(tmp_path: Path):
    """If scan_dir is a path to a regular file (caller bug), bail
    cleanly rather than crash."""
    f = _make_new_file(tmp_path / "actually-a-file.txt", "x")
    actions = cleanup_stray_prompts(since_timestamp=0.0, scan_dir=f)
    assert actions == []


def test_empty_scan_dir_returns_empty_actions(tmp_path: Path):
    """Cleanup of an empty dir is a no-op."""
    actions = cleanup_stray_prompts(since_timestamp=time.time(), scan_dir=tmp_path)
    assert actions == []


# ---------------------------------------------------------------------------
# CLI: cleanup-stray-prompts subcommand emits the expected JSON summary
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scripts.orchestration", *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )


def test_cli_cleanup_stray_prompts_deletes_and_emits_summary(tmp_path: Path):
    """End-to-end: invoke `cleanup-stray-prompts` via the CLI on a
    tmpdir staged with one rogue file, assert the file is gone and the
    JSON summary lists the action."""
    since = time.time() - 5.0
    rogue = _make_new_file(tmp_path / "tailor-prompt.txt", "PII content")

    r = _run_cli(
        "cleanup-stray-prompts",
        "--since-timestamp", str(since),
        "--scan-dir", str(tmp_path),
    )
    assert r.returncode == 0, r.stderr

    summary = json.loads(r.stdout)
    assert summary["deleted"] == 1
    assert summary["skipped"] == 0
    assert len(summary["actions"]) == 1
    assert summary["actions"][0]["action"] == "deleted"
    assert summary["actions"][0]["path"] == str(rogue)
    assert not rogue.exists()


def test_cli_cleanup_stray_prompts_default_scan_dir_is_tmp(tmp_path: Path):
    """When --scan-dir is omitted, default is /tmp. We don't actually
    poke /tmp here (running tests shouldn't touch system /tmp); we just
    verify the CLI accepts the omission and exits 0 (no crash on
    permission / missing scan dir paths)."""
    r = _run_cli("cleanup-stray-prompts", "--since-timestamp", "0")
    assert r.returncode == 0, r.stderr
    summary = json.loads(r.stdout)
    assert "actions" in summary
    assert "deleted" in summary
    # We don't assert the count here — it depends on what's in real /tmp.


def test_cli_cleanup_stray_prompts_exits_zero_on_missing_scan_dir(tmp_path: Path):
    """Always exits 0 — cleanup failures are non-fatal to the orchestrator."""
    nonexistent = tmp_path / "missing"
    r = _run_cli(
        "cleanup-stray-prompts",
        "--since-timestamp", "0",
        "--scan-dir", str(nonexistent),
    )
    assert r.returncode == 0
    summary = json.loads(r.stdout)
    assert summary["deleted"] == 0
    assert summary["actions"] == []


# ---------------------------------------------------------------------------
# SKILL.md prescription check (deterministic)
# ---------------------------------------------------------------------------


def test_skill_md_prescribes_run_dir_prompts_for_staging():
    """SKILL.md must explicitly prescribe `$RUN_DIR/prompts/` for prompt
    staging and forbid `/tmp/`. If a future edit drops the prescription,
    this test catches it before the agent has to discover the bad path
    on its own."""
    skill_md = REPO_ROOT / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    assert "$RUN_DIR/prompts/" in text, (
        "SKILL.md must prescribe $RUN_DIR/prompts/ for prompt staging"
    )
    # And it must forbid /tmp/ for prompt staging specifically.
    assert "`/tmp/` is forbidden" in text or (
        "NEVER `/tmp/`" in text or "NEVER /tmp/" in text
    ), (
        "SKILL.md must explicitly forbid /tmp/ for prompt staging "
        "(students' resume + JD content would leak as plaintext PII)"
    )


def test_skill_md_calls_cleanup_stray_prompts_after_dispatch():
    """The cleanup call must be wired into Phase 8 (post-dispatch).
    Without it, the belt-and-suspenders shape collapses to authoring-
    discipline only."""
    skill_md = REPO_ROOT / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    assert "cleanup-stray-prompts" in text, (
        "SKILL.md must invoke `cleanup-stray-prompts` after sub-agent dispatch"
    )
