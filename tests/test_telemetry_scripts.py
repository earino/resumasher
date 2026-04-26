"""
Tests for bin/resumasher-telemetry-{log,sync,cli}.

These shell scripts have no Python entrypoint; we run them via subprocess
with a sandboxed RESUMASHER_STATE_DIR so each test gets a clean state.
We never hit the real Supabase backend — sync is exercised but pointed
at an unreachable URL so curl times out fast (max-time 10s in the script,
but our tests use a fake URL that fails connection-refused immediately).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _resolve_bash() -> str:
    """Pick the bash interpreter to invoke under subprocess.

    On Windows GitHub Actions runners, `bash` on Windows PATH often resolves
    to `C:\\Windows\\System32\\bash.exe` (the Windows Subsystem for Linux
    launcher) BEFORE Git Bash (`C:\\Program Files\\Git\\bin\\bash.exe`). WSL's
    bash emits "Windows Subsystem for Linux has no installed distributions"
    to stdout (in UTF-16, no less) and exits non-zero. That's what we hit
    on the first Windows CI run after adding the bash prefix to the
    subprocess call — the prefix fixed WinError 193 but let WSL intercept.

    Resolution order:
      1. `$BASH` env var — set by GitHub Actions when the job runs under
         `defaults.run.shell: bash` (points at Git Bash on Windows runners).
      2. Well-known Git Bash path on Windows, if present.
      3. Plain `bash` — correct on POSIX, and the last resort on Windows.
    """
    env_bash = os.environ.get("BASH")
    if env_bash:
        return env_bash
    if sys.platform == "win32":
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        if git_bash.is_file():
            return str(git_bash)
    return "bash"


REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_BIN = REPO_ROOT / "bin" / "resumasher-telemetry-log"
CLI_BIN = REPO_ROOT / "bin" / "resumasher-telemetry-cli"
SYNC_BIN = REPO_ROOT / "bin" / "resumasher-telemetry-sync"


def _run(bin_path: Path, args: list[str], env: dict[str, str], cwd: Path | None = None,
         input_text: str | None = None, timeout: int = 15) -> subprocess.CompletedProcess:
    # Invoke through `bash` explicitly rather than letting the OS resolve the
    # script's shebang. On POSIX the kernel would honor `#!/usr/bin/env bash`
    # directly. On Windows, Python's CreateProcessW can only launch PE/EXE
    # files — `subprocess.run([str(bin_path), ...])` raises WinError 193.
    # Git Bash has its own shebang layer but it isn't reached from Python
    # subprocess. `_resolve_bash()` picks Git Bash explicitly on Windows —
    # plain `bash` on Windows PATH often resolves to WSL's stub first.
    full_env = os.environ.copy()
    full_env.update(env)
    return subprocess.run(
        [_resolve_bash(), str(bin_path), *args],
        env=full_env,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input_text,
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> dict[str, Path]:
    """Sandboxed STATE_DIR + a fake student CWD with a config.json."""
    state = tmp_path / "state"
    student = tmp_path / "student"
    (student / ".resumasher").mkdir(parents=True)
    return {"state": state, "student": student, "tmp": tmp_path}


def _write_config(student_cwd: Path, tier: str) -> Path:
    config = student_cwd / ".resumasher" / "config.json"
    config.write_text(json.dumps({"telemetry": tier, "name": "Test"}))
    return config


def _env(state_dir: Path, **extra: str) -> dict[str, str]:
    """Standard env for telemetry-log: state dir + unreachable Supabase
    so background sync fails fast (port 1 is reserved, instant ECONNREFUSED)."""
    base = {
        "RESUMASHER_STATE_DIR": str(state_dir),
        "RESUMASHER_HOST": "claude_code",
        "RESUMASHER_SUPABASE_URL": "http://127.0.0.1:1",
        "RESUMASHER_SUPABASE_ANON_KEY": "fake",
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# resumasher-telemetry-log
# ---------------------------------------------------------------------------


def test_log_skips_when_tier_off(sandbox):
    """tier=off → no JSONL file is written."""
    _write_config(sandbox["student"], "off")
    res = _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
        "--run-id", "r1",
    ], env=_env(sandbox["state"]))
    assert res.returncode == 0
    jsonl = sandbox["state"] / "analytics" / "skill-usage.jsonl"
    assert not jsonl.exists()
    # No installation-id either (only generated under community).
    assert not (sandbox["state"] / "installation-id").exists()


def test_log_writes_event_under_anonymous(sandbox):
    """tier=anonymous → JSONL has the event WITHOUT installation_id."""
    _write_config(sandbox["student"], "anonymous")
    res = _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
        "--run-id", "r1",
    ], env=_env(sandbox["state"]))
    assert res.returncode == 0
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text()
    assert '"event_type":"run_started"' in jsonl
    assert '"run_id":"r1"' in jsonl
    assert "installation_id" not in jsonl  # anonymous omits it
    assert not (sandbox["state"] / "installation-id").exists()


def test_log_generates_install_id_under_community(sandbox):
    """tier=community → installation_id is generated, persisted, included."""
    _write_config(sandbox["student"], "community")
    res = _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
        "--run-id", "r1",
    ], env=_env(sandbox["state"]))
    assert res.returncode == 0
    install_file = sandbox["state"] / "installation-id"
    assert install_file.exists()
    install_id = install_file.read_text().strip()
    assert len(install_id) >= 16  # UUID-shaped
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text()
    assert f'"installation_id":"{install_id}"' in jsonl


def test_log_reuses_existing_install_id(sandbox):
    """Second event under community reuses the same installation_id."""
    _write_config(sandbox["student"], "community")
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
        "--run-id", "r1",
    ], env=_env(sandbox["state"]))
    install_id_1 = (sandbox["state"] / "installation-id").read_text().strip()

    _run(LOG_BIN, [
        "--event-type", "run_completed",
        "--cwd", str(sandbox["student"]),
        "--run-id", "r1",
        "--duration", "30",
    ], env=_env(sandbox["state"]))
    install_id_2 = (sandbox["state"] / "installation-id").read_text().strip()

    assert install_id_1 == install_id_2
    jsonl_lines = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text().strip().split("\n")
    assert len(jsonl_lines) == 2
    for line in jsonl_lines:
        assert install_id_1 in line


def test_log_computes_time_of_day_when_omitted(sandbox):
    """If --time-of-day is not passed, the script computes it from local TZ."""
    _write_config(sandbox["student"], "anonymous")
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
    ], env=_env(sandbox["state"]))
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text()
    assert '"time_of_day_bucket":' in jsonl
    # Must be one of the 5 buckets.
    found = False
    for bucket in ["late_night", "morning", "afternoon", "evening", "unknown"]:
        if f'"time_of_day_bucket":"{bucket}"' in jsonl:
            found = True
            break
    assert found, f"no expected bucket in: {jsonl}"


def test_log_dedupes_run_started_for_same_run_id(sandbox):
    """Orchestrator retries that re-enter Phase 1 should not produce
    duplicate run_started events for the same run_id. Regression from
    the 2026-04-19 Gemini run which had 2× run_started + 2× run_completed."""
    _write_config(sandbox["student"], "community")
    # First fire
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
        "--run-id", "dedupe-test-1",
    ], env=_env(sandbox["state"]))
    # Retry: same run_id, same event_type
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
        "--run-id", "dedupe-test-1",
    ], env=_env(sandbox["state"]))
    # Only ONE event should be in the JSONL.
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text().strip()
    lines = [l for l in jsonl.split("\n") if l]
    assert len(lines) == 1, f"expected 1 run_started event, got {len(lines)}"


def test_log_dedupes_run_completed_for_same_run_id(sandbox):
    """Same dedup guarantee on the Phase 9 terminal event."""
    _write_config(sandbox["student"], "community")
    for _ in range(2):
        _run(LOG_BIN, [
            "--event-type", "run_completed",
            "--cwd", str(sandbox["student"]),
            "--run-id", "dedupe-test-2",
            "--duration", "120",
            "--outcome", "success",
        ], env=_env(sandbox["state"]))
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text().strip()
    lines = [l for l in jsonl.split("\n") if l]
    assert len(lines) == 1


def test_log_does_not_dedupe_placeholder_fill_choice(sandbox):
    """placeholder_fill_choice is INTENTIONALLY exempt from dedup —
    it fires once per placeholder resolved, typically multiple per run."""
    _write_config(sandbox["student"], "community")
    for choice in ["specifics", "soften", "drop"]:
        _run(LOG_BIN, [
            "--event-type", "placeholder_fill_choice",
            "--cwd", str(sandbox["student"]),
            "--run-id", "exempt-test",
            "--choice-type", choice,
        ], env=_env(sandbox["state"]))
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text().strip()
    lines = [l for l in jsonl.split("\n") if l]
    assert len(lines) == 3


def test_log_different_run_ids_bypass_dedup(sandbox):
    """Second legitimate /resumasher invocation (new run_id) should fire
    its own run_started event — the dedup is per (run_id, event_type)."""
    _write_config(sandbox["student"], "community")
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
        "--run-id", "first-run",
    ], env=_env(sandbox["state"]))
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
        "--run-id", "second-run",
    ], env=_env(sandbox["state"]))
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text().strip()
    lines = [l for l in jsonl.split("\n") if l]
    assert len(lines) == 2


def test_log_strips_embedded_newlines_from_numeric_values(sandbox):
    """Defensive: callers that pass '0\\n0' (e.g. from `grep -c ... || echo 0`
    returning doubled output on zero-match) must not corrupt the JSONL.

    The malformed input comes from the Gemini test on 2026-04-19: SKILL.md's
    count_placeholders function returned '0\\n0' and that newline survived
    into `--num-placeholders`, splitting run_completed into two JSONL lines
    and breaking the whole batch sync."""
    _write_config(sandbox["student"], "anonymous")
    _run(LOG_BIN, [
        "--event-type", "run_completed",
        "--cwd", str(sandbox["student"]),
        "--num-placeholders", "0\n0",
        "--fit-score", "7\n\n",
    ], env=_env(sandbox["state"]))
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text()
    # One logical line, one newline at the end — no embedded breaks.
    assert jsonl.count("\n") == 1, f"JSONL has {jsonl.count(chr(10))} newlines, expected 1"
    parsed = json.loads(jsonl.strip())
    assert parsed["num_placeholders_emitted"] == 0
    assert parsed["fit_score"] == 7


def test_log_strips_embedded_newlines_from_boolean_values(sandbox):
    """Same defense applied to boolean flags — a caller that passes
    'true\\n' shouldn't corrupt the output."""
    _write_config(sandbox["student"], "anonymous")
    _run(LOG_BIN, [
        "--event-type", "tailor_completed",
        "--cwd", str(sandbox["student"]),
        "--used-multirole-format", "true\n",
    ], env=_env(sandbox["state"]))
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text()
    assert jsonl.count("\n") == 1
    parsed = json.loads(jsonl.strip())
    assert parsed["used_multirole_format"] is True


def test_log_strips_dangerous_chars_from_strings(sandbox):
    """Quotes, backslashes, newlines must be stripped from string fields."""
    _write_config(sandbox["student"], "anonymous")
    _run(LOG_BIN, [
        "--event-type", "run_completed",
        "--cwd", str(sandbox["student"]),
        "--company", 'Evil"Corp\\with\nnewlines',
    ], env=_env(sandbox["state"]))
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text()
    # Must still be valid JSON when parsed.
    line = jsonl.strip().split("\n")[0]
    parsed = json.loads(line)
    assert parsed["event_type"] == "run_completed"
    assert "\n" not in parsed["company"]
    assert '"' not in parsed["company"]
    assert "\\" not in parsed["company"]


def test_log_includes_host_when_flag_passed(sandbox):
    """--host "<id>" lands in the JSONL and takes precedence over env sniffing.

    Motivating bug: Codex CLI doesn't set CODEX_VERSION/CODEX env vars, so the
    env-based host detection falls back to "unknown". The fix is orchestrator
    self-reporting via --host, same pattern as --model."""
    _write_config(sandbox["student"], "anonymous")
    # Explicitly clear RESUMASHER_HOST env override so we're testing --host flag.
    env = _env(sandbox["state"])
    env.pop("RESUMASHER_HOST", None)
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
        "--host", "codex_cli",
    ], env=env)
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text().strip()
    parsed = json.loads(jsonl)
    assert parsed["host"] == "codex_cli"


def test_log_host_flag_overrides_env(sandbox):
    """If both --host and RESUMASHER_HOST are set, --host wins."""
    _write_config(sandbox["student"], "anonymous")
    env = _env(sandbox["state"])
    env["RESUMASHER_HOST"] = "claude_code"  # env says one thing
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
        "--host", "gemini_cli",  # flag says another — flag wins
    ], env=env)
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text().strip()
    parsed = json.loads(jsonl)
    assert parsed["host"] == "gemini_cli"


def test_log_host_falls_through_to_unknown_when_unset(sandbox):
    """No --host flag, no RESUMASHER_HOST, no sniffable env signal → host=unknown.
    This is the Codex behavior that prompted the --host addition.

    We have to actively clear CLAUDE_CODE env vars because the test suite
    itself runs inside Claude Code; without the explicit clear the sniffer
    sees CLAUDECODE=1 and reports claude_code, hiding the real regression."""
    _write_config(sandbox["student"], "anonymous")
    env = _env(sandbox["state"])
    env.pop("RESUMASHER_HOST", None)
    for v in ["CLAUDE_CODE_VERSION", "CLAUDECODE",
              "CODEX_VERSION", "CODEX",
              "GEMINI_VERSION", "GEMINI_CLI",
              "OPENCODE_VERSION", "OPENCODE"]:
        env.pop(v, None)
        env[v] = ""  # explicitly empty in case it's set in parent env
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
    ], env=env)
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text().strip()
    parsed = json.loads(jsonl)
    assert parsed["host"] == "unknown"


def test_log_sniffs_opencode_from_env(sandbox):
    """OPENCODE / OPENCODE_VERSION env vars trigger host=opencode_cli when
    no --host flag and no RESUMASHER_HOST override are present. Mirrors the
    sniff behavior for the other three hosts; documents the contract."""
    _write_config(sandbox["student"], "anonymous")
    env = _env(sandbox["state"])
    env.pop("RESUMASHER_HOST", None)
    # Clear other host signals so the sniffer can't pick them up first.
    for v in ["CLAUDE_CODE_VERSION", "CLAUDECODE",
              "CODEX_VERSION", "CODEX",
              "GEMINI_VERSION", "GEMINI_CLI"]:
        env.pop(v, None)
        env[v] = ""
    env["OPENCODE_VERSION"] = "1.14.25"
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
    ], env=env)
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text().strip()
    parsed = json.loads(jsonl)
    assert parsed["host"] == "opencode_cli"


def test_log_includes_model_when_flag_passed(sandbox):
    """--model "<id>" lands in the JSONL so orchestrator self-reporting works."""
    _write_config(sandbox["student"], "anonymous")
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
        "--model", "claude-opus-4-7",
    ], env=_env(sandbox["state"]))
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text().strip()
    parsed = json.loads(jsonl)
    assert parsed["model"] == "claude-opus-4-7"


def test_log_omits_model_when_flag_absent(sandbox):
    """No --model → JSONL has no 'model' key (not null, not empty string).

    Reason: the edge function stores null when the field is absent, which
    is the intended behavior for hosts/runs where the orchestrator doesn't
    know its model. Emitting 'model':null would be redundant and bloat
    the payload."""
    _write_config(sandbox["student"], "anonymous")
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
    ], env=_env(sandbox["state"]))
    jsonl = (sandbox["state"] / "analytics" / "skill-usage.jsonl").read_text().strip()
    assert '"model"' not in jsonl


def test_log_no_config_file_treats_as_off(sandbox):
    """Missing config.json → tier defaults to off → no logging."""
    # Don't write config.json
    res = _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
    ], env=_env(sandbox["state"]))
    assert res.returncode == 0
    assert not (sandbox["state"] / "analytics" / "skill-usage.jsonl").exists()


def test_log_invalid_tier_treats_as_off(sandbox):
    """Garbage tier value → defaults to off → no logging."""
    _write_config(sandbox["student"], "PleaseTrackMe")
    res = _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
    ], env=_env(sandbox["state"]))
    assert res.returncode == 0
    assert not (sandbox["state"] / "analytics" / "skill-usage.jsonl").exists()


def test_log_missing_event_type_exits_silently(sandbox):
    """Missing --event-type → exit 0 without writing."""
    _write_config(sandbox["student"], "anonymous")
    res = _run(LOG_BIN, [
        "--cwd", str(sandbox["student"]),
    ], env=_env(sandbox["state"]))
    assert res.returncode == 0
    assert not (sandbox["state"] / "analytics" / "skill-usage.jsonl").exists()


# ---------------------------------------------------------------------------
# resumasher-telemetry-cli
# ---------------------------------------------------------------------------


def test_cli_status_shows_tier_and_log_size(sandbox):
    """status reads tier from config.json and log size from state dir."""
    _write_config(sandbox["student"], "community")
    # Log one event so the log file exists
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
    ], env=_env(sandbox["state"]))

    res = _run(CLI_BIN, ["status", "--cwd", str(sandbox["student"])], env=_env(sandbox["state"]))
    assert res.returncode == 0
    assert "Tier:" in res.stdout and "community" in res.stdout
    assert "Log lines:         1" in res.stdout
    assert "Installation ID:" in res.stdout


def test_cli_set_tier_writes_config_json(sandbox):
    """set-tier <tier> mutates config.json telemetry field."""
    _write_config(sandbox["student"], "off")
    res = _run(CLI_BIN, [
        "set-tier", "anonymous",
        "--cwd", str(sandbox["student"]),
    ], env=_env(sandbox["state"]))
    assert res.returncode == 0
    config = json.loads((sandbox["student"] / ".resumasher" / "config.json").read_text())
    assert config["telemetry"] == "anonymous"
    # Other fields preserved
    assert config["name"] == "Test"


def test_cli_set_tier_creates_config_if_missing(sandbox):
    """set-tier with no existing config.json creates one."""
    res = _run(CLI_BIN, [
        "set-tier", "community",
        "--cwd", str(sandbox["student"]),
    ], env=_env(sandbox["state"]))
    assert res.returncode == 0
    config = json.loads((sandbox["student"] / ".resumasher" / "config.json").read_text())
    assert config["telemetry"] == "community"


def test_cli_set_tier_off_removes_install_id(sandbox):
    """Switching to off proactively removes the installation_id file."""
    _write_config(sandbox["student"], "community")
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
    ], env=_env(sandbox["state"]))
    assert (sandbox["state"] / "installation-id").exists()

    _run(CLI_BIN, [
        "set-tier", "off",
        "--cwd", str(sandbox["student"]),
    ], env=_env(sandbox["state"]))
    assert not (sandbox["state"] / "installation-id").exists()


def test_cli_set_tier_rejects_garbage(sandbox):
    """Invalid tier → exit 2, no mutation."""
    _write_config(sandbox["student"], "off")
    res = _run(CLI_BIN, [
        "set-tier", "garbage",
        "--cwd", str(sandbox["student"]),
    ], env=_env(sandbox["state"]))
    assert res.returncode == 2
    config = json.loads((sandbox["student"] / ".resumasher" / "config.json").read_text())
    assert config["telemetry"] == "off"


def test_cli_export_emits_jsonl(sandbox):
    """export dumps the local JSONL to stdout."""
    _write_config(sandbox["student"], "anonymous")
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
    ], env=_env(sandbox["state"]))

    res = _run(CLI_BIN, ["export", "--cwd", str(sandbox["student"])], env=_env(sandbox["state"]))
    assert res.returncode == 0
    parsed = json.loads(res.stdout.strip())
    assert parsed["event_type"] == "run_started"


# ---------------------------------------------------------------------------
# State-dir scope detection
# ---------------------------------------------------------------------------


def test_install_scope_auto_detected_as_user_home_for_claude_path(tmp_path: Path):
    """Skill at $HOME/.claude/skills/resumasher → install_scope_path=user_home
    auto-populated without --install-scope-path being passed."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    skill_root = fake_home / ".claude" / "skills" / "resumasher"
    (skill_root / "bin").mkdir(parents=True)
    shutil.copy(LOG_BIN, skill_root / "bin" / "resumasher-telemetry-log")
    for p in (skill_root / "bin").iterdir():
        p.chmod(0o755)

    student = tmp_path / "proj"
    (student / ".resumasher").mkdir(parents=True)
    _write_config(student, "community")

    env = {"HOME": str(fake_home), "RESUMASHER_HOST": "claude_code",
           "RESUMASHER_SUPABASE_URL": "http://127.0.0.1:1",
           "RESUMASHER_SUPABASE_ANON_KEY": "fake"}
    _run(skill_root / "bin" / "resumasher-telemetry-log", [
        "--event-type", "first_run_setup_completed",
        "--cwd", str(student),
    ], env=env)

    jsonl = (fake_home / ".resumasher" / "analytics" / "skill-usage.jsonl").read_text().strip()
    parsed = json.loads(jsonl)
    assert parsed["install_scope_path"] == "user_home"


def test_install_scope_auto_detected_as_user_home_for_opencode_path(tmp_path: Path):
    """Same auto-detection for OpenCode user-scope install ($HOME/.opencode/skills/...).

    Note: OpenCode also reads $HOME/.claude/skills/ as a Claude-compat directory,
    so most students will hit the .claude path covered by the test above; this
    test exercises the native OpenCode install path for completeness."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    skill_root = fake_home / ".opencode" / "skills" / "resumasher"
    (skill_root / "bin").mkdir(parents=True)
    shutil.copy(LOG_BIN, skill_root / "bin" / "resumasher-telemetry-log")
    for p in (skill_root / "bin").iterdir():
        p.chmod(0o755)

    student = tmp_path / "proj"
    (student / ".resumasher").mkdir(parents=True)
    _write_config(student, "community")

    env = {"HOME": str(fake_home), "RESUMASHER_HOST": "opencode_cli",
           "RESUMASHER_SUPABASE_URL": "http://127.0.0.1:1",
           "RESUMASHER_SUPABASE_ANON_KEY": "fake"}
    _run(skill_root / "bin" / "resumasher-telemetry-log", [
        "--event-type", "first_run_setup_completed",
        "--cwd", str(student),
    ], env=env)

    jsonl = (fake_home / ".resumasher" / "analytics" / "skill-usage.jsonl").read_text().strip()
    parsed = json.loads(jsonl)
    assert parsed["install_scope_path"] == "user_home"


def test_install_scope_auto_detected_as_user_home_for_gemini_path(tmp_path: Path):
    """Same auto-detection for Gemini user-scope install ($HOME/.gemini/skills/...)."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    skill_root = fake_home / ".gemini" / "skills" / "resumasher"
    (skill_root / "bin").mkdir(parents=True)
    shutil.copy(LOG_BIN, skill_root / "bin" / "resumasher-telemetry-log")
    for p in (skill_root / "bin").iterdir():
        p.chmod(0o755)

    student = tmp_path / "proj"
    (student / ".resumasher").mkdir(parents=True)
    _write_config(student, "community")

    env = {"HOME": str(fake_home), "RESUMASHER_HOST": "gemini_cli",
           "RESUMASHER_SUPABASE_URL": "http://127.0.0.1:1",
           "RESUMASHER_SUPABASE_ANON_KEY": "fake"}
    _run(skill_root / "bin" / "resumasher-telemetry-log", [
        "--event-type", "first_run_setup_completed",
        "--cwd", str(student),
    ], env=env)

    jsonl_path = fake_home / ".resumasher" / "analytics" / "skill-usage.jsonl"
    # Gemini path falls through to the "project-scope" branch of the state dir
    # logic, which happens to resolve to $HOME for user-scope gemini installs.
    jsonl = jsonl_path.read_text().strip()
    parsed = json.loads(jsonl)
    assert parsed["install_scope_path"] == "user_home"


def test_install_scope_auto_detected_as_project_local_for_project_install(tmp_path: Path):
    """Skill at <project>/.claude/skills/resumasher → install_scope_path=project_local."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    project = tmp_path / "some-project"
    skill_root = project / ".claude" / "skills" / "resumasher"
    (skill_root / "bin").mkdir(parents=True)
    shutil.copy(LOG_BIN, skill_root / "bin" / "resumasher-telemetry-log")
    for p in (skill_root / "bin").iterdir():
        p.chmod(0o755)

    (project / ".resumasher").mkdir(parents=True)
    _write_config(project, "community")

    env = {"HOME": str(fake_home), "RESUMASHER_HOST": "claude_code",
           "RESUMASHER_SUPABASE_URL": "http://127.0.0.1:1",
           "RESUMASHER_SUPABASE_ANON_KEY": "fake"}
    _run(skill_root / "bin" / "resumasher-telemetry-log", [
        "--event-type", "first_run_setup_completed",
        "--cwd", str(project),
    ], env=env)

    jsonl = (project / ".resumasher" / "analytics" / "skill-usage.jsonl").read_text().strip()
    parsed = json.loads(jsonl)
    assert parsed["install_scope_path"] == "project_local"


def test_install_scope_explicit_flag_wins_over_detection(tmp_path: Path):
    """--install-scope-path override still works for tests/edge cases."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    skill_root = fake_home / ".claude" / "skills" / "resumasher"
    (skill_root / "bin").mkdir(parents=True)
    shutil.copy(LOG_BIN, skill_root / "bin" / "resumasher-telemetry-log")
    for p in (skill_root / "bin").iterdir():
        p.chmod(0o755)

    student = tmp_path / "proj"
    (student / ".resumasher").mkdir(parents=True)
    _write_config(student, "community")

    env = {"HOME": str(fake_home), "RESUMASHER_HOST": "claude_code",
           "RESUMASHER_SUPABASE_URL": "http://127.0.0.1:1",
           "RESUMASHER_SUPABASE_ANON_KEY": "fake"}
    _run(skill_root / "bin" / "resumasher-telemetry-log", [
        "--event-type", "first_run_setup_completed",
        "--cwd", str(student),
        "--install-scope-path", "custom_override",
    ], env=env)

    jsonl = (fake_home / ".resumasher" / "analytics" / "skill-usage.jsonl").read_text().strip()
    parsed = json.loads(jsonl)
    assert parsed["install_scope_path"] == "custom_override"


def test_state_dir_defaults_to_home_for_user_scope_install(tmp_path: Path):
    """A skill installed at $HOME/.claude/skills/resumasher/ writes state to
    $HOME/.resumasher/ — 'user-scope install means machine-wide state'."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    skill_root = fake_home / ".claude" / "skills" / "resumasher"
    (skill_root / "bin").mkdir(parents=True)
    # Copy the real scripts into the fake skill tree so they execute with
    # the right $RESUMASHER_DIR.
    shutil.copy(LOG_BIN, skill_root / "bin" / "resumasher-telemetry-log")
    shutil.copy(SYNC_BIN, skill_root / "bin" / "resumasher-telemetry-sync")
    for p in (skill_root / "bin").iterdir():
        p.chmod(0o755)

    student = tmp_path / "some-project"
    (student / ".resumasher").mkdir(parents=True)
    _write_config(student, "community")

    # Invoke WITHOUT RESUMASHER_STATE_DIR override; HOME points at fake_home.
    env = {"HOME": str(fake_home), "RESUMASHER_HOST": "claude_code",
           # Point RESUMASHER_SUPABASE_URL at an unreachable host so the
           # inline sync fails fast without a real network call.
           "RESUMASHER_SUPABASE_URL": "http://127.0.0.1:1",
           "RESUMASHER_SUPABASE_ANON_KEY": "fake"}
    _run(skill_root / "bin" / "resumasher-telemetry-log", [
        "--event-type", "run_started",
        "--cwd", str(student),
    ], env=env)

    # State should land in $HOME/.resumasher/, NOT inside the project.
    assert (fake_home / ".resumasher" / "analytics" / "skill-usage.jsonl").exists()
    assert (fake_home / ".resumasher" / "installation-id").exists()
    # And definitely should NOT be in the project folder.
    assert not (student / ".resumasher" / "analytics").exists()
    assert not (student / ".resumasher" / "installation-id").exists()


def test_state_dir_defaults_to_project_for_project_scope_install(tmp_path: Path):
    """A skill installed at <project>/.claude/skills/resumasher/ writes
    state to <project>/.resumasher/ — scope matches scope, no leakage
    into the student's home directory."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    project = tmp_path / "some-project"
    skill_root = project / ".claude" / "skills" / "resumasher"
    (skill_root / "bin").mkdir(parents=True)
    shutil.copy(LOG_BIN, skill_root / "bin" / "resumasher-telemetry-log")
    shutil.copy(SYNC_BIN, skill_root / "bin" / "resumasher-telemetry-sync")
    for p in (skill_root / "bin").iterdir():
        p.chmod(0o755)

    (project / ".resumasher").mkdir(parents=True)
    _write_config(project, "community")

    env = {"HOME": str(fake_home), "RESUMASHER_HOST": "claude_code",
           "RESUMASHER_SUPABASE_URL": "http://127.0.0.1:1",
           "RESUMASHER_SUPABASE_ANON_KEY": "fake"}
    _run(skill_root / "bin" / "resumasher-telemetry-log", [
        "--event-type", "run_started",
        "--cwd", str(project),
    ], env=env)

    # State should land in <project>/.resumasher/, NOT in fake $HOME.
    assert (project / ".resumasher" / "analytics" / "skill-usage.jsonl").exists()
    assert (project / ".resumasher" / "installation-id").exists()
    assert not (fake_home / ".resumasher").exists()


def test_cli_delete_wipes_local_state(sandbox):
    """delete removes JSONL, cursor, install-id, sync time files."""
    _write_config(sandbox["student"], "community")
    _run(LOG_BIN, [
        "--event-type", "run_started",
        "--cwd", str(sandbox["student"]),
    ], env=_env(sandbox["state"]))
    # Touch the cursor + rate file so we can verify they get wiped too.
    analytics = sandbox["state"] / "analytics"
    (analytics / ".last-sync-line").write_text("1")
    (analytics / ".last-sync-time").touch()

    res = _run(CLI_BIN, ["delete", "--cwd", str(sandbox["student"])], env=_env(sandbox["state"]))
    assert res.returncode == 0
    # All four state files gone:
    assert not (analytics / "skill-usage.jsonl").exists()
    assert not (analytics / ".last-sync-line").exists()
    assert not (analytics / ".last-sync-time").exists()
    assert not (sandbox["state"] / "installation-id").exists()
    assert "Local telemetry state wiped" in res.stdout
