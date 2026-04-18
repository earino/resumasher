"""
Tests for scripts/github_mine.py — mock the transport so tests are offline
and deterministic.

Strategy: patch `_api_call` (the unified transport entry point) with a
canned dict/list per endpoint. Exercises the full fetch → filter → prose
pipeline without hitting GitHub.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import github_mine as gm


# ---------------------------------------------------------------------------
# Fixtures: canned GitHub API responses
# ---------------------------------------------------------------------------


def _repo(
    name: str,
    *,
    description: str = "",
    language: str = "Python",
    pushed_at: str = "2026-03-01T12:00:00Z",
    fork: bool = False,
    archived: bool = False,
    size: int = 100,
    stargazers: int = 0,
    topics: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "description": description,
        "language": language,
        "pushed_at": pushed_at,
        "fork": fork,
        "archived": archived,
        "size": size,
        "stargazers_count": stargazers,
        "topics": topics or [],
    }


def _readme_response(text: str) -> dict:
    return {"content": base64.b64encode(text.encode("utf-8")).decode("ascii")}


SAMPLE_REPOS = [
    _repo(
        "churn-model",
        description="XGBoost churn classifier on retail banking data.",
        language="Python",
        pushed_at="2026-04-10T10:00:00Z",
        topics=["machine-learning", "xgboost"],
        stargazers=8,
    ),
    _repo(
        "firmx-capstone",
        description="6-week consulting engagement, Central European retailer.",
        language="Python",
        pushed_at="2026-03-20T10:00:00Z",
        topics=["forecasting", "prophet"],
    ),
    _repo(
        "forked-repo",
        description="A fork we should ignore",
        fork=True,
    ),
    _repo(
        "old-project",
        description="Archived, should be excluded",
        archived=True,
    ),
    _repo(
        "empty-repo",
        description="Empty; should be excluded",
        size=0,
    ),
]


def _canned_api(username: str, readmes: dict[str, str] | None = None):
    """
    Returns a function suitable for patching `_api_call`. Maps endpoint →
    canned response using the SAMPLE_REPOS fixture and provided readmes.
    """
    readmes = readmes or {}

    def _impl(endpoint: str, prefer_gh: bool = True):
        if endpoint.startswith(f"/users/{username}/repos"):
            return SAMPLE_REPOS
        if endpoint.startswith(f"/repos/{username}/"):
            # /repos/{user}/{repo}/readme
            parts = endpoint.split("/")
            if len(parts) >= 5 and parts[-1] == "readme":
                repo_name = parts[-2]
                if repo_name in readmes:
                    return _readme_response(readmes[repo_name])
                raise gm.NotFoundError(f"no readme for {repo_name}")
        raise gm.APIError(f"unexpected endpoint: {endpoint}")

    return _impl


# ---------------------------------------------------------------------------
# fetch_repos + filtering
# ---------------------------------------------------------------------------


def test_fetch_repos_excludes_forks_archived_and_empty():
    with patch.object(gm, "_api_call", side_effect=_canned_api("ana")):
        repos = gm.fetch_repos("ana")
    names = [r.name for r in repos]
    assert "churn-model" in names
    assert "firmx-capstone" in names
    assert "forked-repo" not in names
    assert "old-project" not in names
    assert "empty-repo" not in names


def test_fetch_repos_respects_cap():
    many = [_repo(f"repo-{i}", pushed_at=f"2026-03-{(i % 28) + 1:02d}T10:00:00Z") for i in range(40)]

    def api(endpoint: str, prefer_gh: bool = True):
        if "/repos" in endpoint and "readme" not in endpoint:
            return many
        raise gm.NotFoundError(f"no readme for {endpoint}")

    with patch.object(gm, "_api_call", side_effect=api):
        repos = gm.fetch_repos("someone", cap=5)
    assert len(repos) == 5


def test_fetch_repos_includes_readme_when_present():
    with patch.object(
        gm,
        "_api_call",
        side_effect=_canned_api(
            "ana",
            readmes={
                "churn-model": "# Churn Model\n\nF1=0.82 on 2.3M rows.",
            },
        ),
    ):
        repos = gm.fetch_repos("ana")
    churn = next(r for r in repos if r.name == "churn-model")
    assert "F1=0.82" in churn.readme
    # firmx-capstone had no README in the fixture — should be empty, not crash.
    firmx = next(r for r in repos if r.name == "firmx-capstone")
    assert firmx.readme == ""


def test_fetch_repos_handles_missing_readme_gracefully():
    """A repo with no README should still show up, just without README text."""
    with patch.object(gm, "_api_call", side_effect=_canned_api("ana", readmes={})):
        repos = gm.fetch_repos("ana")
    # All non-fork, non-archived, non-empty repos still present
    assert any(r.name == "churn-model" for r in repos)
    assert all(r.readme == "" for r in repos)


# ---------------------------------------------------------------------------
# to_prose_context
# ---------------------------------------------------------------------------


def test_prose_includes_repo_names_and_metadata():
    repos = [
        gm.RepoEvidence(
            name="churn-model",
            description="XGBoost classifier",
            topics=["machine-learning", "xgboost"],
            language="Python",
            pushed_at="2026-04-10T10:00:00Z",
            stargazers=8,
            readme="# Churn Model\nF1=0.82",
        ),
    ]
    prose = gm.to_prose_context("ana", repos)
    assert "GITHUB_PROFILE: ana" in prose
    assert "GITHUB_REPO: ana/churn-model" in prose
    assert "XGBoost classifier" in prose
    assert "F1=0.82" in prose
    assert "Python" in prose
    assert "xgboost" in prose  # topic
    assert "stars: 8" in prose


def test_prose_handles_zero_repos():
    prose = gm.to_prose_context("ghost", [])
    assert "ghost" in prose
    assert "No public non-fork" in prose


def test_prose_labels_missing_readme():
    repos = [
        gm.RepoEvidence(
            name="bare",
            description="",
            topics=[],
            language=None,
            pushed_at="2026-01-01T00:00:00Z",
            stargazers=0,
            readme="",
        )
    ]
    prose = gm.to_prose_context("ana", repos)
    assert "README: (none)" in prose


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_roundtrip(tmp_path: Path):
    gm.save_cached(tmp_path, "ana", "some prose")
    assert gm.load_cached(tmp_path, "ana") == "some prose"


def test_cache_miss_on_expired(tmp_path: Path):
    gm.save_cached(tmp_path, "ana", "stale prose")
    cache_file = gm.cache_path(tmp_path, "ana")
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["fetched_at_epoch"] = time.time() - 7200  # 2 hours ago
    cache_file.write_text(json.dumps(payload), encoding="utf-8")
    # TTL is 1h by default; 2h should be stale.
    assert gm.load_cached(tmp_path, "ana") is None


def test_cache_miss_on_missing(tmp_path: Path):
    assert gm.load_cached(tmp_path, "nobody") is None


def test_cache_miss_on_malformed(tmp_path: Path):
    path = gm.cache_path(tmp_path, "ana")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert gm.load_cached(tmp_path, "ana") is None


# ---------------------------------------------------------------------------
# mine_github (the top-level entry point)
# ---------------------------------------------------------------------------


def test_mine_github_caches_after_first_fetch(tmp_path: Path):
    call_count = {"n": 0}
    canned = _canned_api("ana", readmes={"churn-model": "readme body"})

    def counting(endpoint: str, prefer_gh: bool = True):
        call_count["n"] += 1
        return canned(endpoint, prefer_gh)

    with patch.object(gm, "_api_call", side_effect=counting):
        prose1 = gm.mine_github("ana", cwd=tmp_path)
        first_calls = call_count["n"]
        prose2 = gm.mine_github("ana", cwd=tmp_path)
        second_calls = call_count["n"]
    assert prose1 == prose2
    assert second_calls == first_calls, "second call should be a cache hit"


def test_mine_github_no_cache_forces_refetch(tmp_path: Path):
    canned = _canned_api("ana", readmes={"churn-model": "readme body"})
    call_count = {"n": 0}

    def counting(endpoint: str, prefer_gh: bool = True):
        call_count["n"] += 1
        return canned(endpoint, prefer_gh)

    with patch.object(gm, "_api_call", side_effect=counting):
        gm.mine_github("ana", cwd=tmp_path, use_cache=True)
        first = call_count["n"]
        gm.mine_github("ana", cwd=tmp_path, use_cache=False)
        second = call_count["n"]
    assert second > first


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_strips_url_prefixes(tmp_path: Path):
    """Students might paste 'https://github.com/earino' — accept that."""
    import subprocess
    # Use --no-cache and cap=0 via mocking won't work for subprocess; instead
    # just verify the CLI doesn't reject URL-prefixed inputs outright. Cap to
    # a tiny size to avoid hitting the real API meaningfully.
    # Easier: patch at the library level by pointing PYTHONPATH + monkeypatch.
    # Here we just assert the URL-prefix stripping via a dry code path.
    from scripts.github_mine import _cli
    import sys as _sys

    with patch.object(gm, "mine_github", return_value="PROSE_OK") as stubbed:
        argv_backup = _sys.argv
        try:
            _sys.argv = ["github_mine", "https://github.com/earino", "--cwd", str(tmp_path)]
            rc = _cli()
        finally:
            _sys.argv = argv_backup
    assert rc == 0
    # mine_github should have been called with bare username, no URL.
    assert stubbed.call_args.args[0] == "earino"
