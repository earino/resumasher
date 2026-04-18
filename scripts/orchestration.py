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
    """Return the first existing resume-like file at the CWD root, or None."""
    for name in RESUME_CANDIDATES:
        p = cwd / name
        if p.exists() and p.is_file():
            return p
    return None


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
    ".resumasher/ inside this folder. Nothing is uploaded. If this folder is a\n"
    "git repo, we will add .resumasher/ to your .gitignore automatically."
)


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

    p = sub.add_parser("discover-resume")
    p.add_argument("cwd", nargs="?", default=".")

    p = sub.add_parser("folder-state-hash")
    p.add_argument("cwd", nargs="?", default=".")

    p = sub.add_parser("mine-context")
    p.add_argument("cwd", nargs="?", default=".")

    p = sub.add_parser("read-resume")
    p.add_argument("path")

    p = sub.add_parser("extract-fit-score")
    p = sub.add_parser("extract-company")
    p = sub.add_parser("is-failure")

    p = sub.add_parser("append-history")
    p.add_argument("cwd")
    p.add_argument("json_line")

    p = sub.add_parser("first-run-needed")
    p.add_argument("cwd", nargs="?", default=".")

    p = sub.add_parser("ensure-gitignore")
    p.add_argument("cwd", nargs="?", default=".")

    p = sub.add_parser("company-slug")
    p.add_argument("name")

    args = parser.parse_args()

    if args.command == "parse-job-source":
        res = parse_job_source(args.arg, cwd=Path(args.cwd))
        print(json.dumps({"mode": res.mode, "path": res.path, "content": res.content}))
        return 0

    if args.command == "discover-resume":
        path = discover_resume(Path(args.cwd))
        if path is None:
            print("FAILURE: no resume.md / cv.md found")
            return 1
        print(str(path))
        return 0

    if args.command == "folder-state-hash":
        print(folder_state_hash(Path(args.cwd)))
        return 0

    if args.command == "mine-context":
        print(mine_folder_context(Path(args.cwd)))
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

    if args.command == "is-failure":
        text = sys.stdin.read()
        return 0 if is_failure_sentinel(text) else 1

    if args.command == "append-history":
        record = json.loads(args.json_line)
        path = append_history(Path(args.cwd), record)
        print(str(path))
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

    return 1


if __name__ == "__main__":
    sys.exit(_cli())
