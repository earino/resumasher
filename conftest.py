"""pytest configuration — ensures scripts/ is importable in tests."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        (
            "live_llm: hits a real LLM via `claude -p` (auto-skipped when the "
            "claude CLI is not on PATH or RESUMASHER_SKIP_LIVE=1). Runs "
            "automatically on dev boxes that have Claude Code installed; "
            "auto-skips in CI."
        ),
    )
