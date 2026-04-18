#!/usr/bin/env bash
#
# resumasher installer.
#
# Usage:
#   bash install.sh                # install to ~/.claude/skills/resumasher
#   bash install.sh /custom/path   # install to a custom path
#
# After this runs, restart Claude Code to pick up the skill.

set -euo pipefail

TARGET="${1:-$HOME/.claude/skills/resumasher}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing resumasher to: $TARGET"
mkdir -p "$(dirname "$TARGET")"

# If TARGET already exists, refuse to clobber without confirmation.
if [ -e "$TARGET" ] && [ "$TARGET" != "$REPO_ROOT" ]; then
  echo "WARNING: $TARGET already exists. Remove it and retry, or pass a different path."
  exit 1
fi

# If we're running from a git clone, symlink so updates propagate.
# Otherwise, copy the files.
if [ -d "$REPO_ROOT/.git" ] && [ "$TARGET" != "$REPO_ROOT" ]; then
  ln -s "$REPO_ROOT" "$TARGET"
  echo "Linked $TARGET -> $REPO_ROOT"
else
  if [ "$TARGET" != "$REPO_ROOT" ]; then
    cp -R "$REPO_ROOT" "$TARGET"
    echo "Copied $REPO_ROOT -> $TARGET"
  fi
fi

# Set up the Python virtual environment.
VENV="$TARGET/.venv"
if [ ! -d "$VENV" ]; then
  echo "Creating venv at $VENV"
  python3 -m venv "$VENV"
fi

# Install Python deps.
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$TARGET/requirements.txt"

echo ""
echo "resumasher installed."
echo ""
echo "Next steps:"
echo "  1. Restart Claude Code."
echo "  2. cd to a folder containing resume.md"
echo "  3. Run: /resumasher <job-source>"
echo ""
echo "Try it on the fixtures first:"
echo "  cd $TARGET/GOLDEN_FIXTURES"
echo "  /resumasher sample-jd.md"
