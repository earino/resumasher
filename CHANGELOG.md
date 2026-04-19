# Changelog

All notable changes to resumasher will be captured here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

`Unreleased` covers work in progress on `main`. Tagged versions will appear below it once we start cutting releases.

## [Unreleased]

### Added

- **Non-English resume filenames** ([#3](https://github.com/earino/resumasher/issues/3)). A student whose resume is saved as `Lebenslauf.md`, `curriculum.md`, `cv_francais.md`, `履歴書.md`, `简历.md`, or `my_resume_final_v3.md` no longer hits "FAILURE: no resume found" as a terminal error. When `discover-resume` misses, the skill falls through to asking the student "what's the filename?" via the cross-host question tool and validates the answer with a new `validate-resume-path` subcommand. Handles CJK characters, spaces, absolute paths, and subdirectory paths. Up to 3 re-ask attempts before giving up. No filename-list expansion, no directory scan, no LLM classification — one prompt.

## [0.2.0] — 2026-04-19

### Added

- **Opt-in usage analytics** ([#2](https://github.com/earino/resumasher/issues/2)). Three-tier consent (off / anonymous / community), default off, active opt-in only. Students can change anytime via `resumasher telemetry set-tier <off|anonymous|community>`. Backend is Supabase in the Ireland region (eu-west-1), free tier. Nothing sent unless the student opts in during first-run setup.
- **Eight instrumented pipeline events**: `first_run_setup_completed`, `run_started`, `fit_computed`, `tailor_completed`, `placeholder_fill_choice`, `run_completed`, `run_failed`, `rerender_used`. Events correlate via a per-run UUID so the maintainer can see "this run hit X then Y then failed at Z" instead of orphan signals. Mid-run events write to local JSONL only; terminal events batch-flush in a single HTTP round-trip (~500ms at end of run, imperceptible inside a multi-minute pipeline).
- **Right-to-access and right-to-erasure CLI.** `resumasher telemetry export` dumps the local JSONL to stdout so students can see exactly what's been logged. `resumasher telemetry delete` POSTs the right-to-erasure endpoint AND wipes local state (JSONL, cursor, installation ID). Returns a count so the student can verify.
- **GDPR-compliant `PRIVACY.md`** at repo root. Lists every field that gets sent and every field that does NOT get sent (no resume content, no JD text, no names, no GitHub usernames, no email addresses). Explicit on retention (90-day events, 180-day installations), data region (EU/Ireland), and data controller. Sensitive-employer guidance.
- **Fit-analyst emits structured sentinels.** In addition to the existing `FIT_SCORE:` and `COMPANY:`, the fit-analyst prompt now emits `ROLE:`, `SENIORITY:`, `STRENGTHS_COUNT:`, `GAPS_COUNT:`, and `RECOMMENDATION:` on their own lines. Seniority is classified LLM-side in any language (German "Leitender Entwickler" → senior, Japanese シニア → senior, Spanish "Jefe de Datos" → manager). Edge function only validates the emitted value against an enum whitelist — no English-only regex.
- **`scripts/orchestration.py` extractor subcommands.** `extract-role`, `extract-seniority`, `extract-strengths-count`, `extract-gaps-count`, `extract-recommendation` each mirror the existing extract-fit-score pattern.
- **`pg_cron` retention job.** Events older than 90 days and installations with no activity for 180 days are deleted daily at 03:00 UTC. Runs inside Supabase — no external scheduler needed. Aggregate dashboard views survive retention.
- **`supabase/` source of truth.** Applied migrations + both edge functions + public config committed to the repo so the backend state is auditable.
- **Host self-reporting (`--host`)** alongside model self-reporting. Codex CLI doesn't set env vars that bash can sniff, so the orchestrator passes `--host codex_cli` literally. Same pattern as `--model`. Resolution order: flag > RESUMASHER_HOST env > env-var sniff > "unknown". Result: 100% `host` field population across Claude Code / Codex / Gemini in live tests.
- **Scope-matched state directory.** User-scope install (skill at `~/.claude/skills/...`) → state in `~/.resumasher/`. Project-scope install (skill at `<project>/.claude/skills/...`) → state in `<project>/.resumasher/`. Deleting the project actually cleans up the telemetry state. Auto-detected based on skill install path.
- **Auto-detected `install_scope_path`.** Log script derives `user_home` vs `project_local` from the skill's install path — orchestrator doesn't have to pass it. Works across all three host conventions (`.claude`, `.codex`, `.gemini`).
- **Per-(run_id, event_type) dedup** for terminal events (`run_started`, `run_completed`, `run_failed`). If an orchestrator retries a phase after a transient error, the second fire is suppressed silently. `placeholder_fill_choice` is intentionally exempt (fires N times per run).

### Data philosophy

- **Raw in, curated out.** The fit-analyst prompt asks the LLM for structured enums (`SENIORITY`, `RECOMMENDATION`); stronger models (Claude Opus, Gemini 2.5 Pro, GPT-5 Codex) comply reliably; weaker models (Haiku, `-mini`) paraphrase. Rather than null-out non-conforming values at ingest, the edge function now stores whatever the LLM emitted (lowercased + length-capped). Pipeline views downstream normalize via `CASE WHEN`. Event type is the only enum that stays validated because the schema shape depends on it.

### Fixed during live multi-host testing

- Phase 1 `mkdir -p .resumasher/run/` now happens BEFORE `jd.txt` is written (Gemini retry case).
- `$RUN_DIR` re-derived at the start of every phase's telemetry block — shell state doesn't persist across Bash tool calls.
- Empty `start-ts.txt` content now treated the same as a missing file (fall back to END_TS) so `duration_s` never ends up as a 56-year unix-epoch-sized value.
- `count_placeholders()` in Phase 9 no longer doubles stdout on zero matches (replaced `|| echo 0` with `|| true`); the log script's `f_num` helper defensively takes the first whitespace-delimited token of any numeric input as a second layer of protection.
- Consent prompt reorder: Off first (highlighted default), no "(Recommended)" label on Community — GDPR Article 7 says pre-selected yes + press-Enter is NOT valid consent.

Eight commits of live-test refinements sit inside this release. Full list: `git log v0.1.0..v0.2.0 --oneline`.
- **Model tracking.** Events now carry a `model` field (e.g. `claude-opus-4-7`, `gpt-5-codex`, `gemini-2.5-pro`) so the maintainer can answer questions like "which model produces the highest fit scores" or "which model hits this bug most". Self-reported by the orchestrator LLM on every event. Migration 008 adds the column; edge function propagates it with a 40-char cap; `--model` flag added to `bin/resumasher-telemetry-log`. PRIVACY.md updated to disclose.
- **Phase 9 underfill fixed.** `run_completed` now carries `used_multirole_format` alongside the existing tailor_completed event, so dashboards don't have to join events by `run_id` to see whether the multi-role rendering path was exercised.

### Security

- **RLS locked down to deny-all for the anon role** on both `telemetry_events` and `installations`. The edge functions use `SUPABASE_SERVICE_ROLE_KEY` internally to bypass RLS for validated writes; the anon key is purely a gateway ticket for `verify_jwt`. Students (and anyone with the public anon key) cannot read, insert, update, or delete directly via the REST API. Verified end-to-end: direct `POST /rest/v1/telemetry_events` with the anon key returns `42501 permission denied`.
- **Views recreated with `security_invoker=true`** so aggregate views respect the caller's RLS instead of running as their owner. Belt-and-suspenders over the existing `REVOKE SELECT FROM anon`.
- **`run_telemetry_retention()` has a pinned `search_path`** (`public, pg_catalog`) to close the Postgres "mutable search path" advisory-linter finding.

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
