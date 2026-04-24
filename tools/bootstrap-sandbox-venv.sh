#!/usr/bin/env bash
#
# tools/bootstrap-sandbox-venv.sh
#
# Build a sandbox-only Python venv at $HOME/.venv-resumasher-sandbox/ so a
# Linux Docker container working on this repo doesn't have to share .venv/
# with the macOS host (whose .venv/ is a different platform's binaries —
# the two cannot coexist on a shared filesystem).
#
# Why this exists
# ---------------
# This repo's .venv/ belongs to whoever installed the project on the host
# (typically macOS or Linux native, via install.sh). When a sandbox/Docker
# agent shares the host's filesystem and tries to "just use .venv", things
# break: pyvenv.cfg points at /usr/bin/python3.13 (a path that may not exist
# on the host), or the sandbox builds .venv with Linux binaries the host's
# Python can't execute.
#
# This script creates a sandbox venv in $HOME (which is container-local in
# typical Docker setups) so the sandbox and host never collide.
#
# Idempotent: if the venv already exists and works, this is a no-op.
# Fast: ~30s on a fresh container with reasonable network; <1s if it exists.
#
# bin/resumasher-exec checks $HOME/.venv-resumasher-sandbox/bin/python in
# its search order, so once this runs, all tools that go through the
# wrapper find the right Python automatically.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SANDBOX_VENV="${RESUMASHER_VENV:-$HOME/.venv-resumasher-sandbox}"

# Idempotent fast-path: if the venv exists and has the project's hard deps,
# do nothing.
if [ -x "$SANDBOX_VENV/bin/python" ] && \
   "$SANDBOX_VENV/bin/python" -c "import chardet, reportlab, pytest" >/dev/null 2>&1; then
  echo "Sandbox venv already provisioned at $SANDBOX_VENV"
  exit 0
fi

# Need a Python 3.10+ to bootstrap.
_is_python_310_plus() {
  command -v "$1" >/dev/null 2>&1 \
    && "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}
if _is_python_310_plus python3; then
  PYTHON=python3
elif _is_python_310_plus python; then
  PYTHON=python
else
  echo "ERROR: Python 3.10+ required to bootstrap sandbox venv." >&2
  exit 1
fi

echo "Creating sandbox venv at $SANDBOX_VENV"
rm -rf "$SANDBOX_VENV"
"$PYTHON" -m venv "$SANDBOX_VENV"

# Install via `python -m pip` for the same reason install.sh does:
# pip.exe self-upgrade locks the file on Windows; python -m pip side-steps it.
echo "Installing dependencies (this can take ~30s)..."
"$SANDBOX_VENV/bin/python" -m pip install --quiet --upgrade --default-timeout=300 --retries=10 pip
"$SANDBOX_VENV/bin/python" -m pip install --quiet --default-timeout=300 --retries=10 -r "$SKILL_ROOT/requirements-dev.txt"

# Sanity check.
if ! "$SANDBOX_VENV/bin/python" -c "import chardet, reportlab, pytest" >/dev/null 2>&1; then
  echo "ERROR: sandbox venv built but missing expected deps. Inspect $SANDBOX_VENV." >&2
  exit 1
fi

echo "Sandbox venv ready at $SANDBOX_VENV"
echo "All bin/resumasher-exec invocations will now find this venv automatically."
