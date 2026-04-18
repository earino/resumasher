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

# Verify python3 is available.
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is not installed or not on PATH." >&2
  echo "Install Python 3.10+ and retry." >&2
  exit 1
fi

# Create or reuse the venv.
VENV="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV" ]; then
  echo "Creating venv at $VENV"
  python3 -m venv "$VENV"
else
  echo "Reusing existing venv at $VENV"
fi

# Install dependencies. Use a generous timeout and retry count so students
# on coffee-shop / hotel / airport wifi still see a successful install.
PIP_OPTS="--default-timeout=120 --retries=5"
echo "Installing dependencies (this can take ~30s on a fast connection, longer on slow wifi)..."
"$VENV/bin/pip" install $PIP_OPTS --quiet --upgrade pip
"$VENV/bin/pip" install $PIP_OPTS --quiet -r "$SCRIPT_DIR/requirements.txt"

echo ""
echo "resumasher installed at $SCRIPT_DIR"
echo ""
echo "Next steps:"
echo "  1. Restart Claude Code."
echo "  2. cd to a folder containing resume.md (try GOLDEN_FIXTURES/ for a demo)."
echo "  3. Run: /resumasher <job-source>"
