# Changelog

All notable changes to resumasher will be captured here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

`Unreleased` covers work in progress on `main`. Tagged versions will appear below it once we start cutting releases.

## [Unreleased]

Nothing queued.

## [0.1.0] — 2026-04-18

First public release. Built in a single design-through-ship session; the CHANGELOG below bundles every meaningful change that landed between the initial commit and the first student-ready state.

### Added

- **Claude Code skill `/resumasher <job-source>`** with a nine-phase pipeline: first-run setup → intake → folder + GitHub mine → fit analysis → company research → tailor → parallel cover-letter + interview-prep → interactive placeholder fill → PDF render → log + summary. `<job-source>` accepts a file path, a URL, or literal pasted JD text.
- **EU + US resume styles** with ATS-safe single-column layout. Style choice persists in `.resumasher/config.json`. `--style` flag overrides per run.
- **Pure-Python PDF renderer** (`scripts/render_pdf.py`) using reportlab. No native deps — `pip install` just works on macOS, Linux, Windows. Bundled DejaVu Sans font handles non-ASCII names (Björn, François, Jiří) without box characters.
- **Multi-role tenure rendering.** When a candidate held multiple titles at one company (Meta → Senior Director → Director → Manager), the PDF shows ONE company entry with sub-role sub-bullets, preserving the promotion narrative.
- **Photo support for EU resumes.** Photos auto-downscaled to 500px max dimension, re-encoded as JPEG q=85 before embedding. Output PDFs stay under 200KB even from 3000×4000 phone-camera source photos.
- **`resume.pdf` accepted directly** when no markdown source exists. pdfminer.six extracts selectable text; image-only scanned PDFs fail with a clear error pointing at OCR or manual markdown as alternatives.
- **GitHub profile mining** during first-run setup. Prefers `gh api` (student's existing auth, 5000/hr limit), falls back to unauthenticated `urllib` (60/hr). Fetches non-fork, non-archived, non-empty repos sorted by most-recently-pushed; caps at 15 by default. Returns prose summaries with stars, topics, last-push date, and README content. 1-hour cache under `.resumasher/github-cache/<username>.json`. All failures non-fatal.
- **Interactive placeholder fill** before PDF render. When the tailor emits `[INSERT TEAM SIZE]` etc. for metrics the evidence didn't supply, Phase 7 prompts the student per-bullet with three options: paste specifics, soften to a no-metric version (pre-computed by the tailor as `<!--SOFT:...-->` alternates), or drop the bullet. No placeholder ever ships to the rendered PDF for resume or cover letter. Interview-prep placeholders stay as-is — those are practice prompts the student prepares before the interview.
- **Fit assessment** as a mandatory gate before tailoring. Honest 0–10 score with strengths and gaps. Doesn't block a low-fit application, but names the gap so the student applies with eyes open.
- **Company research** phase fetches 3–5 recent facts with inline citations, used by the cover-letter sub-agent.
- **`.resumasher/history.jsonl`** local application log. One line per application with timestamp, company, fit score, style, output dir, errors. Local-only, gitignored by default.
- **Golden fixtures** (`GOLDEN_FIXTURES/`) — sample MS Business Analytics portfolio with three projects (capstone, ML final, text mining), a non-ASCII student name (Ana Müller), and a realistic Deloitte Vienna JD. Students can try the skill end-to-end before pointing it at their own data.
- **`bin/resumasher-exec`** self-locating wrapper. Finds its own SKILL_ROOT and execs the venv Python with the right script path. Lets SKILL.md use `"$RS" orchestration <subcommand>` instead of verbose `"$PY" "$SKILL_ROOT/scripts/orchestration.py"` invocations.
- **Install script** (`install.sh`) — creates venv, installs requirements, chmods `bin/` wrappers. Idempotent. Actionable network-failure error message (as of this release) pointing students at retry options instead of a raw Python traceback.
- **`docs/DESIGN.md`** — design rationale extracted from the `/office-hours` and `/plan-eng-review` sessions that produced the skill. Frozen-ish; contributors read it before large PRs to understand why the skill is shaped the way it is.
- **Re-render workflow** documented in SKILL.md. Students who edit `tailored-resume.md` or `cover-letter.md` after a run ask Claude to "re-render the resume PDF" and Claude runs just the render step without re-dispatching sub-agents.

### Security

- **Sub-agent tool permissions tightened.** All six LLM sub-agents (folder-miner, fit-analyst, company-researcher, tailor, cover-letter, interview-coach) have explicit "do NOT use Bash, Read, WebFetch, Write, Edit, Grep, Glob" instructions in their prompts. Only company-researcher retains WebSearch + WebFetch (needed for its job). Defense-in-depth against prompt injection through `<<<UNTRUSTED_JD_*>>>` markers.
- **Prompt-injection markers** wrap JD content and company-research output before they reach any sub-agent. Markers are the primary defense; sub-agent tool restrictions (above) are the belt-and-suspenders layer.
- **`.claude/` directory ignored** by the folder miner. Previously, a project-scope install at `<project>/.claude/skills/resumasher/` had the folder miner walking its own source tree and presenting it to the fit-analyst as student evidence — a major data-contamination bug. Fixed via `DEFAULT_IGNORE_DIRS`.
- **`.gstack/` added to .gitignore** so security reports from `/cso` don't get accidentally committed to the public repo.
- **No secrets, PATs, or PII in the repo or its git history.** Verified via `git log -p --all` scan for known secret prefixes (ghp_, sk-, AKIA, etc.) at release time.

### Fixed

- **`ModuleNotFoundError: No module named 'scripts'`** when running `orchestration.py` directly from the CLI (not via `python -m`). Caused by `from scripts import github_mine` requiring the package path to be on `sys.path`. Fixed by inserting the script's own dir into `sys.path` at module load, and switching to sibling-style `import github_mine as gm`. Subprocess-level regression test added.
- **Shell state non-persistence between Bash tool calls.** Earlier SKILL.md assumed `SKILL_ROOT` and `PY` variables persisted across Bash tool invocations; they don't. Every invocation now starts with a path-resolution prologue that re-discovers SKILL_ROOT and errors clearly (pointing at `install.sh`) if the venv is missing.
- **`AskUserQuestion` with 1 real option crashed** with `InputValidationError: Too small: expected array to have >=2 items`. First-run-setup free-text fields (phone, location, photo path, GitHub) now use 2-option patterns with Other for the real input.
- **Markdown bold (`**text**`) rendered as literal asterisks** in PDFs. Renderer now converts `**x**` → `<b>x</b>` via reportlab's HTML subset before Paragraph parsing. Lone single asterisks (e.g., "v1.0*") are preserved, not false-matched as italic.
- **`/tmp/resumasher-*.txt` files polluted global `/tmp`** across runs and across users on shared machines, leaking student resume content. Intermediates moved to `$STUDENT_CWD/.resumasher/run/`, wiped at the start of each run, gitignored by default.
- **1MB resume PDFs** when a student's photo was a 3000×4000 phone-camera export. Photos downscaled to ≤500px, re-encoded as JPEG q=85. Output PDFs now ~150KB.
- **`install.sh` rejected project-scope installs** when run with a relative path arg due to relative-vs-absolute path comparison. Install.sh no longer accepts a target arg at all; it runs in place of wherever it was cloned.
- **First-run setup asked the same free-text value across TWO rounds** — one to say "I'll provide it," another to actually collect the value. Now one round: Skip / Use default / Other-field-for-the-real-value. Setup dropped from 3 rounds to 1-2 for most students.
- **Removed gstack-required notices and hooks** that were inherited from the development environment. Students don't need gstack to use resumasher.

### Deferred (tracked in TODOS.md)

- Dependency pinning + hash-verified lockfile. `requirements.txt` currently uses `>=`, which is a supply-chain risk. Will land with `pip-compile --generate-hashes` in a follow-up.
- Path-traversal containment in `parse_job_source`. Defense-in-depth against a prompt-injection chain reading sensitive files. Tracked with exact code snippet in TODOS.md.

### v0.2 — planned

- `--review` mode: step-by-step interactive rewriting for every bullet, not just placeholders. Pedagogy-first alternative to the default one-shot flow.
- GitHub Actions CI with automated PDF round-trip on every push.
- Incremental folder-mine cache invalidation (currently full re-mine on any file change).
- German / French JD translation pre-pass.
- Facts persistence: remember placeholder-fill answers across runs so the second application to a similar role doesn't re-ask the same `[INSERT TEAM SIZE]` questions.
