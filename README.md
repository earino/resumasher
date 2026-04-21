# resumasher

[![CI](https://github.com/earino/resumasher/actions/workflows/ci.yml/badge.svg)](https://github.com/earino/resumasher/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/earino/resumasher/blob/main/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Tests: 256 passing](https://img.shields.io/badge/tests-256%20passing-brightgreen.svg)](https://github.com/earino/resumasher/tree/main/tests)

resumasher tailors your resume, writes a cover letter, and builds an interview prep bundle for a specific job. It runs as an [Agent Skill](https://github.com/anthropics/skills) inside your AI CLI (**Claude Code**, **OpenAI Codex CLI**, or **Google Gemini CLI**), reading your actual work to back every claim with concrete evidence.

![resumasher running: terminal walkthrough from `/resumasher job.md` through fit assessment, tailor, placeholder fill, and PDF render](assets/img/demo.gif)

## Quick install

Paste this into Claude Code, Codex CLI, or Gemini CLI:

> Install the resumasher skill available at https://github.com/earino/resumasher

The AI CLI reads the README, picks the right path for your CLI, clones, and runs the installer. For exact per-CLI commands or project-scope install, see [Install](#install) below.

## What you get

From your resume folder, run:

```bash
/resumasher job.md
```

A few minutes later you get `./applications/<company>-<date>/` containing:

| File | What it is |
|---|---|
| `resume.pdf` | Tailored resume, ATS-safe, single column, EU or US style |
| `cover-letter.pdf` | 3-paragraph cover letter weaving in recent company news |
| `interview-prep.pdf` | Likely SQL, case, and behavioral questions with draft answers pulled from your actual projects |
| `fit-assessment.md` | Honest fit score (0-10) with strengths and gaps. Not a pep talk. |
| `company-research.md` | 3-5 recent facts about the company with citations |
| `tailored-resume.md`, `cover-letter.md`, `interview-prep.md` | Markdown sources (edit and re-render) |

## The unfair advantage: it sees your actual work

Every other resume-tailoring tool is a web app that only sees the summary you paste in. resumasher runs inside your AI CLI, so it pulls from two evidence sources the web tools cannot reach:

**Your public GitHub.** One-time setup, then every run mines your non-fork repos: names, descriptions, topics, README content, last-push date. For most students this is where the evidence lives, especially on a borrowed or clean laptop.

**Your working directory.** If you keep project files locally (capstone code, ML notebooks, text-mining writeups, PDF reports), resumasher reads those too and cites specific files.

Your bullet becomes: "Built an XGBoost churn classifier on 2.3M rows, F1=0.82, deployed to Flask. See `github.com/you/churn-model`" instead of "built a machine learning model."

## Install

resumasher is an [Agent Skills](https://github.com/anthropics/skills) package. If your AI CLI asks "is this a plugin," the answer is no, it's a skill. Each host has its own skill directory convention (`.claude/skills/`, `.codex/skills/`, `.gemini/skills/`) but the skill source is identical. Pick the block that matches your AI CLI.

**⚠️ `install.sh` is mandatory on every host.** `git clone` alone only copies files. It does NOT create the Python virtual environment or install the required packages (reportlab, pdfminer.six, chardet, nbconvert, Pillow). If you skip `install.sh`, the next invocation of `/resumasher` will crash with `ModuleNotFoundError: No module named 'reportlab'` and you'll think the skill is broken.

### Claude Code

**User-scope, recommended** (skill available in every folder):

```bash
git clone https://github.com/earino/resumasher.git ~/.claude/skills/resumasher
bash ~/.claude/skills/resumasher/install.sh
```

**Project-scope** (skill available only in the current folder — use when you want the skill checked in alongside a specific job-search project):

```bash
git clone https://github.com/earino/resumasher.git .claude/skills/resumasher
bash .claude/skills/resumasher/install.sh
```

Restart Claude Code, then run `/resumasher <job>` from a folder with your `resume.md` or `resume.pdf`.

### OpenAI Codex CLI

**User-scope, recommended:**

```bash
git clone https://github.com/earino/resumasher.git ~/.codex/skills/resumasher
bash ~/.codex/skills/resumasher/install.sh
```

**Project-scope** (only when you want the skill scoped to one folder):

```bash
git clone https://github.com/earino/resumasher.git .codex/skills/resumasher
bash .codex/skills/resumasher/install.sh
```

Restart Codex, then run `/resumasher <job>` from a folder with your `resume.md` or `resume.pdf`.

### Google Gemini CLI

Gemini CLI has a first-class `skills install` subcommand that handles the clone for you:

```bash
gemini skills install --user https://github.com/earino/resumasher    # user-scope, recommended
gemini skills install https://github.com/earino/resumasher           # project-scope (only when scoped to one folder is what you want)
```

Gemini will prompt you to confirm before installing. After it finishes, run the Python installer once:

```bash
bash ~/.gemini/skills/resumasher/install.sh        # user-scope
bash .gemini/skills/resumasher/install.sh          # project-scope
```

Restart Gemini, then run `/resumasher <job>` from a folder with your `resume.md` or `resume.pdf`.

### Verify the install

From a fresh AI CLI session, try the bundled fixtures:

```bash
cd <install-dir>/GOLDEN_FIXTURES    # e.g. ~/.claude/skills/resumasher/GOLDEN_FIXTURES
/resumasher sample-jd.md
```

A few minutes later you should see three PDFs in `./applications/deloitte-consulting-<today>/`. Wall-clock time depends on the LLM in use, GitHub fetch latency, and your network.

### For your AI CLI: authoritative install instructions

<details>
<summary>Expand (instructions for AI CLIs reading this page. Students can skip.)</summary>

**If a user asks their AI CLI (Claude Code, Codex, or Gemini) to install this skill, the CLI should follow the matching block above.** Pick the block for your host, run both commands, done. Do not attempt to install this as a Claude Code plugin. There is no `.claude-plugin/` manifest and the repo is not a plugin marketplace. It's a skill. The skill directory conventions differ per host but the skill source is identical.

</details>

## Usage

### Invocation

Three input forms, all work:

```bash
/resumasher job.md                                               # local file
/resumasher https://careers.deloitte.com/job/12345               # URL
/resumasher "Senior Data Analyst at Acme Corp. Requirements: SQL, Python..."  # literal text
```

### First-run setup (one time per folder)

The first time you run `/resumasher` in a folder, it asks for your contact info, default resume style (EU or US), whether to include a photo by default, and (last question) whether to opt into anonymous usage analytics. Short one-time setup. Your contact info and application history are stored locally in `.resumasher/` — never uploaded. The analytics tier defaults to off; if you opt in, see [PRIVACY.md](PRIVACY.md) for exactly what gets sent and what doesn't.

### Accepted resume formats

resumasher looks for these files in the working directory, in priority order:

1. `resume.md` / `resume.markdown`
2. `cv.md` / `CV.md`
3. `resume.pdf` / `Resume.pdf`
4. `cv.pdf` / `CV.pdf`

**Markdown is preferred** because it's the source-of-truth you should be editing anyway (diff-friendly, easy to update, no rendering stack needed). If both a `.md` and a `.pdf` exist, the `.md` wins.

**PDF works if that's all you have.** resumasher extracts the selectable text via `pdfminer.six` and hands it to the tailor sub-agent. Caveats:

- Scanned / image-only PDFs will fail with a clear error. resumasher does not OCR.
- PDF text extraction loses some structure (columns, tables). The tailor will restructure it, but results are cleaner from a `resume.md`.
- If you want to keep iterating, export your `tailored-resume.md` from the first run as your new base. Future runs will be markdown-driven.

### Folder layout

```
my-job-search/
├── resume.md            # Your base resume (see formats above)
├── photo.jpg            # Optional, for EU-style resumes
├── applications/        # resumasher writes PDFs here
└── projects/            # Your work: code, notebooks, READMEs, PDFs
    ├── capstone/
    ├── ml-final/
    └── text-mining/
```

See `GOLDEN_FIXTURES/` in this repo for a full example.

### Iterating in the same folder

Each run's JD file sits alongside your resume. If you apply to several roles from one folder, delete or archive the old JD file before the next run, or put each JD in its own subfolder. Otherwise the folder miner picks up every JD you've tried and hands them to the tailor as context, wasting tokens and confusing the sub-agent.

### GitHub profile (optional, auto-used when configured)

If your work lives on GitHub more than on your current laptop, or you're applying from a borrowed machine, resumasher can mine your public GitHub profile for evidence. Setup is one prompt at first-run: *"Do you have a GitHub? We can leverage it for this."* Paste your username (or a profile URL, we strip the prefix), and every subsequent run automatically mixes your repos into the evidence pool.

What resumasher fetches per repo: name, description, topics, primary language, last push date, stargazer count, README content (up to 50KB).

What it skips: forks, archived repos, empty repos, source code (too noisy), issues, PRs, contribution graphs. Default cap is 15 most-recently-pushed repos.

**Auth and rate limits.** resumasher uses the GitHub CLI (`gh api`) if it's installed and authenticated, giving you a 5000/hour rate limit and reusing your existing auth with zero PAT handling. Without `gh`, it falls back to unauthenticated requests (60/hour), enough for small profiles but tight for anything bigger. If you hit the limit, resumasher prints a clear message and continues without GitHub evidence. To unlock the 5000/hour limit:

```bash
brew install gh   # or see https://cli.github.com
gh auth login
```

**One-off override.** For a borrowed laptop or an alternate account, pass `--github <username>` on the command line. It beats whatever's in your config for that single run.

**Caching.** GitHub responses are cached for 1 hour under `.resumasher/github-cache/<username>.json`. Iterate on the same JD multiple times without re-hitting the API. Delete the file to force a refresh.

### Flags

```bash
/resumasher <job> --style us       # US style (no photo, different section order)
/resumasher <job> --style eu       # EU style (photo optional)
/resumasher <job> --photo me.jpg   # Override photo path
/resumasher <job> --no-photo       # Suppress photo for this run
```

`--style` always wins over `--photo`. US-style resumes never include a photo.

## Updating an existing install

Three commands in the skill's install directory: `git pull` to fetch new code, `bash install.sh` to refresh the venv if `requirements.txt` changed (idempotent if it didn't), then restart the AI CLI so the updated `SKILL.md` gets picked up.

Pick the block matching the AI CLI you're running in. Each block prefers the user-scope install (`~/.<host>/skills/`) and falls back to project-scope (`.<host>/skills/`) if only the latter exists.

### Claude Code

```bash
if [ -d ~/.claude/skills/resumasher/.git ]; then
  cd ~/.claude/skills/resumasher
elif [ -d .claude/skills/resumasher/.git ]; then
  cd .claude/skills/resumasher
else
  echo "resumasher is not installed for Claude Code. See Install section above."; exit 1
fi
git pull
bash install.sh
```

Then restart Claude Code.

### OpenAI Codex CLI

```bash
if [ -d ~/.codex/skills/resumasher/.git ]; then
  cd ~/.codex/skills/resumasher
elif [ -d .codex/skills/resumasher/.git ]; then
  cd .codex/skills/resumasher
else
  echo "resumasher is not installed for Codex. See Install section above."; exit 1
fi
git pull
bash install.sh
```

Then restart Codex.

### Google Gemini CLI

```bash
if [ -d ~/.gemini/skills/resumasher/.git ]; then
  cd ~/.gemini/skills/resumasher
elif [ -d .gemini/skills/resumasher/.git ]; then
  cd .gemini/skills/resumasher
else
  echo "resumasher is not installed for Gemini. See Install section above."; exit 1
fi
git pull
bash install.sh
```

Then restart Gemini.

### For your AI CLI: authoritative update instructions

<details>
<summary>Expand (instructions for AI CLIs reading this page. Students can skip.)</summary>

**When a user asks you (their AI CLI) to update resumasher**, run the detection-and-update bash block above for the AI CLI you are currently running in. Only update the install directory matching the AI CLI you are running in; do not touch installs for other hosts. After the commands complete, tell the user to restart this specific AI CLI (not a generic "restart your AI CLI").

If the detection block exits with "resumasher is not installed," do not guess. Tell the user resumasher is not installed for this AI CLI and point them at the Install section above. Do not silently fall back to installing from scratch.

</details>

## ATS safety

Every generated PDF passes `pdfminer.six` round-trip extraction. We've also manually verified the output through Jobscan's free parser to confirm section detection.

**Before applying through a major ATS** (Workday, Taleo, iCIMS), upload your `resume.pdf` to [jobscan.co](https://www.jobscan.co/) (free preview) with the JD pasted in, and eyeball that sections parse the way you'd expect.

## Something looks wrong?

resumasher runs inside your AI CLI and the agent can investigate its own output. If a PDF looks off — missing content, a stretched photo, sections in a weird order, anything — stay in the same chat and describe what you see in plain English. The agent will read the artifacts, match your symptom against [known failure modes](docs/KNOWN_FAILURE_MODES.md), and draft a bug report you can review and file (with your contact info redacted).

No commands to remember, no `--debug` flag. Just tell your AI CLI what's wrong.

## Architecture

The skill runs a nine-phase pipeline: first-run setup → intake → folder + GitHub mine → fit analysis → company research → tailor → parallel cover-letter + interview-prep → interactive placeholder fill → PDF render → log and summary.

Sub-agents dispatch via each host's subagent mechanism (Claude's `Task` with `subagent_type="general-purpose"`, Gemini's `@generalist`, or Codex's inline execution). Interactive prompts use each host's native tool (`AskUserQuestion` / `request_user_input` / `ask_user`) with a hard-fail fallback for non-interactive contexts.

The LLM pipeline runs prose between phases (no JSON), with small sentinel lines (`FIT_SCORE: 7`, `COMPANY: Deloitte`, `FAILURE: ...`) where structure actually matters. Job descriptions and company-research output are wrapped in `<<<UNTRUSTED_*>>>` markers before reaching sub-agents with file or web access. Basic prompt-injection containment.

```
resumasher/
├── SKILL.md                # Orchestration prompt the AI CLI follows at runtime
├── bin/
│   └── resumasher-exec     # Self-locating wrapper around venv Python
├── scripts/
│   ├── orchestration.py    # Deterministic helpers (CLI + importable)
│   ├── prompts.py          # All 6 sub-agent prompt templates + substitution
│   ├── render_pdf.py       # Pure-Python PDF renderer (reportlab + DejaVu Sans)
│   └── github_mine.py      # GitHub profile evidence fetcher
├── assets/
│   ├── DejaVuSans.ttf
│   └── DejaVuSans-Bold.ttf
├── docs/DESIGN.md          # Design rationale (read before a large PR)
├── GOLDEN_FIXTURES/        # Sample portfolio for testing and demo
├── tests/                  # pytest suite
├── install.sh              # One-liner installer + venv setup
└── requirements.txt
```

## Usage analytics

resumasher can optionally send anonymous usage data to help the maintainer see what's breaking and what students actually use. **Default is off.** You're asked once during first-run setup; change anytime with `resumasher telemetry set-tier <off|anonymous|community>`.

Three tiers:

- **Off** (default): nothing logged, nothing sent.
- **Anonymous**: event data sent without an installation identifier. Individual runs cannot be correlated.
- **Community**: same data plus a random UUID so the maintainer can see "one user is hitting this bug three times in a row" vs "three unrelated users".

See [PRIVACY.md](PRIVACY.md) for the complete list of what's logged and what isn't. Highlights: no resume content, no JD text, no names, no GitHub usernames, no email addresses. Data is stored on Supabase in the Ireland region (eu-west-1) and retained for 90 days.

```bash
resumasher telemetry status             # Show tier, installation ID, log size
resumasher telemetry export             # See everything that's been logged locally
resumasher telemetry delete             # Wipe local data + backend data for your ID
resumasher telemetry set-tier anonymous # Change tier
```

## Development

```bash
# Run the test suite (256 tests, ~5 seconds)
source .venv/bin/activate
pytest tests/ -v

# Try the skill on the bundled fixtures
cd GOLDEN_FIXTURES
/resumasher sample-jd.md
```

Before opening a PR:

- `pytest tests/ -v` should pass.
- If you change rendering logic, regenerate the `GOLDEN_FIXTURES` output and eyeball it through [jobscan.co](https://www.jobscan.co/) to confirm ATS parsing still works.
- For larger changes, read [`docs/DESIGN.md`](https://github.com/earino/resumasher/blob/main/docs/DESIGN.md) first. It captures why the skill is shaped the way it is (prose between LLM phases, pure-Python PDF, deterministic prompt substitution) so you can propose changes that work with the design rather than against it.

## Roadmap

**v0.1 (shipped):**
- EU and US resume styles, ATS-safe single-column layout
- English-only JD input (pasted, file, or URL)
- Nine-phase pipeline with prompt-injection containment and ATS round-trip gate
- Multi-role tenures rendered correctly (e.g., Meta progression shown as one company entry with sub-role bullets)
- Photos auto-downscaled to keep output PDFs under 200KB
- `resume.pdf` accepted when no markdown source exists
- GitHub profile mining (`gh api` preferred, unauthenticated fallback)
- `[INSERT ...]` placeholder pattern with interactive fill-in (Specifics / Soften / Drop per bullet)
- Local application history log (`.resumasher/history.jsonl`)
- Runs on Claude Code, OpenAI Codex CLI, and Google Gemini CLI

**v0.2 (shipped):**
- Opt-in usage analytics with three-tier consent (off / anonymous / community), default off, GDPR-compliant ([#2](https://github.com/earino/resumasher/issues/2)). Supabase backend in Ireland. Student-facing CLI: `resumasher telemetry status / export / delete / set-tier`. Full detail in [PRIVACY.md](PRIVACY.md).
- Fit-analyst emits structured sentinels (`ROLE:`, `SENIORITY:`, `STRENGTHS_COUNT:`, `GAPS_COUNT:`, `RECOMMENDATION:`) with multilingual seniority classification (any language the LLM understands).

**v0.3 (shipped):**
- Non-English resume filename detection ([#3](https://github.com/earino/resumasher/issues/3)). Students whose resume lives as `Lebenslauf.md`, `履歴書.md`, `cv_francais.md`, or `my_resume_final_v3.md` no longer hit a terminal "no resume found" error — when auto-discovery misses, the skill asks once and validates the answer.
- GitHub Actions CI with PDF round-trip on every push ([#8](https://github.com/earino/resumasher/issues/8)). Full pytest suite (220 tests) runs on Python 3.10, 3.11, 3.12 on every push and PR. Failed runs upload the generated PDFs as debug artifacts.
- Live community stats dashboard at [earino.github.io/resumasher/stats](https://earino.github.io/resumasher/stats/). Aggregate metrics from opt-in community telemetry: runs per day, host distribution, model mix, fit score histogram, failures by phase. No per-user data exposed.

**Planned (shaped by early user feedback):**
- `--review` mode: step-by-step interactive rewriting for every bullet, not just placeholders ([#11](https://github.com/earino/resumasher/issues/11))
- Final coherence pass flagging cross-document drift before PDF render ([#1](https://github.com/earino/resumasher/issues/1))
- Incremental folder-mine cache invalidation ([#10](https://github.com/earino/resumasher/issues/10))
- German / French JD translation pre-pass ([#7](https://github.com/earino/resumasher/issues/7))
- Facts persistence: remember placeholder-fill answers across runs ([#9](https://github.com/earino/resumasher/issues/9))

## Contributing

PRs and issues welcome. resumasher is explicitly shaped by feedback from early users: what surprised you, what looked wrong, what you wish the tool had caught. File anything that helped or bit you.

## License

MIT. See [LICENSE](https://github.com/earino/resumasher/blob/main/LICENSE). Fork it, extend it, ship it to your students.

## Credits

Built by [Eduardo Ariño de la Rubia](https://github.com/earino) for his wonderful students, and anyone else who may find it useful.

Designed with [gstack](https://github.com/garrytan/gstack) (office-hours and plan-eng-review skills) and built with [Claude Code](https://claude.com/claude-code).
