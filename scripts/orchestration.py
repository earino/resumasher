"""
Deterministic orchestration helpers for resumasher.

Everything that can be done without calling an LLM lives here: parsing args,
finding the resume, hashing folder state, mining content the LLM will see,
regex-extracting fit scores, appending history, and first-run setup.

These are CLI-callable so SKILL.md can shell out without re-implementing logic
in a prompt, and importable so tests can exercise every branch.

CLI map:
    python -m scripts.orchestration parse-job-source <arg>
    python -m scripts.orchestration discover-resume <cwd>
    python -m scripts.orchestration folder-state-hash <cwd>
    python -m scripts.orchestration mine-context <cwd>
    python -m scripts.orchestration read-resume <path>
    python -m scripts.orchestration extract-fit-score <<< "prose with FIT_SCORE: 7 line"
    python -m scripts.orchestration extract-company <<< "prose with COMPANY: Deloitte line"
    python -m scripts.orchestration is-failure <<< "FAILURE: reason"       (exit 0 = yes)
    python -m scripts.orchestration append-history <cwd> <json-line>
    python -m scripts.orchestration first-run-setup <cwd>
    python -m scripts.orchestration company-slug "Deloitte Consulting LLC"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import chardet

# Ensure sibling modules (e.g., github_mine, prompts) import cleanly whether
# this file is run as a script (`python scripts/orchestration.py`) or imported
# as a module (`from scripts import orchestration`). When run as a script,
# Python puts this file's directory on sys.path, so `import github_mine`
# works. When imported as a module (in tests), both the package and the
# scripts dir are on sys.path. The explicit insert below makes the
# script-invocation path bulletproof regardless of caller context.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Import the prompt registry eagerly so the CLI's --kind choices can be
# populated. The prompts module has no heavy deps (just stdlib + dataclasses),
# so the import cost is negligible.
from prompts import (
    PROMPT_KINDS as _PROMPT_KINDS,
    build_prompt as _build_prompt,
    format_contact_info as _format_contact_info,
)


# ---------------------------------------------------------------------------
# 1. parse_job_source: file-path | URL | literal precedence
# ---------------------------------------------------------------------------

JobSourceMode = str  # "file" | "url" | "literal"


@dataclass
class JobSource:
    mode: JobSourceMode
    content: str       # file contents, URL string, or literal text
    path: Optional[str] = None  # set when mode == "file"


def parse_job_source(arg: str, cwd: Optional[Path] = None) -> JobSource:
    """
    Resolve <job-source> to (mode, content). Precedence:
      1. If arg refers to an existing file path -> mode=file, content=file text
      2. Else if arg starts with http:// or https:// -> mode=url, content=arg
      3. Else -> mode=literal, content=arg

    Why the file-first check matters: a user could name a file "https.md". The
    file-existence check wins over URL-lookalike strings.
    """
    cwd = cwd or Path.cwd()
    candidate = cwd / arg if not os.path.isabs(arg) else Path(arg)
    if candidate.exists() and candidate.is_file():
        text = _read_text_with_encoding_detection(candidate)
        return JobSource(mode="file", content=text, path=str(candidate))

    if arg.lower().startswith(("http://", "https://")):
        return JobSource(mode="url", content=arg)

    return JobSource(mode="literal", content=arg)


def format_jd(mode: str, content: str, url: Optional[str] = None) -> str:
    """
    Format the JD text that gets written to $RUN_DIR/jd.txt and subsequently
    copied to $OUT_DIR/jd.md in Phase 3.

    For mode="url", prepend a `Source URL: <url>` header (followed by a blank
    line) so the posting URL survives alongside the fetched page text. A
    recruiter follow-up weeks after the application, or a re-read of the exact
    posting phrasing, both need the URL, and the fetched page text alone drops
    it (the URL is metadata, not content).

    For mode="file" or mode="literal", return content unchanged — there's no
    URL to preserve (file mode: the student already has the file; literal
    mode: the JD was pasted inline).

    If mode="url" is passed but url is None/empty, return content unchanged as
    a defensive fallback — we'd rather ship an un-headered file than crash.
    """
    if mode == "url" and url:
        return f"Source URL: {url}\n\n{content}"
    return content


# ---------------------------------------------------------------------------
# 2. discover_resume: canonical filenames in priority order
# ---------------------------------------------------------------------------

# Markdown is preferred (source-of-truth, easier to diff), then PDF.
# If a student has both resume.md and resume.pdf, the .md wins — most
# students keep the .md as their working copy and export the PDF from it.
RESUME_CANDIDATES = [
    "resume.md", "resume.markdown", "cv.md", "CV.md",
    "resume.pdf", "Resume.pdf", "cv.pdf", "CV.pdf",
]


def discover_resume(cwd: Path) -> Optional[Path]:
    """Return the highest-priority resume-like file at the CWD root, or None.

    Enumerates the directory and matches on lowercased names, so the returned
    Path carries the real on-disk filename even on case-insensitive filesystems
    (macOS APFS, Windows NTFS) — probing `(cwd / "cv.pdf").exists()` there
    matches a file named `CV.pdf` but returns a Path whose `.name` is the
    candidate string, not what's on disk. See issue #27.
    """
    priority = {name.lower(): i for i, name in enumerate(RESUME_CANDIDATES)}
    matches = [
        p for p in cwd.iterdir()
        if p.is_file() and p.name.lower() in priority
    ]
    if not matches:
        return None
    matches.sort(key=lambda p: priority[p.name.lower()])
    return matches[0]


ACCEPTED_RESUME_EXTENSIONS = frozenset({".md", ".markdown", ".pdf"})


def validate_resume_path(cwd: Path, filename: str) -> tuple[Optional[Path], Optional[str]]:
    """
    Validate a student-provided resume filename and return (abs_path, None) if
    acceptable, or (None, error_message) otherwise.

    Used as the fallback when `discover_resume` returns None (e.g., the
    student's file is named `Lebenslauf.md`, `履歴書.md`, `my_resume_v3.md`,
    or anything else not in RESUME_CANDIDATES). The SKILL.md orchestrator
    asks the student "what's the filename?" via the cross-host question tool
    and feeds the response through this validator.

    Accepts:
    - A relative path (resolved against `cwd`).
    - An absolute path (used as-is).
    - Any Unicode filename including CJK characters, spaces, and hyphens.
    - Extensions .md / .markdown / .pdf (case-insensitive).

    Rejects:
    - Files that don't exist.
    - Files with unsupported extensions (.docx, .txt, .rtf, etc).
    - Directories (even if the name looks like a resume).
    - Paths the current process can't read.
    """
    if not filename or not filename.strip():
        return None, "filename is empty"

    filename = filename.strip()
    # Accept both relative-to-cwd and absolute paths.
    candidate = Path(filename) if Path(filename).is_absolute() else (cwd / filename)

    # Resolve symlinks + ".." segments; works even if the file doesn't exist yet.
    # (Path.resolve(strict=False) was added in 3.6; we target 3.10+.)
    try:
        candidate = candidate.resolve()
    except (OSError, RuntimeError):
        return None, f"could not resolve path: {filename}"

    if not candidate.exists():
        return None, f"file does not exist: {candidate}"
    if not candidate.is_file():
        return None, f"not a regular file (directory or special): {candidate}"

    ext = candidate.suffix.lower()
    if ext not in ACCEPTED_RESUME_EXTENSIONS:
        accepted = ", ".join(sorted(ACCEPTED_RESUME_EXTENSIONS))
        return None, (
            f"unsupported extension {ext or '(none)'}: {candidate}. "
            f"Accepted: {accepted}"
        )

    try:
        # Probe readability without loading the whole file.
        with candidate.open("rb") as f:
            f.read(1)
    except OSError as exc:
        return None, f"file is not readable: {candidate} ({exc})"

    return candidate, None


# ---------------------------------------------------------------------------
# 2.5 cleanup_stray_outputs: defense-in-depth for misbehaving sub-agents
# ---------------------------------------------------------------------------
#
# Background: weaker models (observed on Haiku 4.5 in issue #29, NOT on Opus
# 4.7) sometimes ignore the interview-coach prompt's "do not write to disk"
# constraint and use the Write tool to create a markdown file with a
# fabricated name (e.g. "Ana_Muller_Interview_Prep_Bundle.md") directly in
# $STUDENT_CWD. Functionally the content is correct, but it pollutes the
# student's working directory — a hard contract violation.
#
# This scan is the belt; the prompt surgery and SKILL.md Phase 6 wording are
# the suspenders. Even if a future model regresses against the prompt, the
# scan removes the rogue file before the student sees it.
#
# Anti-footgun rules:
#   - Top-level only (never recursive). Students may have legitimate
#     interview-prep notes in subdirectories.
#   - mtime gate: only files newer than the dispatch timestamp are
#     candidates. Pre-existing student files are never touched.
#   - Name match: the file's basename must contain "interview", "prep", or
#     "bundle" (case-insensitive substring). These are the shapes the issue
#     observed. Generic names like "notes.md" are never touched.
#   - Protected names: documented input/output filenames are never touched
#     even if their name matches the heuristic.

INTERVIEW_PREP_NAME_PATTERNS = ("interview", "prep", "bundle")

PROTECTED_NAMES_LOWER = frozenset(
    name.lower()
    for name in (
        # Documented INPUT names — the student owns these. The cleanup scan
        # must never touch them even if their name matches the heuristic.
        # OUTPUT names (interview-prep.md, cover-letter.md) deliberately are
        # NOT in this set: if a misbehaving sub-agent writes an output-named
        # file in cwd, it IS the rogue and should be cleaned up. The mtime
        # gate is what protects pre-existing student files of any name.
        *RESUME_CANDIDATES,
        "jd.md",
        "jd.markdown",
    )
)


@dataclass(frozen=True)
class CleanupAction:
    path: Path  # absolute path to the rogue file (pre-action)
    action: str  # "moved" | "deleted" | "skipped"
    reason: str  # human-readable explanation
    destination: Optional[Path] = None  # only set when action == "moved"


def cleanup_stray_outputs(
    cwd: Path,
    out_dir: Path,
    since_timestamp: float,
) -> list[CleanupAction]:
    """Scan `cwd` top-level for rogue interview-prep files and clean up.

    For each markdown file in `cwd` (top level, not recursive) whose name
    matches an interview-prep pattern AND whose mtime is newer than
    `since_timestamp` AND whose name is not in PROTECTED_NAMES_LOWER:
      - If `out_dir/interview-prep.md` is missing or empty, MOVE the rogue
        file there (best-effort recovery — the orchestrator's own write
        didn't happen, but at least the rogue's content survives).
      - Otherwise, DELETE the rogue file (orchestrator already wrote the
        right thing; the rogue is just pollution).

    Never raises on a single bad file — records a `skipped` action and moves on.
    Returns the full list of CleanupAction records describing what happened.
    """
    actions: list[CleanupAction] = []
    if not cwd.exists() or not cwd.is_dir():
        return actions

    target = out_dir / "interview-prep.md"

    try:
        entries = list(cwd.iterdir())
    except OSError:
        return actions

    for entry in entries:
        try:
            if not entry.is_file():
                continue
            if entry.suffix.lower() != ".md":
                continue
            name_lower = entry.name.lower()
            if name_lower in PROTECTED_NAMES_LOWER:
                continue
            if not any(pat in name_lower for pat in INTERVIEW_PREP_NAME_PATTERNS):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime <= since_timestamp:
                continue
        except OSError:
            continue

        # Decide MOVE vs DELETE.
        target_exists_with_content = (
            target.exists() and target.is_file() and target.stat().st_size > 0
        )
        if not target_exists_with_content:
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                entry.replace(target)
                actions.append(
                    CleanupAction(
                        path=entry,
                        action="moved",
                        reason=(
                            "canonical interview-prep.md was missing or empty; "
                            "recovered rogue file's content"
                        ),
                        destination=target,
                    )
                )
            except OSError as exc:
                actions.append(
                    CleanupAction(
                        path=entry,
                        action="skipped",
                        reason=f"move failed: {exc}",
                    )
                )
        else:
            try:
                entry.unlink()
                actions.append(
                    CleanupAction(
                        path=entry,
                        action="deleted",
                        reason=(
                            "canonical interview-prep.md already exists; "
                            "rogue file is pollution"
                        ),
                    )
                )
            except OSError as exc:
                actions.append(
                    CleanupAction(
                        path=entry,
                        action="skipped",
                        reason=f"delete failed: {exc}",
                    )
                )

    return actions


# ---------------------------------------------------------------------------
# 3. read file with encoding detection (chardet fallback to utf-8-sig / utf-8)
# ---------------------------------------------------------------------------


def _read_text_with_encoding_detection(path: Path) -> str:
    """
    Read `path` as text, detecting encoding when UTF-8 decode fails.

    Handles the Windows-Notepad-UTF-16-BOM footgun called out in the eng review.
    Strategy: try UTF-8 first (fast path), then chardet, then let chardet's
    guess fail loudly if the file really is unreadable.
    """
    raw = path.read_bytes()

    # Fast path: UTF-8 (handles UTF-8, UTF-8-BOM via utf-8-sig retry).
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass

    # Chardet path.
    detection = chardet.detect(raw)
    encoding = detection.get("encoding") or "latin-1"
    confidence = detection.get("confidence") or 0.0
    if confidence < 0.5:
        raise UnicodeDecodeError(
            encoding, raw, 0, 1,
            f"Could not reliably detect encoding of {path} "
            f"(best guess: {encoding} with {confidence:.0%} confidence). "
            f"Please resave the file as UTF-8."
        )
    return raw.decode(encoding)


def read_resume(path: Path) -> str:
    """
    Read a resume file as text. Handles markdown (with encoding detection)
    and PDF (via pdfminer.six text extraction).

    Raises a clear error if the PDF appears to be image-only (scanned resume
    with no extractable text). In that case the student needs to either OCR
    it themselves or retype the content into a resume.md.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _read_resume_pdf(path)
    return _read_text_with_encoding_detection(path)


def _read_resume_pdf(path: Path) -> str:
    """
    Extract selectable text from a PDF resume. pdfminer returns text in
    approximate reading order which is usually good enough for the tailor
    sub-agent to restructure into the markdown schema.
    """
    try:
        from pdfminer.high_level import extract_text
    except ImportError as exc:
        raise RuntimeError(
            "pdfminer.six is required to read PDF resumes but is not installed. "
            "Run install.sh inside the skill directory to set up the venv."
        ) from exc

    try:
        text = extract_text(str(path)) or ""
    except Exception as exc:
        raise RuntimeError(
            f"Failed to extract text from {path}: {exc}. "
            f"The PDF may be corrupted or encrypted."
        ) from exc

    # Heuristic: fewer than 50 non-whitespace characters means the PDF is
    # almost certainly image-based (a scanned resume). pdfminer cannot do OCR.
    stripped = "".join(text.split())
    if len(stripped) < 50:
        raise RuntimeError(
            f"{path} appears to be an image-based (scanned) PDF — only "
            f"{len(stripped)} characters of selectable text were extracted. "
            f"resumasher cannot OCR scanned PDFs. Options: "
            f"(1) export a text-based PDF from your source document, "
            f"(2) run OCR yourself (e.g., `ocrmypdf`) and retry, or "
            f"(3) create a resume.md in the same folder and resumasher will use that instead."
        )

    return text


# ---------------------------------------------------------------------------
# 4. folder_state_hash: sha256 of sorted (relpath, mtime_ns, size) tuples
# ---------------------------------------------------------------------------

DEFAULT_IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".resumasher",
    ".pytest_cache",
    ".ipynb_checkpoints",
    "applications",
    ".DS_Store",
    # Critical: when resumasher is installed project-scope at
    # <project>/.claude/skills/resumasher/ (or .codex, .gemini), the folder
    # miner would otherwise walk its own source tree + GOLDEN_FIXTURES and
    # present them to the fit-analyst as the student's evidence. These dirs
    # hold AI CLI skills/agents/settings — never resume evidence.
    ".claude",
    ".codex",
    ".gemini",
    ".agents",
}


def _iter_files(cwd: Path, ignore_dirs: Iterable[str]) -> Iterable[Path]:
    ignore = set(ignore_dirs)
    for root, dirs, files in os.walk(cwd):
        # Prune ignored directories in-place so os.walk doesn't descend into them.
        dirs[:] = [d for d in dirs if d not in ignore]
        for name in files:
            if name in ignore:
                continue
            yield Path(root) / name


def folder_state_hash(cwd: Path, ignore_dirs: Optional[Iterable[str]] = None) -> str:
    """
    Hash the folder by (relpath, mtime_ns, size) tuples. Any touch, move, or
    resize invalidates the cache.

    Ignored: .git, .venv, node_modules, __pycache__, .resumasher, applications
    and any entry explicitly listed in ignore_dirs.
    """
    ignore = set(ignore_dirs or DEFAULT_IGNORE_DIRS)
    triples: list[tuple[str, int, int]] = []
    for p in _iter_files(cwd, ignore):
        try:
            st = p.stat()
        except OSError:
            continue
        rel = p.relative_to(cwd).as_posix()
        triples.append((rel, st.st_mtime_ns, st.st_size))
    triples.sort()
    h = hashlib.sha256()
    for rel, mtime, size in triples:
        h.update(f"{rel}|{mtime}|{size}\n".encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 5. mine_folder_context: build the context block handed to the LLM miner
# ---------------------------------------------------------------------------

TEXT_EXTENSIONS = {".md", ".markdown", ".py", ".sql", ".r", ".rmd", ".txt", ".rst"}
PDF_EXTENSIONS = {".pdf"}
NOTEBOOK_EXTENSIONS = {".ipynb"}
SKIP_EXTENSIONS = {
    ".csv", ".parquet", ".pkl", ".pt", ".h5", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".mp4", ".mov", ".avi",
    ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
    ".xlsx", ".xls", ".doc", ".docx",
}

MAX_FILE_CHARS = 50_000   # 50KB cap, matches design doc
MAX_CONTEXT_CHARS = 80_000  # hard ceiling on total miner context


def _classify(path: Path) -> str:
    """Return one of: 'text', 'pdf', 'notebook', 'readme', 'skip'."""
    # README files are always included regardless of extension.
    if path.name.lower().startswith("readme"):
        return "readme"
    suffix = path.suffix.lower()
    if suffix in NOTEBOOK_EXTENSIONS:
        return "notebook"
    if suffix in PDF_EXTENSIONS:
        return "pdf"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    if suffix in SKIP_EXTENSIONS:
        return "skip"
    return "skip"


def _extract_pdf_text(path: Path, max_chars: int = MAX_FILE_CHARS) -> str:
    try:
        from pdfminer.high_level import extract_text
    except ImportError:
        return f"[PDF: pdfminer.six not installed, cannot extract {path.name}]"
    try:
        text = extract_text(str(path)) or ""
    except Exception as e:
        return f"[PDF extract failed for {path.name}: {e}]"
    if len(text) > max_chars:
        return text[:max_chars] + f"\n[...truncated at {max_chars} chars]"
    return text


def _extract_notebook_text(path: Path, max_chars: int = MAX_FILE_CHARS) -> str:
    """Try nbconvert first; fall back to a lightweight JSON parser."""
    try:
        from nbconvert import MarkdownExporter
        exporter = MarkdownExporter()
        body, _ = exporter.from_filename(str(path))
        if len(body) > max_chars:
            return body[:max_chars] + f"\n[...truncated at {max_chars} chars]"
        return body
    except Exception:
        # Fallback: pull `source` fields from code + markdown cells only.
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            return f"[notebook parse failed for {path.name}: {e}]"
        parts: list[str] = []
        for cell in data.get("cells", []):
            cell_type = cell.get("cell_type")
            if cell_type not in {"code", "markdown"}:
                continue
            source = cell.get("source", "")
            if isinstance(source, list):
                source = "".join(source)
            if cell_type == "code":
                parts.append("```python\n" + source.rstrip() + "\n```")
            else:
                parts.append(source.rstrip())
        body = "\n\n".join(parts)
        if len(body) > max_chars:
            return body[:max_chars] + f"\n[...truncated at {max_chars} chars]"
        return body


def _extract_plain_text(path: Path, max_chars: int = MAX_FILE_CHARS) -> str:
    try:
        text = _read_text_with_encoding_detection(path)
    except Exception as e:
        return f"[read failed for {path.name}: {e}]"
    if len(text) > max_chars:
        return text[:max_chars] + f"\n[...truncated at {max_chars} chars]"
    return text


def mine_folder_context(
    cwd: Path,
    ignore_dirs: Optional[Iterable[str]] = None,
    max_context_chars: int = MAX_CONTEXT_CHARS,
) -> str:
    """
    Walk `cwd`, extract text from allowed files, return a single prose context
    block the folder-miner sub-agent will consume.

    Layout:
        === FILE: <relpath> (<size> bytes) ===
        <content>

    Files are ordered by relpath for determinism. If total context exceeds
    max_context_chars, later files are replaced with a "[skipped: N files,
    budget exhausted]" summary so the miner at least knows they exist.
    """
    ignore = set(ignore_dirs or DEFAULT_IGNORE_DIRS)
    entries: list[tuple[str, int, str]] = []  # (relpath, size, content)
    deferred: list[str] = []

    total = 0
    # Collect all first so we can order deterministically.
    files = sorted(_iter_files(cwd, ignore), key=lambda p: p.relative_to(cwd).as_posix())

    for path in files:
        rel = path.relative_to(cwd).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            continue
        kind = _classify(path)
        if kind == "skip":
            continue

        if total > max_context_chars:
            deferred.append(rel)
            continue

        if kind == "pdf":
            content = _extract_pdf_text(path)
        elif kind == "notebook":
            content = _extract_notebook_text(path)
        else:  # text, readme
            content = _extract_plain_text(path)

        entries.append((rel, size, content))
        total += len(content) + 120  # overhead estimate for header

    chunks: list[str] = []
    for rel, size, content in entries:
        chunks.append(f"=== FILE: {rel} ({size} bytes) ===\n{content.rstrip()}")

    if deferred:
        chunks.append(
            f"=== DEFERRED: {len(deferred)} files skipped due to context budget ===\n"
            + "\n".join(deferred[:50])
        )

    return "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# 6. extract_fit_score / extract_company from prose
# ---------------------------------------------------------------------------

_FIT_SCORE_RE = re.compile(r"FIT_SCORE:\s*(-?\d{1,2})\b", re.IGNORECASE)
_COMPANY_RE = re.compile(r"^\s*COMPANY:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_FAILURE_RE = re.compile(r"^\s*FAILURE:\s*.+", re.IGNORECASE)
_ROLE_RE = re.compile(r"^\s*ROLE:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_SENIORITY_RE = re.compile(r"^\s*SENIORITY:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_STRENGTHS_COUNT_RE = re.compile(r"^\s*STRENGTHS_COUNT:\s*(\d{1,3})\b", re.MULTILINE | re.IGNORECASE)
_GAPS_COUNT_RE = re.compile(r"^\s*GAPS_COUNT:\s*(\d{1,3})\b", re.MULTILINE | re.IGNORECASE)
_RECOMMENDATION_RE = re.compile(r"^\s*RECOMMENDATION:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)

VALID_SENIORITY = frozenset({
    "intern", "junior", "mid", "senior", "staff",
    "manager", "director", "vp", "cxo", "unknown",
})

VALID_RECOMMENDATION = frozenset({"yes", "yes_with_caveats", "no"})


def extract_fit_score(prose: str) -> Optional[int]:
    """Return the integer from a FIT_SCORE: N line in 0..10, else None."""
    match = _FIT_SCORE_RE.search(prose)
    if not match:
        return None
    try:
        score = int(match.group(1))
    except (ValueError, TypeError):
        return None
    if score < 0 or score > 10:
        return None
    return score


def extract_company(prose: str) -> Optional[str]:
    """Return the COMPANY: value, stripped. 'UNKNOWN' -> None."""
    match = _COMPANY_RE.search(prose)
    if not match:
        return None
    value = match.group(1).strip()
    if value.upper() == "UNKNOWN" or not value:
        return None
    return value


def is_failure_sentinel(prose: str) -> bool:
    """True if the first non-blank line starts with 'FAILURE:'."""
    for line in prose.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return bool(_FAILURE_RE.match(stripped))
    return False


def extract_role(prose: str) -> Optional[str]:
    """Return the ROLE: value, stripped. Blank / UNKNOWN / missing -> None."""
    match = _ROLE_RE.search(prose)
    if not match:
        return None
    value = match.group(1).strip()
    if not value or value.upper() == "UNKNOWN":
        return None
    return value


def extract_seniority(prose: str) -> Optional[str]:
    """Return the SENIORITY: value as a lowercase enum in VALID_SENIORITY.

    The fit-analyst prompt guides the LLM to classify in ANY language (German
    'Leitender Entwickler' -> senior, Japanese シニア -> senior, etc). Here
    we only validate the emitted value against the enum whitelist. Unknown
    -> None (so callers can distinguish "explicitly unknown" from "missing",
    which matters for telemetry: we want to know when classification failed).
    """
    match = _SENIORITY_RE.search(prose)
    if not match:
        return None
    value = match.group(1).strip().lower()
    if value not in VALID_SENIORITY:
        return None
    if value == "unknown":
        return None
    return value


def extract_strengths_count(prose: str) -> Optional[int]:
    """Return the integer from STRENGTHS_COUNT: N, else None."""
    match = _STRENGTHS_COUNT_RE.search(prose)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (ValueError, TypeError):
        return None


def extract_gaps_count(prose: str) -> Optional[int]:
    """Return the integer from GAPS_COUNT: N, else None."""
    match = _GAPS_COUNT_RE.search(prose)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (ValueError, TypeError):
        return None


def extract_recommendation(prose: str) -> Optional[str]:
    """Return the RECOMMENDATION: value normalized to one of
    {yes, yes_with_caveats, no} or None.

    Accepts case variations ("Yes", "YES"), space-separated ("yes with
    caveats"), and the hyphenated / underscored forms.
    """
    match = _RECOMMENDATION_RE.search(prose)
    if not match:
        return None
    raw = match.group(1).strip().lower()
    # Normalize punctuation between words to '_'
    normalized = re.sub(r"[-\s]+", "_", raw)
    if normalized in VALID_RECOMMENDATION:
        return normalized
    return None


# ---------------------------------------------------------------------------
# 7. company_slug: safe directory name
# ---------------------------------------------------------------------------


def company_slug(name: str) -> str:
    """Turn 'Deloitte Consulting LLC' into 'deloitte-consulting'.

    Unicode-preserving: 'Müller GmbH' → 'müller' (keeps the umlaut, drops
    the legal suffix). Relies on Python 3's default Unicode \\w for letters.
    """
    if not name or not name.strip():
        return "unknown"
    s = name.strip().lower()
    # Drop common legal-entity suffixes people don't want in directory names.
    s = re.sub(r"\b(gmbh|s\.?a\.?|ag|llc|inc|ltd|corp|plc|co|company)\b\.?", "", s)
    # Collapse any run of non-word chars (including whitespace, punctuation,
    # ampersand, but NOT accented letters) into a single hyphen.
    s = re.sub(r"[^\w]+", "-", s, flags=re.UNICODE)
    s = s.strip("-_")
    return s or "unknown"


# ---------------------------------------------------------------------------
# 8. append_history
# ---------------------------------------------------------------------------


def append_history(cwd: Path, record: dict) -> Path:
    """Append a JSON line to .resumasher/history.jsonl. Creates dir if needed."""
    target = cwd / ".resumasher" / "history.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return target


# ---------------------------------------------------------------------------
# 9. first_run_setup: config.json + gitignore + GDPR note
# ---------------------------------------------------------------------------


GDPR_NOTE = (
    "resumasher stores your contact info and application history LOCALLY in\n"
    ".resumasher/ inside this folder. If this folder is a git repo, we will\n"
    "add .resumasher/ to your .gitignore automatically.\n"
    "\n"
    "Your resume content, job descriptions, and application outputs are never\n"
    "uploaded. At the end of setup you can OPTIONALLY opt into anonymous usage\n"
    "analytics (event types, fit scores, company names, no resume or JD text)\n"
    "to help the maintainer see what's breaking. Default is off. Full detail:\n"
    "PRIVACY.md in the skill directory."
)


# ---------------------------------------------------------------------------
# inspect — JSON introspection for agent-driven debugging.
#
# When a student says "my resume looks wrong," the AI CLI running the
# resumasher skill follows the playbook in SKILL.md's "Debugging this skill"
# section. That playbook calls these `inspect` helpers to get structured
# views of the artifacts: the parsed resume tree, the extracted PDF text,
# the source photo dimensions. Each helper returns JSON so the LLM can read
# it without having to shell-parse the output.
#
# Design: these return DATA, not interpretations. The LLM does the
# hypothesis-forming using docs/KNOWN_FAILURE_MODES.md as a checklist.
# We include a light `warnings` field that surfaces three obvious
# parser-state red flags (empty name, empty contact, orphaned bullets);
# the failure modes doc covers everything else.
# ---------------------------------------------------------------------------


def inspect_resume(path: Path) -> dict:
    """
    Parse a resume markdown file (usually `tailored-resume.md` from an
    application folder, or the student's source `resume.md`) and return a
    JSON-ready snapshot of the parser's internal view.

    The LLM reading this uses it to spot mismatches between what the
    markdown "looks like" and what the parser actually extracted. If the
    markdown visually contains a name on line 1 but `doc.name` comes back
    empty, that tells the agent the parser dropped the contact header.
    Similarly, 0-bullet blocks next to N>0 `raw_bullets` in the same
    section is the signature of orphaned bullets.
    """
    # Imported here to avoid a hard dependency at module-load time — the
    # inspect flow is a debug path, not the hot path. Uses the sibling
    # import shape (not `from scripts.render_pdf`) because line 46 puts
    # `scripts/` on sys.path; the package-qualified shape only works in
    # test context where conftest.py adds the repo root to sys.path.
    from render_pdf import parse_resume_markdown

    text = _read_text_with_encoding_detection(path)
    doc = parse_resume_markdown(text)

    # Light warnings for the three obvious bug signatures. These are
    # shortcuts for the most common failure modes; the full checklist
    # lives in docs/KNOWN_FAILURE_MODES.md for the agent to consult.
    warnings = []
    if not doc.name:
        warnings.append({
            "severity": "critical",
            "code": "EMPTY_NAME",
            "message": (
                "Parser extracted no candidate name. The resume-header parse "
                "expects `# Name` as the first non-empty line. Without it, "
                "the rendered PDF will have no name — an ATS cannot associate "
                "the resume with a candidate. Check line 1 of the markdown."
            ),
        })
    if not doc.contact_line:
        warnings.append({
            "severity": "critical",
            "code": "EMPTY_CONTACT_LINE",
            "message": (
                "Parser extracted no contact line. Expected on line 2 after "
                "the `# Name` header, with email/phone/linkedin/location "
                "separated by ` | `."
            ),
        })

    sections_json = []
    for section in doc.sections:
        block_bullet_counts = [len(b.bullets) for b in section.blocks]
        sub_block_counts = [len(b.sub_blocks) for b in section.blocks]
        section_raw_bullets = len(section.raw_bullets)

        # Orphaned bullets shows up in two shapes depending on how the
        # markdown was structured:
        #
        # Shape A (parser saw blocks but didn't attach bullets):
        #   section.blocks is non-empty, every block has 0 bullets, and
        #   raw_bullets > 0. Rare — would require `###` headings with
        #   no content under them.
        #
        # Shape B (parser didn't create blocks at all — the #19 case):
        #   section.blocks is empty, raw_paragraphs contains `**Title**`-
        #   looking lines that a reader would naturally read as sub-block
        #   headings, and raw_bullets > 0. This is the "**Title** directly
        #   under ##" shape the tailor produces when it skips the `###`
        #   wrapper entirely.
        title_like_paragraphs = [
            p for p in section.raw_paragraphs
            if p.startswith("**") and p.count("**") >= 2
        ]
        shape_a = (
            bool(section.blocks)
            and section_raw_bullets > 0
            and all(c == 0 for c in block_bullet_counts)
            and all(s == 0 for s in sub_block_counts)
        )
        shape_b = (
            not section.blocks
            and section_raw_bullets > 0
            and bool(title_like_paragraphs)
        )
        if shape_a or shape_b:
            shape = "A" if shape_a else "B"
            warnings.append({
                "severity": "critical",
                "code": "ORPHANED_BULLETS",
                "section": section.heading,
                "shape": shape,
                "message": (
                    f"Section '{section.heading}' has {section_raw_bullets} "
                    f"section-level bullets that look like they should be "
                    f"attached to {max(len(section.blocks), len(title_like_paragraphs))} "
                    f"sub-block titles but aren't. Classic '**Title** directly "
                    f"under ##' shape (shape {shape}); the parser emitted "
                    f"the titles as paragraphs and the bullets as loose "
                    f"section-level items, so the PDF shows all titles first "
                    f"then a flat bullet list. See KNOWN_FAILURE_MODES.md #2."
                ),
            })

        # Previews help the agent see the actual shape without re-reading
        # the file. Truncate long strings to keep JSON readable.
        def _preview(s: str, n: int = 120) -> str:
            s = s.replace("\n", " ").strip()
            return s if len(s) <= n else s[:n] + "…"

        sections_json.append({
            "heading": section.heading,
            "block_count": len(section.blocks),
            "raw_bullet_count": section_raw_bullets,
            "raw_paragraph_count": len(section.raw_paragraphs),
            "block_titles": [b.title for b in section.blocks],
            "block_bullet_counts": block_bullet_counts,
            "block_sub_block_counts": sub_block_counts,
            "raw_paragraph_previews": [_preview(p) for p in section.raw_paragraphs],
            "raw_bullet_previews": [_preview(b) for b in section.raw_bullets],
        })

    # First non-empty line of the raw markdown — useful for EMPTY_NAME
    # diagnosis. If there's no `# Name` H1, this is what the parser saw
    # on line 1 and couldn't interpret as a header.
    first_line_raw = ""
    for line in text.splitlines():
        if line.strip():
            first_line_raw = line.strip()
            break
    has_h1 = first_line_raw.startswith("# ")

    return {
        "path": str(path),
        "name": doc.name,
        "contact_line": doc.contact_line,
        "first_line_raw": first_line_raw,
        "has_h1": has_h1,
        "section_count": len(doc.sections),
        "section_order": [s.heading for s in doc.sections],
        "sections": sections_json,
        "warnings": warnings,
    }


def inspect_pdf(path: Path) -> dict:
    """
    Extract text and basic metadata from a PDF produced by resumasher.
    Used for rendered-vs-source comparisons (did the section order change?
    is the contact header visibly missing?).
    """
    from pdfminer.high_level import extract_text

    size_bytes = path.stat().st_size
    extracted = extract_text(str(path))

    # Detect the order in which section headings appear in the PDF text.
    # Only headings we know resumasher renders — others are ignored to
    # avoid false positives from bullet content that happens to match.
    KNOWN_SECTION_HEADINGS = [
        "Summary", "Experience", "Work Experience", "Research Experience",
        "Education", "Skills", "Projects", "Languages", "Certifications",
        "Publications", "Awards", "Volunteering",
    ]
    observed = []
    for heading in KNOWN_SECTION_HEADINGS:
        idx = extracted.find(heading)
        if idx != -1:
            observed.append((idx, heading))
    observed.sort()
    section_order_in_text = [h for _, h in observed]

    return {
        "path": str(path),
        "size_bytes": size_bytes,
        "extracted_char_count": len(extracted),
        "extracted_text": extracted,
        "section_order_in_text": section_order_in_text,
    }


def inspect_photo(path: Path) -> dict:
    """
    Return source photo dimensions so the agent can compare against the
    render box (3cm × 3cm square). An aspect-ratio mismatch means the
    rendered photo will be stretched.
    """
    from PIL import Image as PILImage

    with PILImage.open(path) as img:
        width, height = img.size
        fmt = img.format

    aspect = width / height if height else 0.0
    # Render box as of 2026-04: 3cm × 3cm, square → aspect 1.0.
    render_box_aspect = 1.0
    aspect_delta_pct = abs(aspect - render_box_aspect) / render_box_aspect * 100 if render_box_aspect else 0.0

    warnings = []
    if abs(aspect - render_box_aspect) > 0.05:
        warnings.append({
            "severity": "notice",
            "code": "PHOTO_ASPECT_STRETCH",
            "message": (
                f"Source photo aspect ratio {aspect:.2f} does not match the "
                f"render box (3cm × 3cm, aspect {render_box_aspect:.2f}). "
                f"reportlab's Image flowable stretches source to fill the "
                f"box — the embedded photo will be distorted by ~"
                f"{aspect_delta_pct:.0f}%. See KNOWN_FAILURE_MODES.md #4."
            ),
        })

    return {
        "path": str(path),
        "format": fmt,
        "width": width,
        "height": height,
        "aspect": round(aspect, 3),
        "render_box_aspect": render_box_aspect,
        "aspect_delta_pct": round(aspect_delta_pct, 1),
        "warnings": warnings,
    }


def first_run_needed(cwd: Path) -> bool:
    return not (cwd / ".resumasher" / "config.json").exists()


def write_config(cwd: Path, config: dict) -> Path:
    target = cwd / ".resumasher" / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def read_config(cwd: Path) -> Optional[dict]:
    target = cwd / ".resumasher" / "config.json"
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def ensure_gitignore(cwd: Path) -> Optional[Path]:
    """
    If `cwd` is inside a git repo, append `.resumasher/` to .gitignore if the
    entry isn't already there. Return the path written, or None if no git repo.
    """
    # Look upward for a .git dir to decide if we're in a repo.
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists():
            gitignore = cwd / ".gitignore"
            existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
            if ".resumasher/" in existing or ".resumasher" in existing.split():
                return gitignore
            new_content = existing
            if new_content and not new_content.endswith("\n"):
                new_content += "\n"
            new_content += ".resumasher/\n"
            gitignore.write_text(new_content, encoding="utf-8")
            return gitignore
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    parser = argparse.ArgumentParser(prog="scripts.orchestration")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse-job-source")
    p.add_argument("arg")
    p.add_argument("--cwd", default=".")

    p = sub.add_parser("format-jd")
    p.add_argument("--mode", required=True, choices=["file", "url", "literal"])
    p.add_argument("--url", default=None, help="Source URL (for mode=url; prepended as a header line)")
    p.add_argument(
        "--content-file",
        default="-",
        help="Path to a file containing the JD text, or '-' for stdin (default).",
    )

    p = sub.add_parser("discover-resume")
    p.add_argument("cwd", nargs="?", default=".")

    p = sub.add_parser("validate-resume-path")
    p.add_argument("cwd")
    p.add_argument("filename")

    p = sub.add_parser("folder-state-hash")
    p.add_argument("cwd", nargs="?", default=".")

    p = sub.add_parser("mine-context")
    p.add_argument("cwd", nargs="?", default=".")
    p.add_argument(
        "--github-username",
        default=None,
        help="Also mine this GitHub profile and append its prose to the context",
    )

    p = sub.add_parser("github-mine")
    p.add_argument("username")
    p.add_argument("--cwd", default=".")
    p.add_argument("--cap", type=int, default=15)
    p.add_argument("--no-cache", action="store_true")

    p = sub.add_parser("read-resume")
    p.add_argument("path")

    p = sub.add_parser("extract-fit-score")
    p = sub.add_parser("extract-company")
    p = sub.add_parser("extract-role")
    p = sub.add_parser("extract-seniority")
    p = sub.add_parser("extract-strengths-count")
    p = sub.add_parser("extract-gaps-count")
    p = sub.add_parser("extract-recommendation")

    p = sub.add_parser(
        "extract-fit-fields",
        help=(
            "Read fit-assessment text on stdin, extract all 7 structured "
            "fields (score, company, role, seniority, strengths-count, "
            "gaps-count, recommendation), and write each to its own file "
            "under --output-dir. Replaces the heredoc env-file pattern "
            "that breaks when company / role contain spaces (issue #50)."
        ),
    )
    p.add_argument(
        "--output-dir",
        required=True,
        help=(
            "Directory to write per-field files into. Created if missing. "
            "Files written: score.txt, company.txt, role.txt, seniority.txt, "
            "strengths.txt, gaps.txt, recommendation.txt"
        ),
    )

    p = sub.add_parser("is-failure")

    p = sub.add_parser("append-history")
    p.add_argument("cwd")
    p.add_argument("json_line")

    p = sub.add_parser(
        "inspect",
        help=(
            "Return a JSON snapshot of a resumasher artifact for agent-driven "
            "debugging. Pick exactly one of --resume, --pdf, --photo."
        ),
    )
    inspect_group = p.add_mutually_exclusive_group(required=True)
    inspect_group.add_argument("--resume", help="Path to a resume markdown file")
    inspect_group.add_argument("--pdf", help="Path to a rendered PDF")
    inspect_group.add_argument("--photo", help="Path to the source photo image")

    p = sub.add_parser(
        "cleanup-stray-outputs",
        help=(
            "Defense-in-depth: scan $STUDENT_CWD for rogue interview-prep "
            "files a misbehaving sub-agent may have planted (issue #29). "
            "Files newer than --since-timestamp whose names match interview "
            "patterns are moved to $OUT_DIR/interview-prep.md (if missing) or "
            "deleted (if the canonical file already exists). Emits a JSON "
            "summary of actions on stdout. Always exits 0 — cleanup failures "
            "are logged but never block the orchestrator."
        ),
    )
    p.add_argument("--cwd", required=True, help="Student working directory to scan (top level only)")
    p.add_argument("--out-dir", required=True, help="Path where the canonical interview-prep.md should live")
    p.add_argument(
        "--since-timestamp",
        type=float,
        required=True,
        help="Epoch seconds; only files with mtime newer than this are candidates",
    )

    p = sub.add_parser("first-run-needed")
    p.add_argument("cwd", nargs="?", default=".")

    p = sub.add_parser("ensure-gitignore")
    p.add_argument("cwd", nargs="?", default=".")

    p = sub.add_parser("company-slug")
    p.add_argument("name")

    p = sub.add_parser(
        "build-prompt",
        help=(
            "Build a fully-substituted sub-agent prompt and emit to stdout. "
            "Orchestrators should dispatch sub-agents with the output of this "
            "command instead of substituting {vars} themselves — cross-host "
            "testing showed LLM-side substitution is unreliable."
        ),
    )
    p.add_argument(
        "--kind",
        required=True,
        choices=sorted(_PROMPT_KINDS),
        help="Which sub-agent prompt to build.",
    )
    p.add_argument(
        "--run-dir",
        default=None,
        help=(
            "Path to .resumasher/run/ (contains resume.txt, context.txt, "
            "jd.txt). Defaults to <cwd>/.resumasher/run."
        ),
    )
    p.add_argument(
        "--cwd",
        default=".",
        help=(
            "Student's working directory (contains .resumasher/cache.txt). "
            "Defaults to current directory."
        ),
    )
    p.add_argument(
        "--out-dir",
        default=None,
        help=(
            "Output directory for this application (contains "
            "company-research.md, tailored-resume.md). Required for "
            "cover-letter and interview-coach kinds."
        ),
    )
    p.add_argument(
        "--company",
        default=None,
        help="Company name. Required for company-researcher kind.",
    )

    args = parser.parse_args()

    if args.command == "parse-job-source":
        res = parse_job_source(args.arg, cwd=Path(args.cwd))
        print(json.dumps({"mode": res.mode, "path": res.path, "content": res.content}))
        return 0

    if args.command == "format-jd":
        if args.content_file == "-":
            content = sys.stdin.read()
        else:
            content = Path(args.content_file).read_text(encoding="utf-8")
        sys.stdout.write(format_jd(args.mode, content, args.url))
        return 0

    if args.command == "discover-resume":
        path = discover_resume(Path(args.cwd))
        if path is None:
            print(
                "FAILURE: no resume found. Looked for these filenames in "
                + str(Path(args.cwd).resolve())
                + ": "
                + ", ".join(RESUME_CANDIDATES)
            )
            return 1
        print(str(path))
        return 0

    if args.command == "validate-resume-path":
        path, err = validate_resume_path(Path(args.cwd), args.filename)
        if err is not None:
            print(f"FAILURE: {err}", file=sys.stderr)
            return 1
        print(str(path))
        return 0

    if args.command == "folder-state-hash":
        print(folder_state_hash(Path(args.cwd)))
        return 0

    if args.command == "mine-context":
        cwd = Path(args.cwd)
        folder_prose = mine_folder_context(cwd)
        parts = [folder_prose]
        if args.github_username:
            # Import lazily so folder-only runs don't pay the import cost.
            import github_mine as gm

            def _persist_warning(msg: str) -> None:
                """
                Write the warning to a file so it survives trace rollup in
                non-Claude hosts (Codex truncates long stderr blocks and
                summarizes them into a paraphrased history entry). The file
                is a durable ground-truth record the student can paste into
                a bug report.
                """
                try:
                    run_dir = cwd / ".resumasher" / "run"
                    run_dir.mkdir(parents=True, exist_ok=True)
                    (run_dir / "github-mine-error.txt").write_text(msg, encoding="utf-8")
                except OSError:
                    pass  # best-effort; don't fail the mine just because we can't log

            try:
                github_prose = gm.mine_github(args.github_username, cwd=cwd)
                parts.append(github_prose)
            except gm.RateLimitError as exc:
                msg = (
                    f"=== GITHUB_MINE_WARNING ===\n"
                    f"GitHub rate limit hit; continuing without GitHub evidence.\n"
                    f"Install `gh` and run `gh auth login` for a 5000/hr limit.\n"
                    f"Details: {exc}\n"
                )
                print("\n" + msg, file=sys.stderr)
                _persist_warning(msg)
            except gm.NotFoundError:
                msg = (
                    f"=== GITHUB_MINE_WARNING ===\n"
                    f"GitHub user '{args.github_username}' not found or has "
                    f"no public repos. Continuing without GitHub evidence.\n"
                )
                print("\n" + msg, file=sys.stderr)
                _persist_warning(msg)
            except gm.APIError as exc:
                msg = (
                    f"=== GITHUB_MINE_WARNING ===\n"
                    f"GitHub API error: {exc}\n"
                    f"Continuing without GitHub evidence. "
                    f"Full error written to .resumasher/run/github-mine-error.txt\n"
                )
                print("\n" + msg, file=sys.stderr)
                _persist_warning(msg)
        print("\n\n".join(parts))
        return 0

    if args.command == "github-mine":
        import github_mine as gm
        try:
            prose = gm.mine_github(
                args.username,
                cwd=Path(args.cwd),
                cap=args.cap,
                use_cache=not args.no_cache,
            )
        except gm.RateLimitError as exc:
            print(f"FAILURE: rate limit: {exc}", file=sys.stderr)
            return 2
        except gm.NotFoundError:
            print(f"FAILURE: user '{args.username}' not found", file=sys.stderr)
            return 3
        except gm.APIError as exc:
            print(f"FAILURE: {exc}", file=sys.stderr)
            return 4
        print(prose)
        return 0

    if args.command == "read-resume":
        print(read_resume(Path(args.path)))
        return 0

    if args.command == "extract-fit-score":
        text = sys.stdin.read()
        score = extract_fit_score(text)
        if score is None:
            print("")
            return 1
        print(score)
        return 0

    if args.command == "extract-company":
        text = sys.stdin.read()
        company = extract_company(text)
        if company is None:
            print("")
            return 1
        print(company)
        return 0

    if args.command == "extract-role":
        text = sys.stdin.read()
        role = extract_role(text)
        if role is None:
            print("")
            return 1
        print(role)
        return 0

    if args.command == "extract-seniority":
        text = sys.stdin.read()
        seniority = extract_seniority(text)
        if seniority is None:
            print("")
            return 1
        print(seniority)
        return 0

    if args.command == "extract-strengths-count":
        text = sys.stdin.read()
        n = extract_strengths_count(text)
        if n is None:
            print("")
            return 1
        print(n)
        return 0

    if args.command == "extract-gaps-count":
        text = sys.stdin.read()
        n = extract_gaps_count(text)
        if n is None:
            print("")
            return 1
        print(n)
        return 0

    if args.command == "extract-recommendation":
        text = sys.stdin.read()
        rec = extract_recommendation(text)
        if rec is None:
            print("")
            return 1
        print(rec)
        return 0

    if args.command == "extract-fit-fields":
        # Read the fit-assessment text once, run every extractor, and
        # persist each value to its own file under --output-dir. The
        # per-field-file shape replaces the previous SKILL.md pattern of
        # writing key=value lines to fit-extracted.env and shell-sourcing
        # them in Phase 9 — that pattern breaks when company / role
        # contain spaces (e.g. "Elevation Capital" → bash parses
        # "Capital" as a command). See issue #50.
        text = sys.stdin.read()
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # Each value is written as raw text — `$(cat file)` in the
        # consuming shell strips the trailing newline but preserves all
        # interior chars (spaces, ampersands, single quotes, backticks),
        # so the values round-trip byte-perfect regardless of contents.
        # Missing values render as empty files; the agent decides how to
        # handle "" downstream (the existing UNKNOWN sentinel handling).
        fields = {
            "score.txt": extract_fit_score(text),
            "company.txt": extract_company(text),
            "role.txt": extract_role(text),
            "seniority.txt": extract_seniority(text),
            "strengths.txt": extract_strengths_count(text),
            "gaps.txt": extract_gaps_count(text),
            "recommendation.txt": extract_recommendation(text),
        }
        for filename, value in fields.items():
            target = out_dir / filename
            target.write_text(
                "" if value is None else str(value), encoding="utf-8"
            )
        # Stdout summary so the caller can sanity-check at the bash
        # level without re-cat-ing every file. JSON-ish but flat so
        # there's no shell-eats-JSON repeat of issue #44.
        for key in ("score", "company", "role", "seniority"):
            v = fields[f"{key}.txt"]
            sys.stdout.write(f"{key}={'' if v is None else v}\n")
        return 0

    if args.command == "is-failure":
        text = sys.stdin.read()
        return 0 if is_failure_sentinel(text) else 1

    if args.command == "append-history":
        record = json.loads(args.json_line)
        path = append_history(Path(args.cwd), record)
        print(str(path))
        return 0

    if args.command == "inspect":
        if args.resume:
            result = inspect_resume(Path(args.resume))
        elif args.pdf:
            result = inspect_pdf(Path(args.pdf))
        elif args.photo:
            result = inspect_photo(Path(args.photo))
        else:  # pragma: no cover — argparse mutually_exclusive_group(required=True)
            print("inspect requires --resume, --pdf, or --photo", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "cleanup-stray-outputs":
        actions = cleanup_stray_outputs(
            cwd=Path(args.cwd),
            out_dir=Path(args.out_dir),
            since_timestamp=args.since_timestamp,
        )
        summary = {
            "scanned": str(Path(args.cwd).resolve()),
            "actions": [
                {
                    "path": str(a.path),
                    "action": a.action,
                    "reason": a.reason,
                    "destination": str(a.destination) if a.destination else None,
                }
                for a in actions
            ],
            "moved": sum(1 for a in actions if a.action == "moved"),
            "deleted": sum(1 for a in actions if a.action == "deleted"),
            "skipped": sum(1 for a in actions if a.action == "skipped"),
        }
        print(json.dumps(summary))
        return 0

    if args.command == "first-run-needed":
        needed = first_run_needed(Path(args.cwd))
        print("yes" if needed else "no")
        return 0 if needed else 1

    if args.command == "ensure-gitignore":
        path = ensure_gitignore(Path(args.cwd))
        print(str(path) if path else "")
        return 0

    if args.command == "company-slug":
        print(company_slug(args.name))
        return 0

    if args.command == "build-prompt":
        return _cmd_build_prompt(args)

    return 1


# ---------------------------------------------------------------------------
# build-prompt CLI handler
# ---------------------------------------------------------------------------


def _read_if_exists(path: Path) -> Optional[str]:
    """Read a file's text if it exists; return None otherwise. Never raises."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError):
        return None
    except OSError:
        return None


def _cmd_build_prompt(args: argparse.Namespace) -> int:
    """
    Resolve the variables the requested kind needs by reading files in
    $RUN_DIR / $CWD / $OUT_DIR, then call prompts.build_prompt. Emit the
    fully-substituted prompt to stdout.

    The file paths are conventional:
      - $RUN_DIR/resume.txt   — read-resume output
      - $RUN_DIR/context.txt  — mine-context output (raw folder+github)
      - $RUN_DIR/jd.txt       — JD text extracted from parse-job-source
      - $CWD/.resumasher/cache.txt — folder-miner sub-agent's prose summary
      - $OUT_DIR/company-research.md — company-researcher sub-agent output
      - $OUT_DIR/tailored-resume.md  — tailor sub-agent output

    If a required file is missing, exits 2 with an actionable error message
    naming the file and the phase that was supposed to have produced it.
    """
    cwd = Path(args.cwd).resolve()
    run_dir = Path(args.run_dir).resolve() if args.run_dir else cwd / ".resumasher" / "run"
    out_dir = Path(args.out_dir).resolve() if args.out_dir else None

    spec = _PROMPT_KINDS[args.kind]

    # Assemble the kwargs build_prompt accepts. Only the keys in
    # spec.required_vars will actually be substituted; others are ignored.
    kwargs: dict[str, Optional[str]] = {
        "resume_text": None,
        "folder_context": None,
        "folder_summary": None,
        "jd_text": None,
        "company": None,
        "company_research": None,
        "tailored_resume": None,
    }

    def _missing(var: str, expected_path: Path, produced_by: str) -> int:
        print(
            f"FAILURE: build-prompt --kind {args.kind} requires variable "
            f"{var!r}, expected at {expected_path}. This file is produced "
            f"by {produced_by}. Run that phase first, or pass an explicit "
            f"--run-dir / --out-dir if the file is elsewhere.",
            file=sys.stderr,
        )
        return 2

    for var in spec.required_vars:
        if var == "resume_text":
            content = _read_if_exists(run_dir / "resume.txt")
            if content is None:
                return _missing(var, run_dir / "resume.txt", "orchestration read-resume in Phase 1")
            kwargs[var] = content
        elif var == "folder_context":
            content = _read_if_exists(run_dir / "context.txt")
            if content is None:
                return _missing(var, run_dir / "context.txt", "orchestration mine-context in Phase 2")
            kwargs[var] = content
        elif var == "folder_summary":
            content = _read_if_exists(cwd / ".resumasher" / "cache.txt")
            if content is None:
                return _missing(var, cwd / ".resumasher" / "cache.txt", "the folder-miner sub-agent in Phase 2")
            kwargs[var] = content
        elif var == "jd_text":
            content = _read_if_exists(run_dir / "jd.txt")
            if content is None:
                return _missing(var, run_dir / "jd.txt", "orchestration parse-job-source in Phase 1")
            kwargs[var] = content
        elif var == "company":
            if not args.company:
                print(
                    f"FAILURE: build-prompt --kind {args.kind} requires --company <name>.",
                    file=sys.stderr,
                )
                return 2
            kwargs[var] = args.company
        elif var == "company_research":
            if out_dir is None:
                print(
                    f"FAILURE: build-prompt --kind {args.kind} requires --out-dir <path>.",
                    file=sys.stderr,
                )
                return 2
            content = _read_if_exists(out_dir / "company-research.md")
            if content is None:
                return _missing(var, out_dir / "company-research.md", "the company-researcher sub-agent in Phase 4")
            kwargs[var] = content
        elif var == "tailored_resume":
            if out_dir is None:
                print(
                    f"FAILURE: build-prompt --kind {args.kind} requires --out-dir <path>.",
                    file=sys.stderr,
                )
                return 2
            content = _read_if_exists(out_dir / "tailored-resume.md")
            if content is None:
                return _missing(var, out_dir / "tailored-resume.md", "the tailor sub-agent in Phase 5")
            kwargs[var] = content
        elif var == "contact_info":
            # Read configured contact fields from .resumasher/config.json and
            # format as a pre-built 2-line header the tailor must copy verbatim.
            # This exists because tailor sub-agents on some hosts (observed
            # under Gemini) don't have access to config — they'd otherwise
            # emit [INSERT LINKEDIN URL] placeholders or fall back to the
            # resume's stale location. With contact_info pre-formatted here,
            # the tailor has no ambiguity and no way to drift.
            config_path = cwd / ".resumasher" / "config.json"
            config_text = _read_if_exists(config_path)
            if config_text is None:
                return _missing(
                    var, config_path,
                    "first-run setup in Phase 0 (writes .resumasher/config.json)",
                )
            try:
                config = json.loads(config_text)
            except json.JSONDecodeError as exc:
                print(
                    f"FAILURE: build-prompt --kind {args.kind}: "
                    f"could not parse {config_path}: {exc}",
                    file=sys.stderr,
                )
                return 2
            try:
                kwargs[var] = _format_contact_info(
                    name=config.get("name", ""),
                    email=config.get("email", ""),
                    phone=config.get("phone", ""),
                    linkedin=config.get("linkedin", ""),
                    location=config.get("location", ""),
                )
            except ValueError as exc:
                print(
                    f"FAILURE: build-prompt --kind {args.kind}: {exc}. "
                    f"Fix the 'name' field in {config_path} and re-run.",
                    file=sys.stderr,
                )
                return 2

    prompt = _build_prompt(args.kind, **kwargs)
    sys.stdout.write(prompt)
    # No trailing newline beyond whatever the template ends with; orchestrators
    # pasting this into a sub-agent dispatch don't want spurious whitespace.
    return 0


if __name__ == "__main__":
    # Python on Windows defaults stdout/stderr to the system ANSI code page
    # (typically CP1252) when not attached to a TTY. Prompts and user-facing
    # output contain `→`, `…`, curly quotes, and non-ASCII names that CP1252
    # can't encode, so writes raise UnicodeEncodeError. Force UTF-8 so the
    # Windows Git Bash path behaves like macOS/Linux.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(_cli())
