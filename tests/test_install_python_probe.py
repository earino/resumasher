"""
Regression test for install.sh's Python interpreter discovery.

On Windows, the Microsoft Store ships an "App Execution Alias" for `python3`
at C:\\Users\\<user>\\AppData\\Local\\Microsoft\\WindowsApps\\python3. It is a
real file on PATH, so `command -v python3` reports success — but executing
it prints "Python was not found..." to stderr and exits non-zero. That
behavior bit a cohort student (@b0glarka) during verification of #33: her
real Python was at /c/Python314/python, but install.sh trusted the MS Store
stub at /c/Users/boga/AppData/Local/Microsoft/WindowsApps/python3 and
aborted at `python3 -m venv` without ever falling through to the working
`python` fallback.

Windows CI (actions/setup-python@v6) cannot reproduce this: it installs
python3 at a clean hostedtoolcache path with no MS Store shim in sight. So
we simulate the stub here — a fake python3 shim on PATH that mimics the
MS Store failure mode — and assert that install.sh actually probes each
candidate (by invoking it) rather than trusting `command -v` alone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


@pytest.fixture
def fake_env_with_broken_python3():
    """Set up a PATH where `python3` is a broken stub but `python` works.

    Returns (tmpdir, env) ready to pass to subprocess.run. The caller owns
    cleanup via TemporaryDirectory's context manager.
    """
    tmp = tempfile.TemporaryDirectory()
    tmpdir = Path(tmp.name)

    # Copy install.sh plus the minimum context it reads (requirements.txt).
    # An empty requirements.txt is fine — we're not testing pip, we're
    # testing the interpreter probe. Upstream _pip_install will run pip
    # against --no-index below so we don't touch the network.
    shutil.copy(INSTALL_SH, tmpdir / "install.sh")
    (tmpdir / "requirements.txt").write_text("")

    fake_bin = tmpdir / "fake_bin"
    fake_bin.mkdir()

    # Mimic the MS Store stub: writes a Windows-flavored error to stderr and
    # exits with the same code the real stub uses (9009 = "command not found"
    # on Windows). Using 9009 instead of 1 makes the simulation faithful
    # even though our probe only checks for non-zero.
    (fake_bin / "python3").write_text(
        "#!/usr/bin/env bash\n"
        "echo 'Python was not found; run without arguments to install from the "
        "Microsoft Store, or disable this shortcut from Settings > Apps > "
        "Advanced app settings > App execution aliases.' >&2\n"
        "exit 9009\n"
    )
    (fake_bin / "python3").chmod(0o755)

    # Provide a working `python` that proxies to whatever interpreter pytest
    # is using. On CI, actions/setup-python@v6 symlinks both `python` and
    # `python3`, but we can't assume `python` exists locally on every
    # contributor's box — so create a guaranteed-working shim ourselves.
    (fake_bin / "python").write_text(
        f"#!/usr/bin/env bash\n"
        f'exec "{sys.executable}" "$@"\n'
    )
    (fake_bin / "python").chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    # --no-index forces pip to fail instantly without touching PyPI. We don't
    # care whether pip succeeds — only that install.sh got PAST the probe
    # step. The venv having been created is sufficient proof.
    env["PIP_OPTS"] = "--no-index"
    # Some dev sandboxes set BASH_ENV to a profile that re-prepends a real
    # venv's bin/ directory to PATH on every non-interactive bash invocation.
    # That would defeat the fake_bin shim and let the real python3 win,
    # silently turning this test into a no-op. Strip it.
    env.pop("BASH_ENV", None)

    try:
        yield tmpdir, env
    finally:
        tmp.cleanup()


def test_install_sh_falls_through_when_python3_is_broken_ms_store_stub(
    fake_env_with_broken_python3,
):
    """install.sh must invoke each candidate, not just check PATH presence.

    If the probe only checks `command -v python3` (pre-fix behavior), it
    will accept the fake stub, call `python3 -m venv`, and die with the
    MS Store error. The working `python` fallback is never tried.

    With the fix, the probe invokes each candidate's version check. The
    fake stub's non-zero exit disqualifies it, and install.sh proceeds to
    `python` (our shim to the real interpreter), creating the venv.
    """
    tmpdir, env = fake_env_with_broken_python3

    result = subprocess.run(
        ["bash", str(tmpdir / "install.sh")],
        cwd=tmpdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    # Venv must exist — only reachable if the probe correctly skipped the
    # broken python3 stub and picked up the working python fallback.
    assert (tmpdir / ".venv").exists(), (
        "install.sh did not create a venv despite a working `python` being "
        "available on PATH. The interpreter probe trusted the broken "
        "python3 stub.\n\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    # Belt-and-braces: make sure we didn't hit the "no Python available"
    # error path either. A stray false-negative there would also fail to
    # create a venv, but for a different reason worth distinguishing.
    combined = result.stdout + result.stderr
    assert "Python 3.10+ is not installed" not in combined, (
        "install.sh erroneously reported no Python available despite a "
        "working `python` on PATH.\n\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
