#!/usr/bin/env bash
#
# resumasher installer.
#
# This script assumes you've already cloned resumasher to its final location
# (either ~/.claude/skills/resumasher for user-scope, or
# <project>/.claude/skills/resumasher for project-scope — see README).
# It sets up the Python virtual environment and installs dependencies in
# place. No copying, no moving.
#
# Usage:
#   git clone https://github.com/earino/resumasher.git <target-dir>
#   bash <target-dir>/install.sh
#
# After this runs, restart Claude Code to pick up the skill.

set -euo pipefail

# Resolve the directory this script lives in — that IS the install location.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Setting up resumasher at: $SCRIPT_DIR"

# Find a Python 3.10+ interpreter. Prefer `python3` (standard on macOS/Linux);
# fall back to `python` (standard on Windows/Git Bash, where `python3` is
# typically absent — or is a Microsoft Store App Execution Alias stub that
# prints "Python was not found" and exits non-zero when invoked). Since
# `command -v` can't tell a working interpreter from a broken stub, actually
# invoke each candidate and confirm it runs AND reports 3.10+.
_is_python_310_plus() {
  command -v "$1" >/dev/null 2>&1 \
    && "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

if _is_python_310_plus python3; then
  PYTHON=python3
elif _is_python_310_plus python; then
  PYTHON=python
else
  echo "ERROR: Python 3.10+ is not installed or not on PATH." >&2
  echo "Install Python 3.10+ and retry." >&2
  exit 1
fi

# Create or reuse the venv.
VENV="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV" ]; then
  echo "Creating venv at $VENV"
  "$PYTHON" -m venv "$VENV"
else
  echo "Reusing existing venv at $VENV"
fi

# Windows CPython's venv layout uses Scripts/ instead of bin/. Pick whichever
# exists so the rest of this script and the wrapper in bin/resumasher-exec
# work on both layouts.
if [ -d "$VENV/bin" ]; then
  VENV_BIN="$VENV/bin"
elif [ -d "$VENV/Scripts" ]; then
  VENV_BIN="$VENV/Scripts"
else
  echo "ERROR: venv created at $VENV but neither bin/ nor Scripts/ subdirectory found." >&2
  exit 1
fi

# Install dependencies. Use a generous timeout and retry count so students
# on coffee-shop / hotel / airport wifi still see a successful install.
# PIP_OPTS is overridable from the environment so students on especially
# slow connections can bump timeout/retries without editing this file.
: "${PIP_OPTS:=--default-timeout=120 --retries=5}"
echo "Installing dependencies (this can take ~30s on a fast connection, longer on slow wifi)..."

# Friendly error wrapper around pip. set -e would turn a pip failure into
# a raw Python traceback. Catch it instead and print an actionable message.
_pip_install() {
  local label="$1"; shift
  if ! "$VENV_BIN/pip" install $PIP_OPTS --quiet "$@"; then
    cat >&2 <<EOF

ERROR: pip install for ${label} failed (exit code $?).

This is almost always a network issue, not a bug in resumasher. Try:

  1. Re-run the installer — transient PyPI slowness is common:
       bash $SCRIPT_DIR/install.sh

  2. If you're on slow wifi (coffee shop, hotel, conference), bump
     pip's tolerance and retry:
       PIP_OPTS="--default-timeout=300 --retries=10" bash $SCRIPT_DIR/install.sh

  3. If you're behind a corporate proxy, set HTTPS_PROXY before running:
       export HTTPS_PROXY=http://your.proxy:port
       bash $SCRIPT_DIR/install.sh

  4. Still failing? Open an issue with the full pip output:
       https://github.com/earino/resumasher/issues
EOF
    exit 2
  fi
}

_pip_install "pip itself" --upgrade pip
_pip_install "resumasher dependencies" -r "$SCRIPT_DIR/requirements.txt"


# Ensure the wrapper scripts in bin/ are executable. Git preserves the exec
# bit, but a zip download or a Windows-transit clone can drop it.
if [ -d "$SCRIPT_DIR/bin" ]; then
  chmod +x "$SCRIPT_DIR/bin/"* 2>/dev/null || true
fi

echo ""
echo "resumasher installed at $SCRIPT_DIR"
echo ""
echo "Next steps:"
echo "  1. Restart your AI CLI (Claude Code, Codex, or Gemini) so it picks up the new skill."
echo "  2. cd to a folder containing resume.md (try GOLDEN_FIXTURES/ for a demo)."
echo "  3. Run: /resumasher <job-source>"
