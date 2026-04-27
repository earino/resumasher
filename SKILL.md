---
name: resumasher
description: |
  Tailor the student's resume + generate a cover letter + build an interview-prep
  bundle for a specific job posting. Runs in the student's working directory so it
  can cite evidence from their actual project files (capstone, notebooks, READMEs,
  PDFs). Outputs ATS-friendly PDFs in ./applications/<company-slug>-<date>/.

  Also investigates its own output when students report issues — if a student
  says the PDF looks wrong (missing content, stretched photo, weird section
  order, anything off), follow `docs/DEBUGGING.md` to match the symptom
  against known failure modes and draft a bug report for the maintainer.
argument-hint: <job-source> [--style eu|us] [--photo <path>] [--no-photo]
---

# resumasher

Invoked as `/resumasher <job-source>` from inside the student's resume folder.

`<job-source>` is one of:
- A path to a file containing the job description (`job.md`, `jd.txt`).
- A URL to a job posting.
- Literal text pasted after the command.

Optional flags: `--style eu|us` (override config default), `--photo <path>` or `--no-photo` (override config default).

## Prerequisites

The skill requires Python 3.10+ with these packages (see `requirements.txt`):
`reportlab`, `pdfminer.six`, `chardet`, `nbconvert`.

## Workflow

Follow these phases in order. Every deterministic helper is available as a Python module under `scripts/`, and every LLM phase dispatches via the Task tool with `subagent_type="general-purpose"`.

### Setup: resolve paths in EVERY Bash tool call

⚠️ **CRITICAL: Claude Code's Bash tool runs every command in a fresh shell. Variables set in one Bash tool call do NOT persist to the next.** If you set `SKILL_ROOT` in one Bash call and reference `"$SKILL_ROOT/..."` in the next, `$SKILL_ROOT` will be empty and the command will fail with `permission denied` or `file not found`.

**Every single Bash tool call that touches resumasher's code MUST begin with the path prologue below.** It's short. Just paste it at the top of every command. Don't try to "remember" values from a prior call — they're gone.

The prologue (paste at the top of every Bash tool call):

```bash
SKILL_ROOT=""
NEEDS_INSTALL=""
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
for c in \
  "$HOME/.claude/skills/resumasher" \
  "$PWD/.claude/skills/resumasher" \
  "$REPO_ROOT/.claude/skills/resumasher" \
  "$HOME/.codex/skills/resumasher" \
  "$PWD/.codex/skills/resumasher" \
  "$REPO_ROOT/.codex/skills/resumasher" \
  "$HOME/.gemini/skills/resumasher" \
  "$PWD/.gemini/skills/resumasher" \
  "$REPO_ROOT/.gemini/skills/resumasher" \
  "$HOME/.opencode/skills/resumasher" \
  "$PWD/.opencode/skills/resumasher" \
  "$REPO_ROOT/.opencode/skills/resumasher"; do
  [ -n "$c" ] || continue
  [ -f "$c/SKILL.md" ] || continue
  if [ -x "$c/.venv/bin/python" ] || [ -x "$c/.venv/Scripts/python.exe" ]; then
    SKILL_ROOT="$c"; break
  else
    NEEDS_INSTALL="$c"
  fi
done
if [ -z "$SKILL_ROOT" ]; then
  if [ -n "$NEEDS_INSTALL" ]; then
    echo "ERROR: resumasher found at $NEEDS_INSTALL but its Python venv is missing." >&2
    echo "This means install.sh was never run after git clone. Fix:" >&2
    echo "  bash $NEEDS_INSTALL/install.sh" >&2
  else
    echo "ERROR: resumasher is not installed. See https://github.com/earino/resumasher#install" >&2
  fi
  exit 1
fi
RS="$SKILL_ROOT/bin/resumasher-exec"
TEL="$SKILL_ROOT/bin/resumasher-telemetry-log"
STUDENT_CWD="$PWD"
```

This sets:
- `SKILL_ROOT` — absolute path to the installed skill (user-scope OR project-scope).
- `RS` — absolute path to the `bin/resumasher-exec` wrapper that auto-locates the venv Python and the right script.
- `TEL` — absolute path to `bin/resumasher-telemetry-log`, the no-op-when-tier-off event logger called at 8 pipeline boundaries below.
- `STUDENT_CWD` — where the student is working (their resume folder, NOT the skill dir).

**`$MODEL` and `$HOST` are literal strings you substitute, not shell variables the prologue sets.** `$MODEL` is your own model identifier (e.g. `claude-opus-4-7`, `gpt-5-codex`, `gemini-2.5-pro`, `anthropic/claude-opus-4-7` for OpenCode); omit `--model` if you genuinely don't know — null beats fabricated. `$HOST` is exactly one of `claude_code` / `codex_cli` / `gemini_cli` / `opencode_cli` — the CLI that loaded this SKILL.md. Both are self-reported because bash can't reliably detect them across hosts (Codex, for instance, doesn't set a discoverable env var).

The prologue's check distinguishes three failure modes: SKILL_ROOT set → proceed; NEEDS_INSTALL set, SKILL_ROOT empty → cloned but install.sh wasn't run, error message names the fix; both empty → not installed, point at the README.

Every helper call in this document looks like:

```bash
"$RS" orchestration <subcommand> [args...]     # e.g., discover-resume, mine-context, company-slug
"$RS" render_pdf --input ... --output ...      # PDF rendering
"$RS" github_mine <username>                   # GitHub profile mine
```

The `$RS` wrapper handles three things for you: locating SKILL_ROOT by following its own path, execing the venv Python (not system Python — those dependencies aren't installed there), and picking the right script file. Do **not** run `python -m scripts.orchestration` or `python scripts/orchestration.py` directly; use `$RS` instead.

**Run scratch files go in `$STUDENT_CWD/.resumasher/run/`** — NOT `/tmp/`. That directory is:
- Already gitignored (the top-level `.resumasher/` entry).
- Scoped to the student's working folder, not system-global.
- Wiped at the start of each run so prior scratch can't leak.

Create it once per run, near the top:

```bash
RUN_DIR="$STUDENT_CWD/.resumasher/run"
rm -rf "$RUN_DIR"
mkdir -p "$RUN_DIR"
```

Then every intermediate — resume text, folder context, sub-agent outputs — writes into `$RUN_DIR/`, not `/tmp/`.

### Interactive prompt pattern (cross-host)

This skill runs on Claude Code, Codex CLI, Gemini CLI, and OpenCode. Each host has a different tool name but the same contract: present 2+ real options, let the student type free text in an "Other" field. The tools are:

- **Claude Code:** `AskUserQuestion`
- **Codex CLI:** `request_user_input` (NOT `ask_user_question` — that's an unshipped enhancement request)
- **Gemini CLI:** `ask_user`
- **OpenCode:** `question`

Wherever this document says "use the question tool" or names `AskUserQuestion`, use whichever tool your host provides. Reference them with backticks — models match fenced tool names more reliably than bare prose.

⚠️ **All four tools require a MINIMUM of 2 real options.** "Other" is auto-added and does NOT count toward the minimum. Supplying only 1 option crashes with `InputValidationError: Too small: expected array to have >=2 items` (Claude) or `"request_user_input requires non-empty options for every question"` (Codex). Gemini and OpenCode are similarly strict. This is the #1 first-run-setup bug to avoid.

Your job when collecting a free-text value is to avoid TWO separate mistakes:

1. Passing only 1 explicit option (API error, nothing happens).
2. Designing a middleman flow where round 1 asks "will you provide a value?" and round 2 actually collects it (API works, but doubles the prompts).

Both are avoidable with the right 2-option + Other shape.

✅ **Correct pattern A — when a default value exists** (e.g., you extracted `name` / `email` / `phone` / `linkedin` / `location` from a `resume.pdf`):

```
Question: "Phone number for the resume?"
  A) Use the value from your resume: "+43 664 1234567"
  B) Skip — don't include phone on the tailored resume
  Other: paste a different phone number
```

Two real options (A = accept default, B = skip), plus Other for the student to override. One round, collects the value immediately.

✅ **Correct pattern B — when no default exists** (e.g., GitHub username, photo path — the PDF doesn't contain these):

```
Question: "Do you have a GitHub? We can leverage it for this."
  A) I have one — paste the username/URL in Other below
  B) Skip — leave blank; set github_prompted=true so we don't re-ask
  Other: paste your GitHub username or profile URL
```

Two real options (A = I'll provide a value, use the Other field on this screen, B = skip permanently), plus Other for the actual value. Student picks "Other" in practice (since that's where the input is) — `A` exists purely to satisfy the minimum-2 constraint AND to give a visible hint that there IS an input field.

❌ **Wrong pattern 1** — 1 real option (API error):

```
Question: "Phone number?"
  A) Skip
  Other: paste your phone   ← InputValidationError, too few options
```

❌ **Wrong pattern 2** — middleman (2 rounds):

```
Round 1: "Phone number?"
  A) Skip
  B) I'll enter it          ← Student picks B
Round 2: "Type your phone number in Other field"
  A) (forced placeholder)
  Other: paste real value   ← Actual value arrives here
```

Doubles the prompts; the student could have pasted in round 1's Other directly.

Apply pattern A or B to every free-text collection: name, email, phone, location, LinkedIn, photo path, GitHub username.

### No interactive tool available — hard-fail fallback

If none of the three question tools is available (e.g., `codex exec` non-interactive mode, a CI script run, or a host that doesn't yet ship any of them), do NOT guess values from context. Silent inference produced wrong configs for ambiguous inputs in v0.1 — students got run-time decisions they didn't make.

Instead:

1. Stop before Phase 1.
2. Write a skeleton `.resumasher/config.json` in `$STUDENT_CWD` with every required field set to the sentinel string `"__ASK__"`. Include `name`, `email`, `phone`, `linkedin`, `location`, `default_style`, `include_photo`, `photo_path`, `photo_position`, `github_username`, and `github_prompted: false`.
3. Print exactly this message to stdout, then exit with code 2:

   ```
   resumasher needs answers to its setup questions but this host does not
   support interactive prompts. Edit .resumasher/config.json, replace every
   "__ASK__" value with your real answer (use "" to skip optional fields
   like linkedin/photo_path), then re-run the skill.
   ```

This halt-and-resume path is the ONLY acceptable fallback. Never infer name, email, GitHub username, or style from resume content or JD location.

### Sub-agent prompt pattern (cross-host)

Every LLM sub-agent resumasher dispatches (folder-miner, fit-analyst, company-researcher, tailor, cover-letter, interview-coach) uses a prompt built from runtime content — the student's resume, the folder summary, the JD, etc.

**Do NOT build these prompts inline with string interpolation.** LLM judgment is the wrong tool for a mechanical string operation: cross-host testing showed Gemini CLI's fit-analyst received `{resume_text}` unfilled and produced a fit assessment that said *"the resume section is a placeholder."* Use `build-prompt` instead.

Instead, use `build-prompt`:

```bash
PROMPT=$("$RS" orchestration build-prompt --kind <kind> --cwd "$STUDENT_CWD" [--out-dir "$OUT_DIR"] [--company "$COMPANY"])
```

`build-prompt` reads the appropriate files from `$RUN_DIR/` / `.resumasher/cache.txt` / `$OUT_DIR/`, substitutes them into the kind's template (defined in `scripts/prompts.py`), and emits the fully-rendered prompt to stdout. No LLM-side substitution, no ambiguity. If a required file is missing, `build-prompt` exits with code 2 and a clear error naming the file and the phase that produces it.

**If a prompt is too large to round-trip through a shell variable** (the `folder-miner` prompt routinely exceeds 100KB on a real GitHub mine, and some hosts cap argv length at 128KB), stage the rendered prompt to a file inside `$RUN_DIR/prompts/` — NEVER `/tmp/` — then read it back when dispatching:

```bash
mkdir -p "$RUN_DIR/prompts"
"$RS" orchestration build-prompt --kind folder-miner --cwd "$STUDENT_CWD" \
  > "$RUN_DIR/prompts/folder-miner.txt"
PROMPT=$(cat "$RUN_DIR/prompts/folder-miner.txt")
```

`$RUN_DIR/prompts/` is gitignored and wiped each run — staged prompts never leak across sessions or land in git history. **`/tmp/` is forbidden** for prompt staging: on macOS it's world-readable to other local users until reboot (exposing the student's resume + JD + project content as plaintext PII), files there outlive the run, and we have no cleanup hook for paths the agent improvises. A Phase 9 cleanup scan deletes `/tmp/<kind>-prompt.txt` stragglers as defense-in-depth, but the prescription above is the first line of defense.

Then dispatch the sub-agent with `$PROMPT` as the instruction text. **Pass `$PROMPT` AS-IS — do not paraphrase, summarize, shorten, or rewrite it before dispatching.** The compiled prompt is tuned per kind: labeled `<<<...BEGIN>>>/<<<...END>>>` markers, prompt-injection defenses for UNTRUSTED content, exact ordering of structural instructions like "Start with a greeting H1" that downstream rendering depends on. A weak model that "improves" the prompt — observed: a Qwen run inverted "Start with" to "End with" and the cover letter's salutation rendered as a giant H1 at the bottom of the PDF — ships broken artifacts that look superficially correct. **The dispatch primitive AND the `subagent_type` value differ per host — use the entry that matches the CLI you're actually running in, not the first one listed.** Picking the wrong `subagent_type` returns `Unknown agent type: <X> is not a valid agent type` and burns a dispatch attempt (a weak model on OpenCode picked Claude Code's `general-purpose` instead of OpenCode's `general` and got rejected before self-correcting).

- **Claude Code:** `Task` tool with `subagent_type="general-purpose"` and the prompt as `description`/`prompt`.
- **OpenCode:** `task` tool (lowercase) with `subagent_type="general"` (NOT `"general-purpose"` — that's Claude Code's value) and the prompt as `description`/`prompt`. Same shape as Claude Code's `Task`. Note: same-message parallel dispatch works in current builds but has been historically flaky ([sst/opencode#14195](https://github.com/sst/opencode/issues/14195)) — if two concurrent dispatches serialize instead of running in parallel, that's known and benign.
- **Gemini CLI:** `@generalist` (its built-in generalist sub-agent).
- **Codex CLI:** explicitly instruct the model to spawn a sub-agent — "spawn a sub-agent with the following prompt and return its output." Without the explicit spawn request, Codex tends to run the task inline in the parent session (still produces correct output, but loses prompt-injection isolation).

The six kinds and their required inputs:

| Kind | Reads | Output |
|---|---|---|
| `folder-miner` | `$RUN_DIR/context.txt` | prose summary → save to `.resumasher/cache.txt` |
| `fit-analyst` | resume, cache.txt, jd.txt | fit assessment with FIT_SCORE + COMPANY lines |
| `company-researcher` | `--company` arg | 3-5 bullet facts with citations |
| `tailor` | resume, cache.txt, jd.txt | tailored resume markdown |
| `cover-letter` | tailored-resume, jd.txt, company-research | 3-paragraph cover letter |
| `interview-coach` | tailored-resume, cache.txt, jd.txt | SQL + case + behavioral bundle |

---

### Phase 0 — First-run setup (skip if already done)

Check whether this folder has been through first-run setup:

```bash
cd "$STUDENT_CWD"
"$RS" orchestration first-run-needed .
```

If it prints `yes` and exits 1: run the setup flow.

Print the GDPR notice:

> resumasher stores your contact info and application history LOCALLY in
> `.resumasher/` inside this folder. If this folder is a git repo, we will
> add `.resumasher/` to your .gitignore automatically.
>
> Your resume content, job descriptions, and application outputs are never
> uploaded. At the end of setup you can OPTIONALLY opt into anonymous usage
> analytics (event types, fit scores, company names, no resume or JD text)
> to help the maintainer see what's breaking. Default is off. Full detail:
> `PRIVACY.md` in the skill directory.

**Pre-fill from resume.pdf when possible.** If a `resume.pdf` is present, extract its text (`"$RS" orchestration read-resume resume.pdf`) and try to spot the candidate's name, email, LinkedIn, and location. Show those extracted values as the defaults in your questions so the student only has to CONFIRM, not retype them. Saves 3+ prompt rounds on first-run setup.

Use the platform's question tool (`AskUserQuestion` in Claude Code, `request_user_input` in Codex, `ask_user` in Gemini, `question` in OpenCode) to collect the remaining values. Follow the "Interactive prompt pattern (cross-host)" section above: every free-text field uses a 2-option question where the student pastes the answer in Other. Do NOT create a three-option "I'll provide it" middleman.

Concrete question shapes. Every free-text question has at least 2 real options in the `options` array (plus the auto-added Other) — fewer crashes with `InputValidationError`.

**Pattern A canonical shape** (extraction default exists). Use this for `name`, `email`, `phone`, `linkedin`, `location`:

```
Question: "Your resume extract shows '{name}'. Use this on the tailored resume?"
  A) Yes, use '{name}' exactly as shown
  B) Skip — no {field} on the resume
  Other: paste a different {field}
```

**Phone special case:** if PDF extraction found nothing, drop option A and use the Pattern B shape ("A) I have one — paste in Other / B) Skip / Other: paste your phone").

**Style + photo-include — genuine 2-option choices** (no Other path):
- `default_style`: EU (DACH / EU applications) vs US (no photo).
- `include_photo`: Yes vs No (No is more common for anglophone markets).

**Photo path** (only if `include_photo=true`): Pattern B shape with `Other: absolute path`. After the student answers, **verify the file exists with `ls -la <path>` and re-ask on missing — never silently fall through to a broken render.**

**Photo position** (only if `include_photo=true` and the path is valid): three real options — Top right (DACH convention), Top left (French / Benelux), Centered. Save to `photo_position` in config.json as `"right"` / `"left"` / `"center"`. Default is `"right"` if the question is skipped, but the question flow answers first so the default is rarely used.

**GitHub profile:** Pattern B shape ("A) I have one / B) Skip — sets `github_prompted=true` so we don't re-ask"). Other accepts username or profile URL; we'll strip the prefix.

**Usage analytics consent** — the LAST question of first-run setup, before config.json is written.

**GDPR compliance requires Off to be the pass-through default.** Under GDPR Article 7, "consent" means an active, affirmative action. A pre-selected "yes" option that the student accepts by pressing Enter is NOT valid consent. Therefore: Off is listed FIRST (so it's the highlighted default choice in the host's question UI) and NO option carries a "(Recommended)" label. The student has to actively move the cursor to Anonymous or Community to opt in.

```
Question: "Help us improve resumasher?

resumasher is a research tool. If you opt in, we log anonymous usage events
so the maintainer can see what's breaking and what students actually use.
See PRIVACY.md for the full list of what's logged and what isn't. You can
change this anytime with 'resumasher telemetry set-tier <tier>'."
  A) Off. Nothing is logged or sent. This is the default.
  B) Anonymous. Logs events to the backend without an installation identifier.
     Runs cannot be correlated.
  C) Community. Logs events plus a random installation ID so the maintainer
     can see 'this user is hitting the same bug repeatedly'. No names, no
     resume content, no JD text.
```

Write the chosen value to `telemetry` in config.json: `"off"`, `"anonymous"`, or `"community"`. **If the student presses Enter on the highlighted default, that selects Off — which is GDPR's required "no consent given" state.** Do NOT re-order, do NOT add "(Recommended)" to Anonymous or Community, do NOT pre-select a non-Off option in any way. Active opt-in only.

If the student already has a `config.json` from before GitHub was a field, AND does not have `github_prompted: true`, ask the GitHub question once at the top of the current run and rewrite the config. One-time upgrade prompt.

**Photo position migration (issue #22, added 2026-04).** If the student has a `config.json` with `include_photo: true` but no `photo_position` field, ask the same photo-position question from step 8a once, save the answer, and continue. One-time upgrade prompt per student. Students with `include_photo: false` are unaffected (no photo → placement is moot).

Write `.resumasher/config.json` with those values. The parent `.resumasher/` directory may not exist yet on a fresh folder — **create it first** before redirecting into the file, otherwise the redirect fails with `zsh: no such file or directory: .../config.json` and the next phase silently runs against an empty config. The `mkdir -p` is idempotent; cheap insurance:

```bash
mkdir -p "$STUDENT_CWD/.resumasher"
cat > "$STUDENT_CWD/.resumasher/config.json" << 'CONFIGEOF'
{
  "name": "...",
  "email": "...",
  ...
}
CONFIGEOF
"$RS" orchestration ensure-gitignore .
```

(`ensure-gitignore` is idempotent. Returns nothing and exits 0 if the folder isn't inside a git repo.)

**Fire telemetry (end of Phase 0).** If the student opted into `anonymous` or `community` this call logs a `first_run_setup_completed` event and syncs to the backend; if they chose `off` the script exits 0 without writing anything. Either way, run it unconditionally — the script reads the tier from `config.json` and decides:

```bash
"$TEL" --event-type first_run_setup_completed --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --setup-outcome completed \
  --style "$STYLE" \
  --photo-included "$PHOTO_INCLUDED" \
  --github-configured "$GITHUB_CONFIGURED"
```

The `install_scope_path` field is auto-detected by the log script from the skill's own path ($HOME/.claude/skills/... → `user_home`; other locations → `project_local`). You don't need to pass it explicitly.

Substitute `$STYLE` with the chosen style ("eu" or "us"), `$PHOTO_INCLUDED` with "true" or "false", `$GITHUB_CONFIGURED` with "true" or "false" depending on whether `github_username` is set. For `$MODEL` substitute your own model identifier literally (e.g. `claude-opus-4-7`, `gpt-5-codex`, `gemini-2.5-pro`). For `$HOST` substitute the host CLI literally (`claude_code`, `codex_cli`, `gemini_cli`, or `opencode_cli`). The script never exits non-zero; its failures are silent so the student never sees telemetry errors.

---

### Phase 1 — Intake

**Set up the run scratch directory FIRST** — every later step in this phase writes files into it, so it must exist before anything else runs:

```bash
RUN_DIR="$STUDENT_CWD/.resumasher/run"
rm -rf "$RUN_DIR"
mkdir -p "$RUN_DIR"
```

Parse the job source and save the JD text to `$RUN_DIR/jd.txt` (later phases — fit-analyst, tailor, cover-letter, interview-coach — read from that path, and Phase 3 copies the file to `$OUT_DIR/jd.md` for the student's records):

```bash
# Capture the mode (single word: file / url / literal). Safe to capture
# in $(...) under any shell — no JSON, no escapes, no newlines.
JD_MODE=$("$RS" orchestration parse-job-mode "$JOB_SOURCE_ARG")
```

Route the write through `format-jd`. For file and literal modes, pipe `parse-job-content` directly through `format-jd` — never round-trip the content through a shell variable, which would let `echo`-interpret-backslash quirks (zsh, dash, bash with `xpg_echo`) corrupt the bytes:

```bash
# mode=file or mode=literal — pipe content directly, no shell-string roundtrip:
"$RS" orchestration parse-job-content "$JOB_SOURCE_ARG" \
  | "$RS" orchestration format-jd --mode "$JD_MODE" > "$RUN_DIR/jd.txt"

# mode=url — fetch the page FIRST, then pipe the fetched text with --url set:
echo -n "$FETCHED_PAGE_TEXT" | "$RS" orchestration format-jd --mode url --url "$URL" > "$RUN_DIR/jd.txt"
```

`format-jd` is a pure transform — it takes the raw content on stdin, prepends `Source URL: <url>\n\n` when `mode=url`, and emits the final bytes on stdout. File and literal modes pass through unchanged. If `--url` is omitted under `mode=url`, the prepend is skipped (defensive fallback — better to ship an un-headered JD than crash).

If `mode == "url"`: fetch the page with the WebFetch tool (Claude Code) or the equivalent `web_fetch` tool (Gemini) / curl-via-Bash (Codex, which conflates fetch with search) / `webfetch` tool (OpenCode). If the returned text is shorter than 500 characters or clearly a login wall (contains "Sign in", "Log in", or similar without the JD content), prompt the student via the platform's question tool (`AskUserQuestion` in Claude Code, `request_user_input` in Codex, `ask_user` in Gemini, `question` in OpenCode) to paste the JD text manually, then treat the response as `mode: "literal"` (no `--url` needed in the format-jd call since the student's paste has no URL).

**Language detection.** If the JD text is not English, block with a clear message: "resumasher v0.1 supports English JDs only. Detected: <lang>. Please paste an English translation and retry." (Use your own judgment to detect the language — no external detector needed.)

**Generate `$RUN_ID` and capture `$START_TS`** so every event from this run shares the same UUID. `$RUN_DIR` was created at the top of this phase; reuse it:

```bash
RUN_ID=$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid 2>/dev/null)
START_TS=$(date +%s)
echo "$RUN_ID" > "$RUN_DIR/run-id.txt"
echo "$START_TS" > "$RUN_DIR/start-ts.txt"
```

**Defer `run_started` telemetry to Phase 2** — it requires `$RESUME_FORMAT`, which isn't known until after Phase 2's `discover-resume` succeeds. Firing it here with a fabricated or empty `--resume-format` would ship garbage. The exact call lives at the end of Phase 2.

---

### Phase 2 — Folder mine (and GitHub mine, if configured)

Resolve the GitHub username for this run. Precedence: `--github <user>` CLI flag > `github_username` from `.resumasher/config.json` > empty.

```bash
# Read github_username from config if not overridden by a flag.
GITHUB_USER="${GITHUB_FLAG:-$(jq -r '.github_username // ""' "$STUDENT_CWD/.resumasher/config.json" 2>/dev/null || echo "")}"
```

If `$GITHUB_USER` is set (either from config or the flag), the mine phase mixes GitHub evidence into the folder-miner's context block. No separate sub-agent needed — the existing folder-miner prompt already knows how to summarize prose input.

Locate the resume:

```bash
RESUME_PATH=$("$RS" orchestration discover-resume "$STUDENT_CWD")
```

`discover-resume` looks for (in priority order): `resume.md`, `resume.markdown`, `cv.md`, `CV.md`, `resume.pdf`, `Resume.pdf`, `cv.pdf`, `CV.pdf`. Markdown is preferred because it's source-of-truth and diff-friendly; PDF works when the student only has a PDF export. If both a `.md` and a `.pdf` exist, the `.md` wins.

**If `$RESUME_PATH` is empty (discover-resume exited with `FAILURE: no resume found`):** the fast path missed. Don't halt — a student whose resume is named `Lebenslauf.md` (German), `curriculum.md` (Spanish), `履歴書.md` (Japanese), `my_resume_final_v3.md`, or anything else outside the canonical English filename list is still a valid user. Fall through to asking.

Use the platform's question tool (`AskUserQuestion` in Claude Code, `request_user_input` in Codex, `ask_user` in Gemini, `question` in OpenCode) with:

> I couldn't find a resume with one of the default filenames (resume.md, cv.md, resume.pdf, etc.) in this folder. What's the filename? Examples: `Lebenslauf.md`, `履歴書.md`, `my_resume.pdf`.

Validate the student's answer with the `validate-resume-path` subcommand:

```bash
RESUME_PATH=$("$RS" orchestration validate-resume-path "$STUDENT_CWD" "$STUDENT_ANSWER")
```

- Exits 0 and prints the absolute path on success.
- Exits 1 and prints `FAILURE: <reason>` to stderr on failure (file doesn't exist, wrong extension, is a directory, unreadable).

If validation fails, re-ask the student with a clearer error — e.g., "That file (`notes.docx`) has an unsupported extension. resumasher accepts `.md`, `.markdown`, and `.pdf`. What's the actual filename?"

Give the student up to 3 attempts. If all 3 fail, halt with:

> resumasher needs a resume to work with. Please add a `.md`, `.markdown`, or `.pdf` file to this folder and try again. You can use the skill's GOLDEN_FIXTURES/resume.md as a template.

Once `$RESUME_PATH` is set (either from discover-resume or from the validated fallback), read the resume:

```bash
"$RS" orchestration read-resume "$RESUME_PATH" > $RUN_DIR/resume.txt
```

**Fire `run_started` telemetry now** — `$RESUME_FORMAT` is the value deferred from Phase 1 (one of `resume_md`, `resume_pdf`, `cv_md`, `cv_pdf` based on the filename `discover-resume` returned). `$JD_MODE` was captured in Phase 1.

```bash
case "$RESUME_PATH" in
  *resume.md|*resume.markdown) RESUME_FORMAT=resume_md ;;
  *cv.md|*CV.md) RESUME_FORMAT=cv_md ;;
  *resume.pdf|*Resume.pdf) RESUME_FORMAT=resume_pdf ;;
  *cv.pdf|*CV.pdf) RESUME_FORMAT=cv_pdf ;;
  *) RESUME_FORMAT=resume_md ;;  # student-named markdown via validate-resume-path
esac

"$TEL" --event-type run_started --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --run-id "$RUN_ID" \
  --jd-source-mode "$JD_MODE" \
  --resume-format "$RESUME_FORMAT"
```

Compute the folder state hash and check the cache. When GitHub is configured, append its prose to the context before handing it to the folder-miner sub-agent. GitHub mining has its own internal cache (1-hour TTL under `.resumasher/github-cache/<username>.json`), so repeated runs are cheap.

```bash
FOLDER_HASH=$("$RS" orchestration folder-state-hash "$STUDENT_CWD")
CACHE_PATH="$STUDENT_CWD/.resumasher/cache.txt"
CACHE_HASH_PATH="$STUDENT_CWD/.resumasher/cache.hash"

if [ -f "$CACHE_HASH_PATH" ] && [ "$(cat "$CACHE_HASH_PATH")" = "$FOLDER_HASH" ] && [ -f "$CACHE_PATH" ] && [ -z "$GITHUB_USER" ]; then
  # Cache hit only applies when GitHub is NOT configured. If GitHub is enabled,
  # we always re-run mine-context because GitHub activity can change
  # independently of the local folder state (handled by the internal
  # github-cache TTL inside github_mine.py).
  echo "Folder mine cache hit"
  FOLDER_SUMMARY=$(cat "$CACHE_PATH")
else
  # Build the combined context block. The --github-username flag causes
  # mine-context to append a GITHUB_PROFILE / GITHUB_REPO block after the
  # file listing.
  if [ -n "$GITHUB_USER" ]; then
    "$RS" orchestration mine-context "$STUDENT_CWD" \
      --github-username "$GITHUB_USER" > $RUN_DIR/context.txt
  else
    "$RS" orchestration mine-context "$STUDENT_CWD" \
      > $RUN_DIR/context.txt
  fi
  # Dispatch sub-agent (see FOLDER_MINER_PROMPT below) with $RUN_DIR/context.txt as input.
  # Save the sub-agent's prose summary to $CACHE_PATH and the hash to $CACHE_HASH_PATH.
fi
```

**GitHub mine failure modes** (all non-fatal — skill continues without GitHub evidence):
- Rate limit hit → `orchestration.py` prints a GITHUB_MINE_WARNING to stderr and continues.
- Username not found → same: warning, continue without GitHub.
- Network error → same.

If you want to force-refresh GitHub data mid-session, delete `.resumasher/github-cache/<username>.json` and rerun.

**Build the folder-miner prompt and dispatch:**

```bash
PROMPT=$("$RS" orchestration build-prompt --kind folder-miner --cwd "$STUDENT_CWD")
```

Dispatch a sub-agent with `$PROMPT` as its instruction text (see the "Sub-agent prompt pattern" section for host-specific dispatch primitives). The compiled prompt reads `$RUN_DIR/context.txt`, wraps it in `<<<FOLDER_CONTEXT_BEGIN>>>/<<<FOLDER_CONTEXT_END>>>` markers with tool-usage constraints and prompt-injection defenses, and asks for a 400-800 word prose summary of the student's projects. The full template lives in `scripts/prompts.py` under the `folder-miner` kind.

**Retry budget:** folder-miner is load-bearing. If the output starts with `FAILURE: ` or is empty, retry up to 2 more times (3 total) with the same prompt. If all 3 fail, hard-stop with:

> Evidence extraction failed after 3 attempts. Please run /resumasher again, or paste your project list manually into `resume.md` and retry.

Cache the successful summary. **Save the sub-agent's text response via the Write tool, OR via a heredoc with a quoted delimiter** (`<< 'HEREDOC'`) — never by assigning the response to a single-quoted shell variable and echoing it. Single-quoted shell assignment cannot contain a literal `'` (no `\'` escape inside `'...'`); the moment the sub-agent text contains an apostrophe like `Ana's capstone`, zsh dies with `unmatched '` and `$CACHE_PATH` is left empty — the next phase then fails with `FAILURE: ... requires variable 'folder_summary'`. Heredoc with a single-quoted delimiter is byte-literal and immune.

```bash
# Recommended — heredoc with quoted delimiter, byte-literal:
cat > "$CACHE_PATH" << 'HEREDOC'
<paste the sub-agent's text response here>
HEREDOC

echo "$FOLDER_HASH" > "$CACHE_HASH_PATH"
```

Equivalent on hosts with a Write tool (Claude Code, OpenCode): use Write directly with the sub-agent response as the file body — Write doesn't go through a shell at all, so no quoting hazard. **Avoid** `FOLDER_SUMMARY='...'; echo "$FOLDER_SUMMARY" > file` — that's the broken pattern.

---

### Phase 3 — Fit analysis

**Build the fit-analyst prompt and dispatch:**

```bash
PROMPT=$("$RS" orchestration build-prompt --kind fit-analyst --cwd "$STUDENT_CWD")
```

Dispatch a sub-agent with `$PROMPT` as its instruction text. The compiled prompt wraps the resume (from `$RUN_DIR/resume.txt`), folder summary (from `.resumasher/cache.txt`), and JD (from `$RUN_DIR/jd.txt`) in labeled markers and asks for a prose fit assessment ending with `FIT_SCORE: N` and `COMPANY: <name>` sentinel lines. Template: `scripts/prompts.py` `fit-analyst` kind.

**You MUST pipe the fit-analyst output through `extract-fit-fields` — do NOT write the per-field files manually with `echo`.** The extractor enforces enum validation that prevents garbage values from landing in telemetry: `seniority.txt` only gets populated if the value is in the canonical enum (`intern`/`junior`/`mid`/`senior`/`staff`/`manager`/`director`/`vp`/`cxo`); `recommendation.txt` only gets populated if the value normalizes to `yes` / `yes_with_caveats` / `no`. Manual `echo "Entry/Junior" > seniority.txt` bypasses both gates and ships freeform strings to the dashboard, where they don't fit any aggregation bucket. The fit-analyst output may also contain markdown-bold variants like `**ROLE:** Data Analyst` instead of plain `ROLE: Data Analyst`; the extractor handles both forms but a manual `grep` you write yourself usually doesn't. Pipe the output and trust the extractor.

The extractor reads more than just fit_score/company: ROLE, SENIORITY, STRENGTHS_COUNT, GAPS_COUNT, RECOMMENDATION are all extracted. Each field is persisted to its own file under `$RUN_DIR/fit/` so Phase 9 (a separate Bash tool call with no inherited shell state) can read them back without shell-source hazards:

```bash
mkdir -p "$RUN_DIR/fit"
# REQUIRED: pipe the fit-analyst output through extract-fit-fields.
# DO NOT replace this with `echo "8" > $RUN_DIR/fit/score.txt` etc. —
# manual writes bypass enum validation and ship garbage to telemetry.
echo "$FIT_OUTPUT" | "$RS" orchestration extract-fit-fields --output-dir "$RUN_DIR/fit"

# Capture into shell variables for inline use within this Phase 3 block.
# Phase 9 will re-read from the per-field files via $(cat ...) — never
# from a heredoc env file, never via shell-source. See issue #50 for why.
FIT_SCORE=$(cat "$RUN_DIR/fit/score.txt")
COMPANY=$(cat "$RUN_DIR/fit/company.txt")
ROLE=$(cat "$RUN_DIR/fit/role.txt")
SENIORITY=$(cat "$RUN_DIR/fit/seniority.txt")
STRENGTHS_COUNT=$(cat "$RUN_DIR/fit/strengths.txt")
GAPS_COUNT=$(cat "$RUN_DIR/fit/gaps.txt")
RECOMMENDATION=$(cat "$RUN_DIR/fit/recommendation.txt")
```

**Do NOT improvise an `fit-extracted.env` heredoc + `source` pattern.** Unquoted `COMPANY=Elevation Capital` on its own line, then `. fit-extracted.env`, makes bash parse `Capital` as a command and leaves `COMPANY` empty whenever the company name has a space. The per-field-files pattern above is structurally immune — `$(cat file)` strips the trailing newline but preserves every interior character (spaces, ampersands, single quotes, dollar signs, backticks) byte-perfect.

If `COMPANY` is empty (fit-analyst returned `UNKNOWN` or no line): prompt the student once via the platform's question tool (`AskUserQuestion` in Claude Code, `request_user_input` in Codex, `ask_user` in Gemini, `question` in OpenCode): "I couldn't identify the company from the JD. What company is this role at?" Use the response as `COMPANY`.

**Fire telemetry (end of Phase 3).** After fit-assessment.md is written and extractors have run:

```bash
RUN_DIR="$STUDENT_CWD/.resumasher/run"   # re-derive: shell state doesn't persist across Bash tool calls
RUN_ID=$(cat "$RUN_DIR/run-id.txt")
"$TEL" --event-type fit_computed --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --run-id "$RUN_ID" \
  --fit-score "$FIT_SCORE" \
  --fit-strengths-count "$STRENGTHS_COUNT" \
  --fit-gaps-count "$GAPS_COUNT" \
  --fit-recommendation "$RECOMMENDATION"
```

Compute the output directory and persist the path so Phase 9 can read it back without re-deriving:

```bash
SLUG=$("$RS" orchestration company-slug "$COMPANY")
DATE=$(date +%Y%m%d)
OUT_DIR="$STUDENT_CWD/applications/$SLUG-$DATE"
mkdir -p "$OUT_DIR"
echo "$OUT_DIR" > "$RUN_DIR/out-dir.txt"  # Phase 9 reads via $(cat ...)
cp "$RUN_DIR/jd.txt" "$OUT_DIR/jd.md"
```

The `cp` persists the JD (with Source URL header for URL-mode inputs) into the application folder. `$RUN_DIR/jd.txt` gets wiped at the start of every new run, so without this copy the JD is lost as soon as the student runs resumasher against a different posting. Doing the copy at Phase 3 rather than Phase 9 means the JD survives even if a later phase (company research, tailor, PDF render) hard-stops.

Print the fit score to the terminal: `Fit score: $FIT_SCORE/10. Full assessment saved to $OUT_DIR/fit-assessment.md.`

Save the fit output to `$OUT_DIR/fit-assessment.md` for the student's records — use Write directly with the sub-agent response as the file body, OR a heredoc with a quoted delimiter. **Never** assign the response to a single-quoted shell variable and echo it; sub-agent text often contains apostrophes (`Ana's capstone`, `client's request`) that break single-quoted assignment with `unmatched '` and leave the file empty. Same prescription as Phase 2's cache save:

```bash
cat > "$OUT_DIR/fit-assessment.md" << 'HEREDOC'
<paste the fit-analyst sub-agent's text response here>
HEREDOC
```

**Retry budget:** fit-analyst gets 1 retry. If the retry also returns `FAILURE: ` or a missing FIT_SCORE, hard-stop (cannot proceed without fit context).

---

### Phase 4 — Company research

Dispatch the company-researcher sub-agent, giving it the WebSearch tool.

**Build the company-researcher prompt and dispatch:**

```bash
PROMPT=$("$RS" orchestration build-prompt --kind company-researcher --cwd "$STUDENT_CWD" --company "$COMPANY")
```

Dispatch a sub-agent with `$PROMPT` as its instruction text. Unlike the other sub-agents, company-researcher MUST have `WebSearch` and `WebFetch` (Claude Code) / `web_search` and `web_fetch` (Gemini) / `web_search` opt-in (Codex) / `websearch` and `webfetch` (OpenCode — `websearch` requires `OPENCODE_ENABLE_EXA=1` or the OpenCode provider) tools available — those are the whole point of this task. The compiled prompt asks for 3-5 recent company facts with parenthetical citations. Template: `scripts/prompts.py` `company-researcher` kind.

If the sub-agent returns a FAILURE sentinel, prompt the student via the platform's question tool (`AskUserQuestion` in Claude Code, `request_user_input` in Codex, `ask_user` in Gemini, `question` in OpenCode): "Company research failed (<reason>). Paste 2-3 bullets of what you already know about {company}, or leave blank to accept a generic cover letter."

Save the research to `$OUT_DIR/company-research.md` — same heredoc-or-Write rule as fit-assessment above. Company facts often contain possessives (`OpenAI's funding round`) that would break a single-quoted shell variable.

```bash
cat > "$OUT_DIR/company-research.md" << 'HEREDOC'
<paste the company-researcher sub-agent's text response here>
HEREDOC
```

---

### Phase 5 — Tailor

**Build the tailor prompt and dispatch:**

```bash
PROMPT=$("$RS" orchestration build-prompt --kind tailor --cwd "$STUDENT_CWD")
```

Dispatch a sub-agent with `$PROMPT` as its instruction text. The compiled prompt contains the full tailoring spec — schema, length targets, multi-role tenure format, `[INSERT ...]` placeholder rules, SOFT-alternate requirement, and the non-negotiable ANCHORING RULE that forbids fabricating experience to match the JD. It also contains a pre-built contact header at the top, read from `.resumasher/config.json` — the tailor copies that header verbatim rather than inferring contact info from the resume PDF (which may lack the student's LinkedIn URL or show a stale location). Template: `scripts/prompts.py` `tailor` kind (the canonical source — edits go there, not here).

Save the output to `$OUT_DIR/tailored-resume.md` — same heredoc-or-Write rule. The tailored resume is dense with possessives, single-quoted clauses, dollar signs in metrics ($2M, $500K), and backticks if the candidate has technical bullets. Heredoc with a quoted delimiter is byte-literal and immune.

```bash
cat > "$OUT_DIR/tailored-resume.md" << 'HEREDOC'
<paste the tailor sub-agent's text response here>
HEREDOC
```

**Retry budget:** tailor gets 1 retry. If the retry also fails, hard-stop (the tailored resume is the core deliverable — a stub isn't acceptable).

**Fire telemetry (end of Phase 5).** After tailored-resume.md is written:

```bash
RUN_DIR="$STUDENT_CWD/.resumasher/run"   # re-derive: shell state doesn't persist across Bash tool calls
RUN_ID=$(cat "$RUN_DIR/run-id.txt")
NUM_PLACEHOLDERS=$(grep -c '\[INSERT' "$OUT_DIR/tailored-resume.md" 2>/dev/null || echo 0)
USED_MULTIROLE=$(grep -q 'sub-role\|· \*\*' "$OUT_DIR/tailored-resume.md" 2>/dev/null && echo true || echo false)

"$TEL" --event-type tailor_completed --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --run-id "$RUN_ID" \
  --num-placeholders "$NUM_PLACEHOLDERS" \
  --used-multirole-format "$USED_MULTIROLE"
```

---

### Phase 6 — Cover letter + Interview prep (PARALLEL)

Dispatch BOTH sub-agents in the same message with two sub-agent dispatch calls (`Task` in Claude Code, `task` in OpenCode, `@generalist` in Gemini, sub-agent spawn instructions in Codex). They have no dependency on each other, and running in parallel saves ~30-45 seconds.

**Build the cover-letter prompt:**

```bash
PROMPT_COVER=$("$RS" orchestration build-prompt --kind cover-letter --cwd "$STUDENT_CWD" --out-dir "$OUT_DIR")
```

The compiled prompt reads `$OUT_DIR/tailored-resume.md`, `$RUN_DIR/jd.txt`, and `$OUT_DIR/company-research.md`, and asks for a 3-paragraph ~300-word cover letter ending with a `"# Dear {Company} Hiring Team,"` greeting line. Template: `scripts/prompts.py` `cover-letter` kind.

Save the sub-agent output to `$OUT_DIR/cover-letter.md`.

**Build the interview-coach prompt:**

```bash
PROMPT_PREP=$("$RS" orchestration build-prompt --kind interview-coach --cwd "$STUDENT_CWD" --out-dir "$OUT_DIR")
```

The compiled prompt reads `$OUT_DIR/tailored-resume.md`, `.resumasher/cache.txt`, and `$RUN_DIR/jd.txt`, and asks for a SQL + Case Study + Behavioral STAR bundle with answers anchored to the candidate's actual projects and experience. Template: `scripts/prompts.py` `interview-coach` kind.

**Capture a dispatch timestamp before issuing the sub-agent calls** — the post-phase cleanup scan below uses it as the "files newer than this might be rogue" cutoff:

```bash
DISPATCH_TS=$(date +%s)
```

**Dispatch cover-letter and interview-coach in parallel** — in one orchestrator turn, issue both sub-agent calls with `$PROMPT_COVER` and `$PROMPT_PREP` respectively. Under Claude Code this is two `Task` calls in the same message; under OpenCode two `task` calls in the same message (parallel works in current builds but may serialize — see dispatch notes earlier in this doc); under Gemini two `@generalist` calls; under Codex instruct the model to spawn two sub-agents concurrently.

**Take each sub-agent's text response — the markdown document it returned in its message — and save it via the Write tool OR a heredoc with a quoted delimiter. Never via single-quoted shell assignment.** Cover letters routinely contain possessives (`the company's mission`, `we're building`) that break `VAR='...'` with `unmatched '` and silently produce empty files; interview-prep bundles contain SQL with backticks and dollar-sign placeholders that break unquoted variants too. Heredoc with `<< 'HEREDOC'` is byte-literal and immune.

```bash
cat > "$OUT_DIR/cover-letter.md" << 'HEREDOC'
<paste the cover-letter sub-agent's text response here>
HEREDOC

cat > "$OUT_DIR/interview-prep.md" << 'HEREDOC'
<paste the interview-coach sub-agent's text response here>
HEREDOC
```

The sub-agents were explicitly instructed not to write files themselves. If a sub-agent disobeyed and wrote a file anyway (observed on weaker models, see issue #29), ignore that file — rely on the text response from the sub-agent's message and let the cleanup scan below remove the rogue file. Do NOT scan the filesystem looking for sub-agent-written files; that is the bug, not the recovery.

**Run the post-phase cleanup scans** to remove any rogue files a misbehaving sub-agent or shell may have left behind:

```bash
# Belt #1: rogue interview-prep-shaped files in $STUDENT_CWD (issue #29).
"$RS" orchestration cleanup-stray-outputs \
    --cwd "$STUDENT_CWD" \
    --out-dir "$OUT_DIR" \
    --since-timestamp "$DISPATCH_TS"

# Belt #2: stray prompt-staging files left in /tmp (issue #45).
# Even with the SKILL.md prescription above ($RUN_DIR/prompts/),
# an agent that improvises around the guidance could still drop
# /tmp/<kind>-prompt.txt files containing student PII (resume, JD,
# project content). On macOS /tmp is world-readable to other local
# users until reboot. This scan deletes any such file with mtime
# newer than $START_TS so PII never sits there.
"$RS" orchestration cleanup-stray-prompts \
    --since-timestamp "$START_TS"
```

Defense-in-depth — the prompt + orchestration changes should prevent rogue files in the first place, but a future weaker model could regress. The first scan is narrow by design: only files newer than `$DISPATCH_TS` whose names match `interview` / `prep` / `bundle` (case-insensitive); student content is never at risk. The second scan is similarly narrow: only `/tmp` (no recursion, never outside `/tmp`), only basenames matching `<kind>-prompt.{txt,md}` for a registered kind, only files newer than `$START_TS`.

**Retry budget:** each gets 1 retry. On second failure, write a stub file:

```
# {Cover Letter | Interview Prep} — generation failed

This document was not generated. Re-run /resumasher <job-source> to regenerate
the full bundle, OR edit this file manually and ask Claude to re-render the
PDF from it (see "Re-rendering after manual edits" in Phase 8).
```

and continue. The student still gets the resume PDF.

---

### Phase 7 — Interactive placeholder fill (resume + cover letter ONLY)

The tailor emits `[INSERT ...]` placeholders when the resume/evidence didn't supply a specific metric (team size, revenue, scale). Those placeholders CANNOT ship in the PDF — a resume with `[INSERT TEAM SIZE]` is embarrassing. Before rendering, walk the student through filling each one.

**Scope:** this phase runs on `tailored-resume.md` and `cover-letter.md`. It does NOT run on `interview-prep.md` — those placeholders are prep prompts for the student to think about before the interview (e.g., `[INSERT SPECIFIC FIRST-HIRE EXAMPLE FROM YOUR RECORD]`), not values to substitute. Interview-prep keeps its placeholders as-is; the summary phase will surface the count so the student reads them.

**Flow per file:**

1. Grep for placeholder lines:
   ```bash
   grep -nE '\[INSERT [^]]+\]' "$OUT_DIR/tailored-resume.md"
   ```
   Each matching line is one bullet to address.

2. For each bullet, Read the full line (including any `<!--SOFT: ... -->` comment), parse out the placeholder tokens (`[INSERT TEAM SIZE]`, etc.) and the SOFT alternate content.

3. Batch questions — up to 4 bullets per question-tool call (`AskUserQuestion` / `request_user_input` / `ask_user` / `question`; all four support batching 2-4 questions per call). For each bullet:

   ```
   Question: "This bullet in tailored-resume.md has placeholders:

     'Led [INSERT TEAM SIZE] data scientists across [INSERT PRODUCT/ORG AREA],
      setting delivery standards, hiring bar, and roadmap prioritization.'

   Placeholders needed: TEAM SIZE, PRODUCT/ORG AREA. What do you want to do?"

     A) Soften — replace with the no-metric version:
        'Led a senior data science organization across multiple product
         verticals, setting delivery standards, hiring bar, and roadmap
         prioritization.'
     B) Drop this bullet entirely
     Other: paste the specifics (e.g., "TEAM SIZE: 8 senior DS engineers;
            PRODUCT/ORG AREA: Measurement Infra")
   ```

   Always show the FULL bullet text, not just the placeholders. The student needs context to decide.

4. Apply each answer with the Edit tool on the markdown file:
   - **Soften**: replace the whole line with the content of the `<!--SOFT: ... -->` comment, stripped of the comment markers.
   - **Drop**: delete the entire bullet line.
   - **Other (student provided specifics)**: mechanically substitute each placeholder token with the value the student provided. If the student pasted free-form prose like "team of 8 in Measurement Infra" rather than field=value pairs, use your own judgment to substitute grammatically — but do NOT invent any values not in the student's response.

   **Fire telemetry after each placeholder is resolved** (one call per placeholder). `$CHOICE` is one of `specifics`, `soften`, or `drop`:

   ```bash
   RUN_DIR="$STUDENT_CWD/.resumasher/run"   # re-derive: shell state doesn't persist across Bash tool calls
   RUN_ID=$(cat "$RUN_DIR/run-id.txt")
   "$TEL" --event-type placeholder_fill_choice --cwd "$STUDENT_CWD" \
     --host "$HOST" \
  --model "$MODEL" \
     --run-id "$RUN_ID" \
     --choice-type "$CHOICE"
   ```

5. After processing all placeholders, re-grep to verify none remain:
   ```bash
   if grep -qE '\[INSERT [^]]+\]' "$OUT_DIR/tailored-resume.md"; then
     echo "ERROR: placeholders still present in tailored-resume.md"
     exit 1
   fi
   ```

6. Repeat the whole flow for `cover-letter.md`. Cover letters rarely have many placeholders (the tailor doesn't usually reach for metrics in the narrative paragraphs), but the mechanism is the same.

7. Also strip any lingering `<!--SOFT: ... -->` HTML comments from the file (whether filled, softened, or dropped, the SOFT annotation shouldn't appear in the PDF):
   ```bash
   sed -i '' 's| *<!--SOFT:[^>]*-->||g' "$OUT_DIR/tailored-resume.md"
   sed -i '' 's| *<!--SOFT:[^>]*-->||g' "$OUT_DIR/cover-letter.md"
   ```
   (macOS sed uses `-i ''`; Linux uses `-i`. Use whichever matches the student's platform.)

Only AFTER all placeholders are addressed and SOFT comments stripped, proceed to Phase 8 (render PDFs).

If the student interrupts mid-fill or expresses frustration with the process, offer an escape: "Would you like to stop here and edit the markdown files manually? They're at `$OUT_DIR/tailored-resume.md` and `$OUT_DIR/cover-letter.md`. When you're done, ask me to re-render the PDFs (see 'Re-rendering PDFs after edits' in SKILL.md for the exact command)." Do not force them through if they clearly want out.

---

### Phase 8 — Render PDFs

Use `render-pdf.py` to produce three PDFs. Pass `--photo` only for EU resumes where the config says photo=true and the photo file exists. US resumes suppress the photo regardless (enforced inside `render-pdf.py`).

Build the photo argument as a bash array — unquoted string expansion (`$PHOTO_ARG`) breaks when the path has spaces or special characters, and has been seen to fail expansion entirely in some shell environments.

```bash
# Read photo config fresh in this shell invocation (shell state does not persist between Bash calls).
INCLUDE_PHOTO=$(jq -r '.include_photo // false' "$STUDENT_CWD/.resumasher/config.json")
PHOTO_PATH=$(jq -r '.photo_path // ""' "$STUDENT_CWD/.resumasher/config.json")
PHOTO_POSITION=$(jq -r '.photo_position // "right"' "$STUDENT_CWD/.resumasher/config.json")

PHOTO_ARGS=()
if [ "$STYLE" = "eu" ] && [ "$INCLUDE_PHOTO" = "true" ] && [ -f "$PHOTO_PATH" ]; then
  PHOTO_ARGS=(--photo "$PHOTO_PATH" --photo-position "$PHOTO_POSITION")
fi

# Resume
"$RS" render_pdf \
  --input "$OUT_DIR/tailored-resume.md" \
  --kind resume \
  --style "$STYLE" \
  --output "$OUT_DIR/resume.pdf" \
  "${PHOTO_ARGS[@]}"

# Cover letter
"$RS" render_pdf \
  --input "$OUT_DIR/cover-letter.md" \
  --kind cover-letter \
  --output "$OUT_DIR/cover-letter.pdf"

# Interview prep
"$RS" render_pdf \
  --input "$OUT_DIR/interview-prep.md" \
  --kind interview-prep \
  --output "$OUT_DIR/interview-prep.pdf"
```

If a markdown input was a stub (cover letter or interview prep generation failed), skip the corresponding PDF render and note it in the summary.

#### Re-rendering after manual edits

When a student says "I edited tailored-resume.md, re-render the PDF" or similar, do NOT re-run `/resumasher <job>` from scratch — that re-dispatches the sub-agents and overwrites their edits. Instead, jump directly to the render commands above with `$OUT_DIR` pointing at the already-existing application folder. Constraints:

- **Only re-render what they edited.** "Re-render the resume" means the resume — don't also regenerate cover-letter.pdf.
- **Do not re-run tailor / cover-letter / interview-coach sub-agents.** The student's manual edits are authoritative; sub-agents would overwrite them.
- **Warn on remaining `[INSERT ...]` placeholders.** Grep the .md before rendering; if any remain, ask "Your edited markdown still has N `[INSERT ...]` placeholders. Render anyway, or fill them first?"
- **Print path + size after each re-render** so the student sees confirmation: `Re-rendered resume.pdf ({size} bytes). Your edits are in the PDF.`

Then fire the rerender telemetry event (`$KIND` is one of `resume`, `cover`, `prep`):

```bash
"$TEL" --event-type rerender_used --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --rerender-kind "$KIND"
```

---

### Phase 9 — Log + Summary

Phase 9 runs in a separate Bash tool call, so shell variables from earlier phases are gone. Re-read the fit fields from `$RUN_DIR/fit/` (issue #50 — never `source` an env file, never improvise a heredoc), and re-derive `$RUN_ID` / `$START_TS` / `$OUT_DIR` from their per-field files:

```bash
RUN_ID=$(cat "$RUN_DIR/run-id.txt")
START_TS=$(cat "$RUN_DIR/start-ts.txt")
OUT_DIR=$(cat "$RUN_DIR/out-dir.txt")  # written in Phase 3 alongside the slug compute
COMPANY=$(cat "$RUN_DIR/fit/company.txt")
ROLE=$(cat "$RUN_DIR/fit/role.txt")
SENIORITY=$(cat "$RUN_DIR/fit/seniority.txt")
FIT_SCORE=$(cat "$RUN_DIR/fit/score.txt")
STRENGTHS_COUNT=$(cat "$RUN_DIR/fit/strengths.txt")
GAPS_COUNT=$(cat "$RUN_DIR/fit/gaps.txt")
RECOMMENDATION=$(cat "$RUN_DIR/fit/recommendation.txt")
```

Append the history record:

```bash
"$RS" orchestration append-history "$STUDENT_CWD" "$(cat <<EOF
{
  "ts": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "company": "$COMPANY",
  "fit_score": $FIT_SCORE,
  "style": "$STYLE",
  "output_dir": "$OUT_DIR",
  "errors": []
}
EOF
)"
```

**Scan each markdown output for placeholders.** After Phase 6.5, tailored-resume.md and cover-letter.md should be at zero (any `[INSERT]` there is a bug — the interactive fill phase should have resolved them all). interview-prep.md will have placeholders on purpose — those are practice prompts, not values to substitute, and the student is expected to see them.

```bash
count_placeholders() {
  if [ -f "$1" ]; then
    # grep -c already prints "0" when there are no matches (exit 1) or
    # "N" when there are matches (exit 0). `|| true` swallows the exit
    # code without appending a second "0" to stdout. Using `|| echo 0`
    # instead would produce "0\n0" for the zero-match case, which then
    # corrupts anything that feeds this value into a JSON serializer.
    grep -c '\[INSERT' "$1" 2>/dev/null || true
  else
    echo 0
  fi
}
PH_RESUME=$(count_placeholders "$OUT_DIR/tailored-resume.md")
PH_COVER=$(count_placeholders "$OUT_DIR/cover-letter.md")
PH_PREP=$(count_placeholders "$OUT_DIR/interview-prep.md")
```

**Fire telemetry (end of Phase 9, terminal flush).** This is the `run_completed` event — it triggers the actual HTTP sync that flushes every mid-run event queued locally during this run:

```bash
RUN_DIR="$STUDENT_CWD/.resumasher/run"   # re-derive: shell state doesn't persist across Bash tool calls
RUN_ID=$(cat "$RUN_DIR/run-id.txt")
END_TS=$(date +%s)
# Read $START_TS from disk (captured at Phase 1 start). Shell state doesn't
# persist across Bash tool calls, so we re-read from the saved file. Defensive:
# empty content or non-numeric content (which arithmetic would silently treat
# as 0, producing a ~56-year epoch-sized "duration") fall back to END_TS so
# DURATION ends up as 0, not a garbage number. This is the observed failure
# mode from the 2026-04-19 Gemini run.
START_TS=$(cat "$RUN_DIR/start-ts.txt" 2>/dev/null | tr -d ' \n\r\t')
case "$START_TS" in
  ''|*[!0-9]*) START_TS="$END_TS" ;;
esac
DURATION=$(( END_TS - START_TS ))
STYLE_CHOSEN=$(jq -r '.default_style // "us"' "$STUDENT_CWD/.resumasher/config.json" 2>/dev/null)
PHOTO_INCLUDED=$(jq -r '.include_photo // false' "$STUDENT_CWD/.resumasher/config.json" 2>/dev/null)
GITHUB_CONFIGURED=$(jq -r '(.github_username // "") != ""' "$STUDENT_CWD/.resumasher/config.json" 2>/dev/null)

"$TEL" --event-type run_completed --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --run-id "$RUN_ID" \
  --duration "$DURATION" \
  --outcome success \
  --company "$COMPANY" \
  --job-title "$ROLE" \
  --seniority "$SENIORITY" \
  --fit-score "$FIT_SCORE" \
  --fit-strengths-count "$STRENGTHS_COUNT" \
  --fit-gaps-count "$GAPS_COUNT" \
  --fit-recommendation "$RECOMMENDATION" \
  --num-placeholders "$PH_RESUME" \
  --used-multirole-format "$USED_MULTIROLE" \
  --style "$STYLE_CHOSEN" \
  --photo-included "$PHOTO_INCLUDED" \
  --github-configured "$GITHUB_CONFIGURED" \
  --used-folder-evidence true \
  --all-pdfs-rendered true
```

If any phase hard-stopped with an error before reaching Phase 9, fire `run_failed` instead with whatever fields you know at that point — `--error-class` is a pre-declared enum (`no_resume`, `non_english_jd`, `folder_miner_failed`, `fit_analyst_failed`, `tailor_failed`, `pdf_render_failed`, `timeout`, `unknown`):

```bash
"$TEL" --event-type run_failed --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --run-id "$RUN_ID" \
  --duration "$DURATION" \
  --failed-phase "$PHASE_NUMBER" \
  --error-class "$ERROR_CLASS"
```

Print a 1-screen summary:

```
resumasher run complete.

Company: {company}
Fit score: {fit_score}/10
Style: {style}
Output: {out_dir}

Files generated:
  ✓ resume.pdf
  ✓ cover-letter.pdf
  ✓ interview-prep.pdf
  ✓ tailored-resume.md (markdown source)
  ✓ cover-letter.md (markdown source)
  ✓ interview-prep.md (markdown source)
  ✓ fit-assessment.md (honest assessment)
  ✓ company-research.md (cited facts)
```

If `PH_RESUME > 0` OR `PH_COVER > 0`, print this ERROR block — it means Phase 7 didn't fully resolve the placeholders, which is a bug:

```
⚠  UNEXPECTED PLACEHOLDERS REMAIN (these should have been filled in Phase 6.5):
   - tailored-resume.md: {PH_RESUME} placeholder(s)
   - cover-letter.md:    {PH_COVER} placeholder(s)

   Open each file and search for "[INSERT". Either the Phase 7 fill-in
   was skipped or had a bug. Edit the .md manually, then ask Claude to
   re-render the PDF (see "Re-rendering after manual edits" in Phase 8).
```

If `PH_PREP > 0`, print this NOTE block (this is expected — interview-prep placeholders are prep prompts, not substitution values):

```
📝 interview-prep.md has {PH_PREP} practice prompts (things like
   "[INSERT SPECIFIC FIRST-HIRE EXAMPLE FROM YOUR RECORD]"). These are
   not filled automatically — they're things to think through BEFORE
   the interview so you walk in with concrete stories ready. Read the
   doc, prepare the stories, walk in dangerous.
```

Then the Next steps block:

```
Next steps:
  1. Open resume.pdf and eyeball it — does the section order match what this
     company expects?
  2. Read cover-letter.pdf paragraph 2 carefully; the AI sometimes overstates.
  3. Skim interview-prep.pdf before the interview. Pay attention to the STAR
     answers — they're drafted from your actual projects.

Applied through Workday or Greenhouse? Upload resume.pdf to jobscan.co
(free preview) with this JD pasted in, and verify the sections parse cleanly
before sending.

💡 Edited a markdown file after this run? Ask me to "re-render the {resume|cover|prep} PDF"
and I'll regenerate just that PDF without re-running the full pipeline.

✨ Want to tweak the look? Open resume.pdf in Google Docs, Pages, Word, or Canva —
they all import PDFs and let you adjust fonts, spacing, and colors with tools
you already know.

🐛 If anything in these PDFs looks off — missing content, weird layout, photo
looks stretched, sections in a strange order — just tell me what you see and
I'll investigate. See "Debugging this skill" in SKILL.md for the playbook.
```

---

## Error recovery

If any phase returns `FAILURE: ` twice, the skill falls back per the retry
budget rules above:

- folder-miner, fit-analyst, tailor: hard-stop.
- company-researcher, cover-letter, interview-coach: continue with stub output.

The student always gets a status summary explaining what succeeded and what
failed, with a concrete retry command for each failed artifact.

## Style flag precedence

`--style` always wins. If `--style us` is passed or config says `us`, the
photo is suppressed regardless of `--photo` or config photo settings.

---

## Usage analytics (telemetry)

The orchestrator fires `"$TEL"` events at 8 pipeline boundaries — the calls are documented inline at each phase, not duplicated here. Behavior worth knowing:

- **No-op when off.** `resumasher-telemetry-log` reads tier from `config.json`. When tier is `off` (the default) the script writes nothing and exits 0. Safe to call unconditionally.
- **Don't block on telemetry.** The script is `set -uo pipefail` (no `-e`) and exits 0 on any internal error. Telemetry failures never surface to the student.
- **Sync semantics.** Mid-run events (`run_started`, `fit_computed`, `tailor_completed`, `placeholder_fill_choice`) write to a local JSONL queue: ~30ms per call. Terminal events (`first_run_setup_completed`, `run_completed`, `run_failed`, `rerender_used`) flush the queue to Supabase in one POST: ~500ms. A typical full run costs ~1.6s of telemetry latency total. If the student kills mid-run, queued events ship on the next run via cursor-based catch-up.
- **`install_scope_path` is auto-detected** from the skill's own path (`$HOME/<host-skill-dir>/...` → `user_home`; anywhere else → `project_local`). No orchestrator substitution needed.
- **`$ERROR_CLASS` enum** for `run_failed`: `no_resume`, `non_english_jd`, `folder_miner_failed`, `fit_analyst_failed`, `tailor_failed`, `pdf_render_failed`, `timeout`, `unknown`.
- **Field whitelist.** The edge function silently drops fields that don't match `supabase/migrations/001_telemetry.sql`. Don't add new `--flag` values here without adding the matching column + ingest whitelist entry too.

---

## Debugging this skill

When a student reports something wrong with a resumasher output (missing name, squished photo, wrong section order, vague "something looks off") — follow `docs/DEBUGGING.md`. Seven-step playbook: find the artifacts in `applications/<slug>-<date>/`, quote the student's report literally, run `"$RS" orchestration inspect` on the resume / PDF / photo, match against `docs/KNOWN_FAILURE_MODES.md`, investigate novel bugs by tracing through `scripts/render_pdf.py`, write the bug report from `docs/BUG_REPORT_TEMPLATE.md`, file it with the student's explicit consent. Anonymize PII (name/email/phone/LinkedIn/GitHub) by default; leave company names + metrics + project names intact (load-bearing for reproduction). Don't build a `--debug` mode, don't paste raw parse-tree JSON, don't auto-file.
