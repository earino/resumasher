---
name: resumasher
description: |
  Tailor the student's resume + generate a cover letter + build an interview-prep
  bundle for a specific job posting. Runs in the student's working directory so it
  can cite evidence from their actual project files (capstone, notebooks, READMEs,
  PDFs). Outputs ATS-friendly PDFs in ./applications/<company-slug>-<date>/.
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
  "$REPO_ROOT/.gemini/skills/resumasher"; do
  [ -n "$c" ] || continue
  [ -f "$c/SKILL.md" ] || continue
  if [ -x "$c/.venv/bin/python" ]; then
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

**Telemetry identifiers you (the orchestrator) substitute literally: `$MODEL` and `$HOST`.** Many `"$TEL"` calls below pass `--model "$MODEL"` and `--host "$HOST"`. These are NOT shell variables the prologue sets — they're strings you substitute with literals before executing the command.

- `$MODEL`: your own model identifier. Examples: `claude-opus-4-7`, `claude-sonnet-4-6`, `gpt-5-codex`, `gpt-5-mini`, `gemini-2.5-pro`, `gemini-2.5-flash`. You know what you are. If you genuinely don't, omit `--model`; null is better than fabricated.
- `$HOST`: which AI CLI you're running in. Exactly one of `claude_code`, `codex_cli`, or `gemini_cli`. You know this — it's literally the CLI that loaded this SKILL.md. If omitted, the log script falls back to env-var sniffing and then to `"unknown"`, which is what we want to avoid.

Both are self-reported because bash can't reliably detect them across host CLIs (Codex, for instance, doesn't set a discoverable env var).

The check distinguishes three failure modes:
- **SKILL_ROOT set, success** — everything good, proceed.
- **NEEDS_INSTALL set, SKILL_ROOT empty** — skill was cloned but `install.sh` was never run. Error message names the exact command to fix it. This is the "future Claude cloned the repo and forgot the install step" case.
- **Both empty** — skill isn't installed at all. Point the user at the README install section.

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

This skill runs on Claude Code, Codex CLI, and Gemini CLI. Each host has a different tool name but the same contract: present 2+ real options, let the student type free text in an "Other" field. The tools are:

- **Claude Code:** `AskUserQuestion`
- **Codex CLI:** `request_user_input` (NOT `ask_user_question` — that's an unshipped enhancement request)
- **Gemini CLI:** `ask_user`

Wherever this document says "use the question tool" or names `AskUserQuestion`, use whichever tool your host provides. Reference them with backticks — models match fenced tool names more reliably than bare prose.

⚠️ **All three tools require a MINIMUM of 2 real options.** "Other" is auto-added and does NOT count toward the minimum. Supplying only 1 option crashes with `InputValidationError: Too small: expected array to have >=2 items` (Claude) or `"request_user_input requires non-empty options for every question"` (Codex). Gemini is similarly strict. This is the #1 first-run-setup bug to avoid.

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
2. Write a skeleton `.resumasher/config.json` in `$STUDENT_CWD` with every required field set to the sentinel string `"__ASK__"`. Include `name`, `email`, `phone`, `linkedin`, `location`, `default_style`, `include_photo`, `photo_path`, `github_username`, and `github_prompted: false`.
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

**Do NOT build these prompts inline with string interpolation.** A previous design had the orchestrator LLM substitute `{resume_text}` / `{folder_summary}` / `{jd_text}` tokens before dispatching. Cross-host testing revealed this is unreliable: under Gemini CLI, the fit-analyst sub-agent received a prompt with `{resume_text}` unfilled and produced a fit assessment that literally said *"the resume section is a placeholder."* Claude and Codex happened to substitute, but LLM judgment is the wrong tool for a mechanical string operation.

Instead, use `build-prompt`:

```bash
PROMPT=$("$RS" orchestration build-prompt --kind <kind> --cwd "$STUDENT_CWD" [--out-dir "$OUT_DIR"] [--company "$COMPANY"])
```

`build-prompt` reads the appropriate files from `$RUN_DIR/` / `.resumasher/cache.txt` / `$OUT_DIR/`, substitutes them into the kind's template (defined in `scripts/prompts.py`), and emits the fully-rendered prompt to stdout. No LLM-side substitution, no ambiguity. If a required file is missing, `build-prompt` exits with code 2 and a clear error naming the file and the phase that produces it.

Then dispatch the sub-agent with `$PROMPT` as the instruction text. The dispatch primitive varies by host:

- **Claude Code:** `Task` tool with `subagent_type="general-purpose"` and the prompt as `description`/`prompt`.
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

Use the platform's question tool (`AskUserQuestion` in Claude Code, `request_user_input` in Codex, `ask_user` in Gemini) to collect the remaining values. Follow the "Interactive prompt pattern (cross-host)" section above: every free-text field uses a 2-option question where the student pastes the answer in Other. Do NOT create a three-option "I'll provide it" middleman.

Concrete question shapes. Every free-text question has EXACTLY 2 or more explicit options in `options` array (plus the auto-added Other). Anything less crashes with `InputValidationError`.

1. **Name** (usually extracted from PDF):
   ```
   Question: "Your resume extract shows '{name}'. Use this on the tailored resume?"
     A) Yes, use '{name}' exactly as shown
     B) Skip — no name on the resume (unusual but allowed)
     Other: paste the exact name to use instead
   ```

2. **Email** (usually extracted from PDF):
   ```
   Question: "Email for the resume?"
     A) Use '{email}' from your resume
     B) Skip — no email on the resume
     Other: paste a different email
   ```

3. **Phone** (may or may not be in PDF):
   ```
   Question: "Phone number for the resume?"
     A) Use '{phone_from_pdf}'     ← only include this option if extraction found a phone
     B) Skip — don't include phone
     Other: paste a different phone (e.g., +43 664 1234567)
   ```
   If no phone extracted, drop option A and fall back to pattern B:
   ```
     A) I have one — paste it in Other below
     B) Skip — don't include phone
     Other: paste your phone
   ```

4. **LinkedIn** (usually extracted from PDF):
   ```
   Question: "LinkedIn URL for the resume?"
     A) Use '{linkedin_url}' from your resume
     B) Skip — don't include LinkedIn
     Other: paste a different URL (we'll normalize to https://)
   ```

5. **Location** (usually extracted from PDF):
   ```
   Question: "City, country for the resume?"
     A) Use '{location}' from your resume
     B) Skip — don't include location
     Other: paste a different location
   ```

6. **Style** — genuine 2-option choice (no Other path expected):
   ```
   Question: "Default resume style?"
     A) EU (recommended for DACH / EU applications)
     B) US (recommended for US applications, no photo)
   ```

7. **Photo include** — genuine 2-option choice:
   ```
   Question: "Include a photo on EU-style resumes by default?"
     A) Yes, include a photo
     B) No photo (more common for anglophone markets)
   ```

8. **Photo path** (only if include-photo=yes):
   ```
   Question: "Where's the photo file? Paste the absolute path in Other."
     A) I have one — paste the absolute path in Other below
     B) Skip photo for this run — I'll add a path later by editing .resumasher/config.json
     Other: absolute path (e.g., /Users/you/Desktop/headshot.png)
   ```
   After the student answers, verify the file exists with `ls -la <path>`. If missing, re-ask; don't silently fall through.

9. **GitHub profile**:
   ```
   Question: "Do you have a GitHub? We can leverage it for this."
     A) I have one — paste the username or profile URL in Other
     B) Skip — leave blank (sets github_prompted=true so we don't re-ask)
     Other: username (e.g., earino) or profile URL (we'll strip the prefix)
   ```

10. **Usage analytics consent** — this is the LAST question of first-run setup, before config.json is written.

    **GDPR compliance requires Off to be the pass-through default.** Under GDPR
    Article 7, "consent" means an active, affirmative action. A pre-selected
    "yes" option that the student accepts by pressing Enter is NOT valid
    consent. Therefore: Off is listed FIRST (so it's the highlighted default
    choice in the host's question UI) and NO option carries a "(Recommended)"
    label. The student has to actively move the cursor to Anonymous or
    Community to opt in.
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
    Write the chosen value to `telemetry` in config.json: `"off"`, `"anonymous"`, or
    `"community"`. **If the student presses Enter on the highlighted default,
    that selects Off — which is GDPR's required "no consent given" state.** Do
    NOT re-order, do NOT add "(Recommended)" to Anonymous or Community, do NOT
    pre-select a non-Off option in any way. Active opt-in only.

If the student already has a `config.json` from before GitHub was a field, AND does not have `github_prompted: true`, ask the GitHub question once at the top of the current run and rewrite the config. One-time upgrade prompt.

Write `.resumasher/config.json` with those values, then:

```bash
"$RS" orchestration ensure-gitignore .
```

(Idempotent. Returns nothing and exits 0 if the folder isn't inside a git repo.)

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

Substitute `$STYLE` with the chosen style ("eu" or "us"), `$PHOTO_INCLUDED` with "true" or "false", `$GITHUB_CONFIGURED` with "true" or "false" depending on whether `github_username` is set. For `$MODEL` substitute your own model identifier literally (e.g. `claude-opus-4-7`, `gpt-5-codex`, `gemini-2.5-pro`). For `$HOST` substitute the host CLI literally (`claude_code`, `codex_cli`, or `gemini_cli`). The script never exits non-zero; its failures are silent so the student never sees telemetry errors.

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
"$RS" orchestration parse-job-source "$JOB_SOURCE_ARG"
# Returns JSON: {"mode": "file|url|literal", "path": "...", "content": "..."}
```

Route the write through `format-jd` so the Source URL header is prepended for URL-mode inputs (matters for `applications/<slug>/jd.md`: students recovering an old run need the URL for recruiter follow-up even after the posting is taken down). Pass the content via stdin:

```bash
# mode=file or mode=literal — content comes straight from parse-job-source:
echo -n "$CONTENT" | "$RS" orchestration format-jd --mode "$MODE" > "$RUN_DIR/jd.txt"

# mode=url — fetch the page FIRST, then pipe the fetched text with --url set:
echo -n "$FETCHED_PAGE_TEXT" | "$RS" orchestration format-jd --mode url --url "$URL" > "$RUN_DIR/jd.txt"
```

`format-jd` is a pure transform — it takes the raw content on stdin, prepends `Source URL: <url>\n\n` when `mode=url`, and emits the final bytes on stdout. File and literal modes pass through unchanged. If `--url` is omitted under `mode=url`, the prepend is skipped (defensive fallback — better to ship an un-headered JD than crash).

If `mode == "url"`: fetch the page with the WebFetch tool (Claude Code) or the equivalent `web_fetch` tool (Gemini) / curl-via-Bash (Codex, which conflates fetch with search). If the returned text is shorter than 500 characters or clearly a login wall (contains "Sign in", "Log in", or similar without the JD content), prompt the student via the platform's question tool (`AskUserQuestion` in Claude Code, `request_user_input` in Codex, `ask_user` in Gemini) to paste the JD text manually, then treat the response as `mode: "literal"` (no `--url` needed in the format-jd call since the student's paste has no URL).

**Language detection.** If the JD text is not English, block with a clear message: "resumasher v0.1 supports English JDs only. Detected: <lang>. Please paste an English translation and retry." (Use your own judgment to detect the language — no external detector needed.)

**Generate `$RUN_ID`, capture `$START_TS`, and fire telemetry (start of Phase 1).** Every event from this run shares the same UUID so the maintainer can trace "what did run X do". `$RUN_DIR` was created at the top of this phase; reuse it:

```bash
RUN_ID=$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid 2>/dev/null)
START_TS=$(date +%s)
echo "$RUN_ID" > "$RUN_DIR/run-id.txt"
echo "$START_TS" > "$RUN_DIR/start-ts.txt"

"$TEL" --event-type run_started --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --run-id "$RUN_ID" \
  --jd-source-mode "$JD_MODE" \
  --resume-format "$RESUME_FORMAT"
```

Substitute `$JD_MODE` with the `mode` field from `parse-job-source` output (`file`, `url`, or `literal`). Substitute `$RESUME_FORMAT` with one of `resume_md`, `resume_pdf`, `cv_md`, `cv_pdf` based on the filename `discover-resume` returned. Substitute `$MODEL` as described in the prologue.

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

Use the platform's question tool (`AskUserQuestion` in Claude Code, `request_user_input` in Codex, `ask_user` in Gemini) with:

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

Cache the successful summary:

```bash
echo "$FOLDER_SUMMARY" > "$CACHE_PATH"
echo "$FOLDER_HASH" > "$CACHE_HASH_PATH"
```

---

### Phase 3 — Fit analysis

**Build the fit-analyst prompt and dispatch:**

```bash
PROMPT=$("$RS" orchestration build-prompt --kind fit-analyst --cwd "$STUDENT_CWD")
```

Dispatch a sub-agent with `$PROMPT` as its instruction text. The compiled prompt wraps the resume (from `$RUN_DIR/resume.txt`), folder summary (from `.resumasher/cache.txt`), and JD (from `$RUN_DIR/jd.txt`) in labeled markers and asks for a prose fit assessment ending with `FIT_SCORE: N` and `COMPANY: <name>` sentinel lines. Template: `scripts/prompts.py` `fit-analyst` kind.

Parse the output (the fit-analyst emits more than just fit_score/company — ROLE, SENIORITY, STRENGTHS_COUNT, GAPS_COUNT, RECOMMENDATION — extract them all for telemetry and downstream phases):

```bash
FIT_SCORE=$(echo "$FIT_OUTPUT" | "$RS" orchestration extract-fit-score)
COMPANY=$(echo "$FIT_OUTPUT" | "$RS" orchestration extract-company)
ROLE=$(echo "$FIT_OUTPUT" | "$RS" orchestration extract-role)
SENIORITY=$(echo "$FIT_OUTPUT" | "$RS" orchestration extract-seniority)
STRENGTHS_COUNT=$(echo "$FIT_OUTPUT" | "$RS" orchestration extract-strengths-count)
GAPS_COUNT=$(echo "$FIT_OUTPUT" | "$RS" orchestration extract-gaps-count)
RECOMMENDATION=$(echo "$FIT_OUTPUT" | "$RS" orchestration extract-recommendation)
```

If `COMPANY` is empty (fit-analyst returned `UNKNOWN` or no line): prompt the student once via the platform's question tool (`AskUserQuestion` in Claude Code, `request_user_input` in Codex, `ask_user` in Gemini): "I couldn't identify the company from the JD. What company is this role at?" Use the response as `COMPANY`.

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

Compute the output directory:

```bash
SLUG=$("$RS" orchestration company-slug "$COMPANY")
DATE=$(date +%Y%m%d)
OUT_DIR="$STUDENT_CWD/applications/$SLUG-$DATE"
mkdir -p "$OUT_DIR"
cp "$RUN_DIR/jd.txt" "$OUT_DIR/jd.md"
```

The `cp` persists the JD (with Source URL header for URL-mode inputs) into the application folder. `$RUN_DIR/jd.txt` gets wiped at the start of every new run, so without this copy the JD is lost as soon as the student runs resumasher against a different posting. Doing the copy at Phase 3 rather than Phase 9 means the JD survives even if a later phase (company research, tailor, PDF render) hard-stops.

Print the fit score to the terminal: `Fit score: $FIT_SCORE/10. Full assessment saved to $OUT_DIR/fit-assessment.md.`

Save the fit output to `$OUT_DIR/fit-assessment.md` for the student's records.

**Retry budget:** fit-analyst gets 1 retry. If the retry also returns `FAILURE: ` or a missing FIT_SCORE, hard-stop (cannot proceed without fit context).

---

### Phase 4 — Company research

Dispatch the company-researcher sub-agent, giving it the WebSearch tool.

**Build the company-researcher prompt and dispatch:**

```bash
PROMPT=$("$RS" orchestration build-prompt --kind company-researcher --cwd "$STUDENT_CWD" --company "$COMPANY")
```

Dispatch a sub-agent with `$PROMPT` as its instruction text. Unlike the other sub-agents, company-researcher MUST have `WebSearch` and `WebFetch` (Claude Code) / `web_search` and `web_fetch` (Gemini) / `web_search` opt-in (Codex) tools available — those are the whole point of this task. The compiled prompt asks for 3-5 recent company facts with parenthetical citations. Template: `scripts/prompts.py` `company-researcher` kind.

If the sub-agent returns a FAILURE sentinel, prompt the student via the platform's question tool (`AskUserQuestion` in Claude Code, `request_user_input` in Codex, `ask_user` in Gemini): "Company research failed (<reason>). Paste 2-3 bullets of what you already know about {company}, or leave blank to accept a generic cover letter."

Save the research to `$OUT_DIR/company-research.md`.

---

### Phase 5 — Tailor

**Build the tailor prompt and dispatch:**

```bash
PROMPT=$("$RS" orchestration build-prompt --kind tailor --cwd "$STUDENT_CWD")
```

Dispatch a sub-agent with `$PROMPT` as its instruction text. The compiled prompt contains the full tailoring spec — schema, length targets, multi-role tenure format, `[INSERT ...]` placeholder rules, SOFT-alternate requirement, and the non-negotiable ANCHORING RULE that forbids fabricating experience to match the JD. It also contains a pre-built contact header at the top, read from `.resumasher/config.json` — the tailor copies that header verbatim rather than inferring contact info from the resume PDF (which may lack the student's LinkedIn URL or show a stale location). Template: `scripts/prompts.py` `tailor` kind (the canonical source — edits go there, not here).

Save the output to `$OUT_DIR/tailored-resume.md`.

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

Dispatch BOTH sub-agents in the same message with two Task tool calls. They have no dependency on each other, and running in parallel saves ~30-45 seconds.

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

**Dispatch cover-letter and interview-coach in parallel** — in one orchestrator turn, issue both sub-agent calls with `$PROMPT_COVER` and `$PROMPT_PREP` respectively. Under Claude Code this is two `Task` calls in the same message; under Gemini two `@generalist` calls; under Codex instruct the model to spawn two sub-agents concurrently.

Save the outputs to `$OUT_DIR/cover-letter.md` and `$OUT_DIR/interview-prep.md`.

**Retry budget:** each gets 1 retry. On second failure, write a stub file:

```
# {Cover Letter | Interview Prep} — generation failed

This document was not generated. Re-run /resumasher <job-source> to regenerate
the full bundle, OR edit this file manually and ask Claude to re-render the
PDF from it (see "Re-rendering PDFs after edits" near the end of SKILL.md).
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

3. Batch questions — up to 4 bullets per question-tool call (`AskUserQuestion` / `request_user_input` / `ask_user`; all three support batching 2-4 questions per call). For each bullet:

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

PHOTO_ARGS=()
if [ "$STYLE" = "eu" ] && [ "$INCLUDE_PHOTO" = "true" ] && [ -f "$PHOTO_PATH" ]; then
  PHOTO_ARGS=(--photo "$PHOTO_PATH")
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

---

### Phase 9 — Log + Summary

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
   re-render the PDF (see "Re-rendering PDFs after edits" in SKILL.md).
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

🐛 If anything in these PDFs looks off — missing content, weird layout, photo
looks stretched, sections in a strange order — just tell me what you see and
I'll investigate. See "Debugging this skill" in SKILL.md for the playbook.
```

---

## Re-rendering PDFs after manual edits

Students often want to tweak the generated markdown (fix a bullet, add a missing detail, change a word) and get the PDF updated WITHOUT re-running the full pipeline. The full pipeline would re-dispatch all the sub-agents and overwrite their edits.

When a student asks to "re-render the resume" or "update the PDF after I edited the markdown," follow this flow. Do NOT re-run `/resumasher <job>` from scratch.

**Path prologue (required — shell state doesn't persist between Bash tool calls):**

```bash
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
  "$REPO_ROOT/.gemini/skills/resumasher"; do
  [ -n "$c" ] || continue
  [ -f "$c/SKILL.md" ] || continue
  [ -x "$c/.venv/bin/python" ] && SKILL_ROOT="$c" && break
done
RS="$SKILL_ROOT/bin/resumasher-exec"
TEL="$SKILL_ROOT/bin/resumasher-telemetry-log"
STUDENT_CWD="$PWD"
```

**Locate the target output directory.** Ask the student which application they edited, or infer from context (most recent `applications/<slug>-<date>/`). Then:

```bash
OUT_DIR="$STUDENT_CWD/applications/<slug>-<date>"   # substitute the real path
```

**Read config for style and photo:**

```bash
STYLE=$(jq -r '.default_style // "eu"' "$STUDENT_CWD/.resumasher/config.json")
INCLUDE_PHOTO=$(jq -r '.include_photo // false' "$STUDENT_CWD/.resumasher/config.json")
PHOTO_PATH=$(jq -r '.photo_path // ""' "$STUDENT_CWD/.resumasher/config.json")
```

**Re-render the one(s) the student edited:**

For the **resume** — pass `--photo` only if style is EU and include_photo is true. Use a bash array, not an unquoted string variable — the latter mis-expands on paths with spaces and has been seen to fail silently in some shell environments.

```bash
PHOTO_ARGS=()
if [ "$STYLE" = "eu" ] && [ "$INCLUDE_PHOTO" = "true" ] && [ -f "$PHOTO_PATH" ]; then
  PHOTO_ARGS=(--photo "$PHOTO_PATH")
fi
"$RS" render_pdf \
  --input "$OUT_DIR/tailored-resume.md" \
  --kind resume \
  --style "$STYLE" \
  --output "$OUT_DIR/resume.pdf" \
  "${PHOTO_ARGS[@]}"
```

For the **cover letter**:

```bash
"$RS" render_pdf \
  --input "$OUT_DIR/cover-letter.md" \
  --kind cover-letter \
  --output "$OUT_DIR/cover-letter.pdf"
```

For the **interview prep**:

```bash
"$RS" render_pdf \
  --input "$OUT_DIR/interview-prep.md" \
  --kind interview-prep \
  --output "$OUT_DIR/interview-prep.pdf"
```

**Fire telemetry after a re-render.** `$KIND` is one of `resume`, `cover`, `prep` depending on which file the student asked to re-render:

```bash
"$TEL" --event-type rerender_used --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --rerender-kind "$KIND"
```

**Important constraints:**

- Only re-render the files the student actually edited. If they said "re-render the resume," don't also regenerate the cover letter — that's 20 extra seconds and tempts you to wonder if you should run the tailor sub-agent again (you shouldn't).
- Do NOT re-run the tailor, cover-letter, or interview-coach sub-agents. The point of this flow is that the student's manual edits are authoritative.
- After rendering, print the output path and file size:
  ```
  Re-rendered resume.pdf ({size} bytes). Your edits are in the PDF.
  ```
- If the `.md` file still contains `[INSERT ...]` placeholders, warn the student before rendering: "Your edited markdown still has N `[INSERT ...]` placeholders. Render anyway, or do you want to fill them first?"

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

If the student opted in during Phase 0 first-run setup, the orchestrator fires
telemetry events at 8 pipeline boundaries. The `resumasher-telemetry-log`
script is a no-op when `config.json` has `"telemetry": "off"` (which is the
default), so it's safe to call unconditionally.

**Do not block on telemetry.** The log script is `set -uo pipefail` (no `-e`)
and exits 0 on any internal error. Telemetry failures never surface to the
student.

**Sync behavior.** The log script writes to a local JSONL file on every call.
The HTTP sync to Supabase only fires when event_type is "terminal":
`first_run_setup_completed`, `run_completed`, `run_failed`, or `rerender_used`.
Terminal events flush the whole queue of mid-run events in a single POST.
Measured against the live Supabase Ireland backend:

- Mid-run events (`run_started`, `fit_computed`, `tailor_completed`,
  `placeholder_fill_choice`): ~30ms per call (write-only, imperceptible).
- Terminal events: ~500ms (flushes the batch in one round-trip).

A typical full run costs ~1.6s of telemetry latency total, concentrated at
Phase 0 end and Phase 9 end where a half-second pause reads as "saving"
rather than "why is this hanging."

If the student kills the process mid-run before a terminal event fires,
queued mid-run events sit in the JSONL file and ship on the next run via
cursor-based catch-up. "Best of our abilities" — no shutdown hooks across
three host CLIs.

### Run correlation

At the start of Phase 1, generate a `run_id` (UUID v4) and save it so all
events from the run share the same ID:

```bash
RUN_ID=$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid)
mkdir -p "$STUDENT_CWD/.resumasher/run"
echo "$RUN_ID" > "$STUDENT_CWD/.resumasher/run/run-id.txt"
```

Subsequent phases read `$STUDENT_CWD/.resumasher/run/run-id.txt` to get the
run_id.

### The 8 call-sites

Resolve `$TEL` once per Bash tool call, just like `$RS`:

```bash
TEL="$RS_DIR/bin/resumasher-telemetry-log"
```

**Every call-site below should include both `--host "$HOST"` and `--model "$MODEL"`.** `$HOST` is the AI CLI you're running in (one of `claude_code`, `codex_cli`, `gemini_cli`); `$MODEL` is your own model identifier. You substitute both as literal strings — you know what CLI you are and what model you are. Examples by host:

- Claude Code: `--host claude_code --model claude-opus-4-7` (or `claude-sonnet-4-6`, `claude-haiku-4-5`)
- Codex CLI: `--host codex_cli --model gpt-5-codex` (or `gpt-5`, `gpt-5-mini`)
- Gemini CLI: `--host gemini_cli --model gemini-2.5-pro` (or `gemini-2.5-flash`)

If you genuinely don't know the model ID, omit `--model` (null is better than fabricated). Same rule for `--host`: omit rather than guess. The edge function caps both at 40 chars; no enum validation on model (space moves too fast), but host should match the three canonical values above.

**Phase 0 (end) — first_run_setup_completed.** Fired right after config.json
is written:

```bash
"$TEL" --event-type first_run_setup_completed --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --setup-duration "$SETUP_DURATION_SECONDS" \
  --setup-outcome completed \
  --style "$STYLE" \
  --photo-included "$PHOTO_INCLUDED" \
  --github-configured "$GITHUB_CONFIGURED"
```

`install_scope_path` is auto-detected by the log script from the skill's own
installation path — user-scope (`$HOME/.claude/skills/`, `.codex/skills/`, or
`.gemini/skills/`) → `user_home`; anywhere else → `project_local`. No orchestrator
substitution needed. `$SETUP_DURATION_SECONDS` is time elapsed since the consent
prompt started.

**Phase 1 (start) — run_started.** Fired right after `parse-job-source` and
`discover-resume` succeed:

```bash
"$TEL" --event-type run_started --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --run-id "$RUN_ID" \
  --jd-source-mode "$JD_MODE" \
  --resume-format "$RESUME_FORMAT"
```

`$JD_MODE` is the `mode` field from `parse-job-source` output (`file`, `url`,
or `literal`). `$RESUME_FORMAT` is one of `resume_md`, `resume_pdf`, `cv_md`,
`cv_pdf` based on the `discover-resume` filename.

**Phase 3 (end) — fit_computed.** Fired after fit-assessment.md is written
and the extract-* commands have pulled the structured fields:

```bash
FIT_SCORE=$("$RS" orchestration extract-fit-score < "$OUT_DIR/fit-assessment.md")
COMPANY=$("$RS" orchestration extract-company < "$OUT_DIR/fit-assessment.md")
ROLE=$("$RS" orchestration extract-role < "$OUT_DIR/fit-assessment.md")
SENIORITY=$("$RS" orchestration extract-seniority < "$OUT_DIR/fit-assessment.md")
STRENGTHS_COUNT=$("$RS" orchestration extract-strengths-count < "$OUT_DIR/fit-assessment.md")
GAPS_COUNT=$("$RS" orchestration extract-gaps-count < "$OUT_DIR/fit-assessment.md")
RECOMMENDATION=$("$RS" orchestration extract-recommendation < "$OUT_DIR/fit-assessment.md")

"$TEL" --event-type fit_computed --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --run-id "$RUN_ID" \
  --fit-score "$FIT_SCORE" \
  --fit-strengths-count "$STRENGTHS_COUNT" \
  --fit-gaps-count "$GAPS_COUNT" \
  --fit-recommendation "$RECOMMENDATION"
```

**Phase 5 (end) — tailor_completed.** Fired after tailored-resume.md is
written:

```bash
NUM_PLACEHOLDERS=$(grep -c '\[INSERT' "$OUT_DIR/tailored-resume.md" 2>/dev/null || echo 0)
USED_MULTIROLE=$(grep -q 'sub-role\|- \*\*.*\*\* ·' "$OUT_DIR/tailored-resume.md" && echo true || echo false)

"$TEL" --event-type tailor_completed --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --run-id "$RUN_ID" \
  --num-placeholders "$NUM_PLACEHOLDERS" \
  --used-multirole-format "$USED_MULTIROLE"
```

**Phase 7 (per placeholder) — placeholder_fill_choice.** Fired after EACH
placeholder is resolved (once per student answer):

```bash
"$TEL" --event-type placeholder_fill_choice --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --run-id "$RUN_ID" \
  --choice-type "$CHOICE"
```

`$CHOICE` is one of `specifics`, `soften`, `drop`.

**Phase 9 (end) — run_completed.** Fired after all PDFs render and history
is appended. Include all the fields from the fit event plus configuration:

```bash
DURATION=$(( $(date +%s) - $START_TS ))

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
  --num-placeholders "$NUM_PLACEHOLDERS" \
  --used-multirole-format "$USED_MULTIROLE" \
  --style "$STYLE" \
  --photo-included "$PHOTO_INCLUDED" \
  --github-configured "$GITHUB_CONFIGURED" \
  --used-github-evidence "$USED_GITHUB_EVIDENCE" \
  --used-folder-evidence true \
  --github-repos-count "$GITHUB_REPOS_COUNT" \
  --folder-files-count "$FOLDER_FILES_COUNT" \
  --all-pdfs-rendered "$ALL_PDFS_RENDERED"
```

**Any phase (failure) — run_failed.** Fired from the hard-stop path of any
phase. Include whatever fields are already known at that point:

```bash
"$TEL" --event-type run_failed --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --run-id "$RUN_ID" \
  --duration "$DURATION" \
  --failed-phase "$PHASE_NUMBER" \
  --error-class "$ERROR_CLASS" \
  ${COMPANY:+--company "$COMPANY"} \
  ${ROLE:+--job-title "$ROLE"} \
  ${SENIORITY:+--seniority "$SENIORITY"}
```

`$ERROR_CLASS` comes from a pre-declared enum: `no_resume`, `non_english_jd`,
`folder_miner_failed`, `fit_analyst_failed`, `tailor_failed`, `pdf_render_failed`,
`timeout`, `unknown`.

**Re-render flow — rerender_used.** Fired when a student invokes the
"re-render the PDF" shortcut from the "Re-rendering PDFs after manual edits"
section:

```bash
"$TEL" --event-type rerender_used --cwd "$STUDENT_CWD" \
  --host "$HOST" \
  --model "$MODEL" \
  --rerender-kind "$KIND"
```

`$KIND` is one of `resume`, `cover`, `prep`.

### Field whitelist

The edge function validates every event against a fixed schema and silently
drops anything that doesn't match. Don't add new `--flag` values without
also adding the matching column + whitelist entry to
`supabase/migrations/001_telemetry.sql` and
`supabase/functions/telemetry-ingest/index.ts`.

---

## Debugging this skill

If a student reports something wrong with a resumasher output — "my name's
missing from the PDF," "the photo looks squished," "the sections are in
the wrong order," or even a vague "something looks off" — follow this
playbook. Do NOT just apologize or guess. The artifacts on disk plus the
inspection helpers will tell you exactly what happened.

This playbook is agent-first: it assumes you (the AI CLI running
resumasher) are right there in the same session, with full tool access.
You are the diagnostic tool. The student only has to describe what they
see.

### Step 1 — Find the artifacts

The student's most recent run lives in `applications/<company-slug>-<date>/`
at their working directory. Key files:

- `tailored-resume.md` — what the tailor produced (source of truth)
- `resume.pdf` — what the student actually got
- `cover-letter.md` + `cover-letter.pdf`
- `interview-prep.md` + `interview-prep.pdf`
- `fit-assessment.md`, `company-research.md`
- `jd.md` (persisted as of v0.4, issue #15) — the JD this run targeted

If multiple application folders exist, pick the most recent by mtime or
by the folder name's date suffix. Ask the student if it's ambiguous.

### Step 2 — Read the student's report literally

Quote the student's own words in your internal reasoning. Don't
paraphrase into your own technical vocabulary before you've investigated.
"Something's weird with the photo" is different from "the photo is
stretched" is different from "the photo is the wrong photo." Each points
at a different failure.

### Step 3 — Inspect the artifacts

Use `scripts/orchestration.py inspect` to get structured JSON views:

```bash
"$RS" orchestration inspect --resume "$OUT_DIR/tailored-resume.md"
"$RS" orchestration inspect --pdf    "$OUT_DIR/resume.pdf"
"$RS" orchestration inspect --photo  "<path-to-source-photo>"
```

Each command returns JSON with counts, contents, and light warnings for
the most common bug signatures. The `warnings` field flags:

- `EMPTY_NAME` / `EMPTY_CONTACT_LINE` — parser found no candidate name
- `ORPHANED_BULLETS` — bullets floating at the end of a section with
  title-like paragraphs stacked before them
- `PHOTO_ASPECT_STRETCH` — source photo aspect differs from render box

Warnings are shortcuts, not the full story. Read the raw parse tree too —
section counts, block structure, paragraph previews — because novel bugs
won't trip any warning but their signature will be visible in the data.

### Step 4 — Match against known failure modes

Read `docs/KNOWN_FAILURE_MODES.md`. It lists every catalogued bug with:

- Symptom description
- Signature (how to detect it from the inspection output)
- Root cause
- Where in the code the fix lives
- Reference repro in `examples/`

If the student's symptom + inspection signature match an entry, you have
a confident hypothesis without further investigation. State the match
clearly: "This matches KNOWN_FAILURE_MODES.md #2 (orphaned bullets)."

If nothing matches, proceed to Step 5.

### Step 5 — Investigate novel bugs

For unknown failures, do the usual root-cause work:

- Read `scripts/render_pdf.py` and trace the path from markdown input to
  the broken output
- Check recent commits on the relevant files for regressions
- Cross-reference with git log, CHANGELOG entries, and prior learnings
- Form a hypothesis and verify it by reading the code (don't guess)

After diagnosis, add a new entry to `docs/KNOWN_FAILURE_MODES.md` so the
next agent (and the next student) benefits. Save the repro pair to
`examples/<short-bug-name>/` (already gitignored).

### Step 6 — Write the bug report

Use the template in `docs/BUG_REPORT_TEMPLATE.md`. Fill in:

- What the student reported (verbatim)
- What you found (specific evidence from inspection)
- Match to known failure mode, or "novel"
- Environment (version, host, model, style, Python, OS)
- Anonymized artifacts (see anonymization guide in the template)
- Suggested fix location (from KNOWN_FAILURE_MODES.md or your own
  investigation)

Anonymize the easy tier by default: name, email, phone, LinkedIn,
GitHub username. Pull these from the student's `.resumasher/config.json`
if available, or from the markdown directly. Leave company names, metrics,
project names, and technical keywords intact unless the student
specifically asks otherwise — these are load-bearing for reproduction.

### Step 7 — File it (with consent)

Show the final report to the student. Read back the redactions you
applied. Ask explicitly: "Ready to file this as a GitHub issue? I can
submit it for you via `gh issue create`." Only proceed if they say yes.

```bash
gh issue create --repo earino/resumasher \
  --title "<concise-symptom>" \
  --label bug \
  --body-file "$OUT_DIR/bug-report.md"
```

Give the student the resulting issue URL so they can follow it.

### What NOT to do

- **Don't build a pre-flight `--debug` mode.** The playbook is post-hoc
  and runs against artifacts already on disk. You don't need the student
  to re-run anything.
- **Don't paste raw parse-tree JSON as the bug report.** That's input to
  your diagnosis, not output to the maintainer. Write prose that names
  the specific failure.
- **Don't auto-file.** Always get explicit consent before
  `gh issue create` — the student owns their data.
- **Don't anonymize company names or metrics without asking.** These are
  needed for reproduction. Only redact if the student flags them as
  sensitive.
