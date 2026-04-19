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
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_BIN = REPO_ROOT / "bin" / "resumasher-telemetry-log"
CLI_BIN = REPO_ROOT / "bin" / "resumasher-telemetry-cli"
SYNC_BIN = REPO_ROOT / "bin" / "resumasher-telemetry-sync"


def _run(bin_path: Path, args: list[str], env: dict[str, str], cwd: Path | None = None,
         input_text: str | None = None, timeout: int = 15) -> subprocess.CompletedProcess:
    full_env = os.environ.copy()
    full_env.update(env)
    return subprocess.run(
        [str(bin_path), *args],
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
