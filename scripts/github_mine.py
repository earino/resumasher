"""
Mine a GitHub profile for resume evidence.

Fetches a user's public repos and produces a prose summary the folder-miner
sub-agent can consume alongside (or instead of) local folder evidence.

Design choices (see docs/DESIGN.md):
- Prefer `gh api` if available — reuses the user's existing auth, 5000/hr
  rate limit, zero PAT handling on our side.
- Fall back to unauthenticated `urllib` requests against the public REST API.
  Rate limit is 60/hr unauthenticated; fine for small profiles, tight for
  larger ones. Error messages point students at `gh auth login` when we hit
  the wall.
- Zero new pip dependencies. subprocess + urllib + json + base64 from stdlib.
- Cache under .resumasher/github-cache/<user>.json with a 1-hour TTL. The
  folder-miner sub-agent doesn't need minute-fresh data, and students can
  iterate on the same JD multiple times without re-hitting the API.

What we fetch per repo:
- name, description, topics, primary language, pushed_at, stargazer_count
- README (best-effort; GitHub's /readme endpoint returns whichever of
  README*, readme*, etc. the repo actually uses, base64-encoded)

What we deliberately skip:
- Forks (those are someone else's work)
- Archived repos (stale)
- Empty repos (size == 0)
- Source code, issues, PRs, contribution graph — noise for resume evidence
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


GITHUB_API = "https://api.github.com"
CACHE_TTL_SECONDS = 3600  # 1 hour
DEFAULT_REPO_CAP = 15
README_CHAR_CAP = 50_000


@dataclass
class RepoEvidence:
    name: str
    description: str
    topics: list[str]
    language: Optional[str]
    pushed_at: str
    stargazers: int
    readme: str  # markdown text, extracted and possibly truncated


# ---------------------------------------------------------------------------
# Transport: prefer `gh api`, fall back to urllib
# ---------------------------------------------------------------------------


def _have_gh() -> bool:
    return shutil.which("gh") is not None


def _gh_call(endpoint: str) -> dict | list:
    """Call the GitHub API via `gh api`. Uses the user's existing auth."""
    try:
        result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise APIError(
            f"transport=gh endpoint={endpoint}: `gh` binary not found on PATH "
            f"inside the Python subprocess. Interactive shell may have it but "
            f"the skill's subprocess may have a different PATH."
        )
    except subprocess.TimeoutExpired:
        raise APIError(
            f"transport=gh endpoint={endpoint}: `gh api` timed out after 30s. "
            f"Likely network stall or sandbox hang."
        )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # gh signals rate-limit / auth issues in stderr.
        if "rate limit" in stderr.lower() or "API rate limit exceeded" in stderr:
            raise RateLimitError(stderr)
        if "Not Found" in stderr or "404" in stderr:
            raise NotFoundError(stderr)
        # Include exit code + stderr head so truncated traces still show the
        # fault. Empty stderr is itself a signal (sandbox may be silencing it).
        stderr_display = stderr if stderr else "(empty stderr)"
        raise APIError(
            f"transport=gh endpoint={endpoint} exit={result.returncode} "
            f"stderr={stderr_display[:500]}"
        )
    return json.loads(result.stdout)


def _urllib_call(endpoint: str) -> dict | list:
    """Call the GitHub API unauthenticated."""
    url = f"{GITHUB_API}{endpoint}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "resumasher",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 403 + rate-limit message means we've hit the unauthenticated ceiling.
        if exc.code == 403:
            body = exc.read().decode("utf-8", errors="replace")
            if "rate limit" in body.lower() or "API rate limit exceeded" in body:
                raise RateLimitError(body)
            raise APIError(f"transport=urllib endpoint={endpoint} HTTP 403: {body[:500]}")
        if exc.code == 404:
            raise NotFoundError(f"transport=urllib endpoint={endpoint} HTTP 404")
        raise APIError(f"transport=urllib endpoint={endpoint} HTTP {exc.code}: {exc.reason}")
    except urllib.error.URLError as exc:
        # Covers DNS failure, connection refused, sandbox blocks, etc.
        raise APIError(
            f"transport=urllib endpoint={endpoint} URLError: {exc.reason!r}. "
            f"Likely sandbox blocking outbound HTTPS or DNS resolution."
        )
    except OSError as exc:
        raise APIError(
            f"transport=urllib endpoint={endpoint} OSError: {exc!r}"
        )


def _api_call(endpoint: str, prefer_gh: bool = True) -> dict | list:
    """
    Unified API call. Uses `gh` if available, falls back to unauthenticated.
    """
    if prefer_gh and _have_gh():
        return _gh_call(endpoint)
    return _urllib_call(endpoint)


class APIError(Exception):
    pass


class RateLimitError(APIError):
    pass


class NotFoundError(APIError):
    pass


# ---------------------------------------------------------------------------
# Fetch + transform
# ---------------------------------------------------------------------------


def fetch_repos(username: str, cap: int = DEFAULT_REPO_CAP) -> list[RepoEvidence]:
    """
    Fetch up to `cap` most-recently-pushed non-fork, non-archived public repos.
    """
    # Sort by pushed_at desc on the server side — saves us from fetching 100s
    # of repos for prolific users.
    endpoint = f"/users/{username}/repos?per_page=100&sort=pushed&direction=desc&type=owner"
    repos_raw = _api_call(endpoint)
    if not isinstance(repos_raw, list):
        raise APIError(f"Expected repo list, got {type(repos_raw).__name__}")

    interesting: list[dict] = []
    for r in repos_raw:
        if r.get("fork"):
            continue
        if r.get("archived"):
            continue
        if r.get("size", 0) == 0:
            continue
        interesting.append(r)
        if len(interesting) >= cap:
            break

    evidence: list[RepoEvidence] = []
    for r in interesting:
        readme = _fetch_readme(username, r["name"])
        evidence.append(RepoEvidence(
            name=r["name"],
            description=r.get("description") or "",
            topics=r.get("topics") or [],
            language=r.get("language"),
            pushed_at=r.get("pushed_at") or "",
            stargazers=r.get("stargazers_count") or 0,
            readme=readme,
        ))
    return evidence


def _fetch_readme(username: str, repo_name: str) -> str:
    """Fetch README (best-effort). Returns '' if the repo has none."""
    try:
        data = _api_call(f"/repos/{username}/{repo_name}/readme")
    except NotFoundError:
        return ""
    except APIError:
        # Don't let a single missing README kill the whole mine.
        return ""
    if not isinstance(data, dict):
        return ""
    content_b64 = data.get("content", "")
    if not content_b64:
        return ""
    try:
        raw = base64.b64decode(content_b64)
    except (ValueError, TypeError):
        return ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    if len(text) > README_CHAR_CAP:
        text = text[:README_CHAR_CAP] + f"\n[...truncated at {README_CHAR_CAP} chars]"
    return text


# ---------------------------------------------------------------------------
# Prose serialization
# ---------------------------------------------------------------------------


def to_prose_context(username: str, repos: list[RepoEvidence]) -> str:
    """
    Render the repo list as the same block format the folder-miner expects.
    Each repo gets a clearly-delimited entry so the LLM can cite specific
    ones by name in the downstream summary.
    """
    if not repos:
        return (
            f"=== GITHUB: {username} ===\n"
            f"No public non-fork, non-archived repositories with content found.\n"
        )

    chunks: list[str] = [f"=== GITHUB_PROFILE: {username} ==="]
    chunks.append(
        f"Fetched {len(repos)} most-recently-pushed public repos "
        f"(forks and archives excluded)."
    )

    for r in repos:
        header_parts = [f"=== GITHUB_REPO: {username}/{r.name} ==="]
        header_parts.append(f"pushed_at: {r.pushed_at}")
        if r.language:
            header_parts.append(f"language: {r.language}")
        if r.topics:
            header_parts.append(f"topics: {', '.join(r.topics)}")
        if r.stargazers:
            header_parts.append(f"stars: {r.stargazers}")
        if r.description:
            header_parts.append(f"description: {r.description}")
        chunks.append("\n".join(header_parts))

        if r.readme:
            chunks.append("README:\n" + r.readme.rstrip())
        else:
            chunks.append("README: (none)")

    return "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def cache_path(cwd: Path, username: str) -> Path:
    return cwd / ".resumasher" / "github-cache" / f"{username}.json"


def load_cached(cwd: Path, username: str, ttl: int = CACHE_TTL_SECONDS) -> Optional[str]:
    """Return cached prose if fresh, else None."""
    path = cache_path(cwd, username)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    fetched_at = data.get("fetched_at_epoch", 0)
    if time.time() - fetched_at > ttl:
        return None
    return data.get("prose")


def save_cached(cwd: Path, username: str, prose: str) -> Path:
    path = cache_path(cwd, username)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "username": username,
        "fetched_at_epoch": time.time(),
        "prose": prose,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def mine_github(
    username: str,
    cwd: Optional[Path] = None,
    cap: int = DEFAULT_REPO_CAP,
    use_cache: bool = True,
    ttl: int = CACHE_TTL_SECONDS,
) -> str:
    """
    Mine username's public GitHub for resume evidence, return prose context.

    Behavior:
    - Returns cached prose if fresh (< ttl seconds old) and use_cache is True.
    - Otherwise fetches fresh data and updates the cache.
    - Raises RateLimitError, NotFoundError, or APIError on fetch problems.
      Callers should wrap with a helpful error message — the github mine
      is non-fatal, so the skill should continue without GitHub evidence
      rather than aborting the whole run.
    """
    cwd = cwd or Path.cwd()

    if use_cache:
        cached = load_cached(cwd, username, ttl)
        if cached is not None:
            return cached

    repos = fetch_repos(username, cap=cap)
    prose = to_prose_context(username, repos)
    save_cached(cwd, username, prose)
    return prose


# ---------------------------------------------------------------------------
# CLI (invoked by orchestration.py or SKILL.md)
# ---------------------------------------------------------------------------


def _cli() -> int:
    parser = argparse.ArgumentParser(
        prog="scripts.github_mine",
        description="Mine a GitHub username for resume evidence.",
    )
    parser.add_argument("username", help="GitHub username (without @)")
    parser.add_argument(
        "--cwd",
        default=".",
        help="Student's working directory (used for cache location)",
    )
    parser.add_argument(
        "--cap",
        type=int,
        default=DEFAULT_REPO_CAP,
        help=f"Max repos to fetch (default: {DEFAULT_REPO_CAP})",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cache; always hit the API",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=CACHE_TTL_SECONDS,
        help=f"Cache TTL in seconds (default: {CACHE_TTL_SECONDS})",
    )
    args = parser.parse_args()

    # Strip common prefixes students might paste: https://github.com/earino
    # or github.com/earino. Be lenient.
    username = args.username.strip()
    for prefix in ("https://", "http://", "www."):
        if username.startswith(prefix):
            username = username[len(prefix):]
    if username.startswith("github.com/"):
        username = username[len("github.com/"):]
    username = username.strip("/").split("/")[0]

    try:
        prose = mine_github(
            username,
            cwd=Path(args.cwd),
            cap=args.cap,
            use_cache=not args.no_cache,
            ttl=args.ttl,
        )
    except RateLimitError as exc:
        print(
            "FAILURE: GitHub rate limit exceeded.\n"
            "Install the GitHub CLI and authenticate to get a 5000/hr limit:\n"
            "  brew install gh   (or https://cli.github.com)\n"
            "  gh auth login\n"
            f"Details: {exc}",
            file=sys.stderr,
        )
        return 2
    except NotFoundError:
        print(
            f"FAILURE: GitHub user '{username}' not found, or has no public repos.",
            file=sys.stderr,
        )
        return 3
    except APIError as exc:
        print(f"FAILURE: GitHub API error: {exc}", file=sys.stderr)
        return 4

    print(prose)
    return 0


if __name__ == "__main__":
    # See orchestration.py for the rationale — Windows defaults stdout/stderr
    # to CP1252 which crashes on non-ASCII glyphs (→, …, curly quotes, unicode
    # names). Force UTF-8 at the CLI boundary.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(_cli())
