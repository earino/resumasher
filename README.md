# resumasher

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Tests: 101 passing](https://img.shields.io/badge/tests-101%20passing-brightgreen.svg)](tests/)

A cross-host AI-CLI skill that tailors your resume + writes a cover letter + builds an interview prep bundle for a specific job, by mining the evidence already in your working directory.

Built for an MS Business Analytics cohort, but the shape generalizes to any student with a portfolio of code, notebooks, and PDF reports.

**TL;DR:** Every other AI resume tool is a web app that only sees the summary you paste in. resumasher reads your actual work — your public GitHub repos and any project files in the working directory — and cites concrete evidence in your resume. Generic bullets become source-backed claims.

## This is a skill, not a plugin

resumasher is an [Agent Skills](https://github.com/anthropics/skills) package. The SKILL.md-based spec is cross-ecosystem — the same skill runs on **Claude Code**, **OpenAI Codex CLI**, and **Google Gemini CLI** with no per-host customization. If your AI CLI asks "is this a plugin," the answer is no — it's a skill. Install commands below.

## Install

Pick the block that matches your AI CLI. Each host has its own skill directory convention but the install is otherwise the same: clone, then run the mandatory `install.sh` to set up the Python venv.

**⚠️ `install.sh` is mandatory on every host.** `git clone` alone only copies files — it does NOT create the Python virtual environment or install the required packages (reportlab, pdfminer.six, chardet, nbconvert, Pillow). If you skip `install.sh`, the next invocation of `/resumasher` will crash with `ModuleNotFoundError: No module named 'reportlab'` and you'll think the skill is broken.

### Claude Code

**Project-scope (skill available only in the current folder):**

```bash
git clone https://github.com/earino/resumasher.git .claude/skills/resumasher
bash .claude/skills/resumasher/install.sh
```

**User-scope (skill available in every folder):**

```bash
git clone https://github.com/earino/resumasher.git ~/.claude/skills/resumasher
bash ~/.claude/skills/resumasher/install.sh
```

Restart Claude Code so the skill is discovered. Then run `/resumasher <job>` from inside the folder with your `resume.md` or `resume.pdf`.

### OpenAI Codex CLI

**Project-scope:**

```bash
git clone https://github.com/earino/resumasher.git .codex/skills/resumasher
bash .codex/skills/resumasher/install.sh
```

**User-scope:**

```bash
git clone https://github.com/earino/resumasher.git ~/.codex/skills/resumasher
bash ~/.codex/skills/resumasher/install.sh
```

Restart Codex so the skill is discovered. Then run `/resumasher <job>` from inside the folder with your `resume.md` or `resume.pdf`.

### Google Gemini CLI

Gemini CLI has a first-class `skills install` subcommand that handles the clone for you:

```bash
gemini skills install https://github.com/earino/resumasher   # project-scope (workspace)
# or:
gemini skills install --user https://github.com/earino/resumasher
```

Gemini will prompt you to confirm before installing. After it finishes, you still need to run the Python installer once:

```bash
bash .gemini/skills/resumasher/install.sh          # project-scope
# or:
bash ~/.gemini/skills/resumasher/install.sh        # user-scope
```

Restart Gemini so the skill is discovered. Then run `/resumasher <job>` from inside the folder with your `resume.md` or `resume.pdf`.

### Updating an existing install

Same pattern on all three hosts — `cd` into wherever the skill was cloned, pull, re-run `install.sh` in case dependencies changed:

```bash
# Pick whichever path matches your install:
cd ~/.claude/skills/resumasher      # or .claude/skills/resumasher
cd ~/.codex/skills/resumasher       # or .codex/skills/resumasher
cd ~/.gemini/skills/resumasher      # or .gemini/skills/resumasher

git pull
bash install.sh
```

Then restart your AI CLI.

## For your AI CLI — authoritative install instructions

**If a user asks their AI CLI (Claude Code, Codex, or Gemini) to install this skill, the CLI should follow the matching block above.** Pick the block for your host, run both commands, done. Do not attempt to install this as a Claude Code plugin — there is no `.claude-plugin/` manifest and the repo is not a plugin marketplace. It's a skill. The skill directory conventions differ per host (`.claude/skills/`, `.codex/skills/`, `.gemini/skills/`) but the skill source is identical.

## What it does

From inside your resume folder, run:

```bash
/resumasher job.md
/resumasher https://careers.deloitte.com/job/12345
/resumasher "Senior Data Analyst at Acme Corp. Requirements: SQL, Python..."
```

A few minutes later you get `./applications/<company>-<date>/` containing:

| File | What it is |
|---|---|
| `resume.pdf` | Tailored resume, ATS-safe, single column, EU or US style |
| `cover-letter.pdf` | 3-paragraph cover letter weaving in recent company news |
| `interview-prep.pdf` | Likely SQL / case / behavioral questions with draft answers pulled from your actual projects |
| `fit-assessment.md` | Honest fit score (0-10) with strengths and gaps — not a pep talk |
| `company-research.md` | 3-5 recent facts about the company with citations |
| `tailored-resume.md`, `cover-letter.md`, `interview-prep.md` | Markdown sources (edit and re-render) |

## The unfair advantage: it sees your actual work

Every other resume-tailoring tool is a web app that only sees the summary you paste in. resumasher runs inside your AI CLI (Claude Code, Codex, or Gemini), so it pulls from two evidence sources the web tools cannot reach:

**Your public GitHub.** One-time setup, then every run mines your non-fork repos — names, descriptions, topics, README content, last-push date. For most students this is where the evidence lives, especially on a borrowed or clean laptop.

**Your working directory.** If you keep project files locally — capstone code, ML notebooks, text-mining writeups, PDF reports — resumasher reads those too and cites specific files.

Your bullet becomes: "Built an XGBoost churn classifier on 2.3M rows, F1=0.82, deployed to Flask — see `github.com/you/churn-model`" instead of "built a machine learning model."

Competitors cannot do this. resumasher can because it's an AI-CLI skill, not a web form.

## Verify the install

From a fresh session of whichever AI CLI you installed into:

```bash
cd ~/.claude/skills/resumasher/GOLDEN_FIXTURES     # or the equivalent .codex / .gemini path
/resumasher sample-jd.md
```

A few minutes later you should see three PDFs in `./applications/deloitte-consulting-<today>/`. Wall-clock time depends on the LLM in use, GitHub fetch latency (if configured), and your network.

## What's in v0.1 (and improving)

Recent fixes you probably want (pull to pick them up):

- **Interactive placeholder fill** before PDFs are rendered — no more `[INSERT TEAM SIZE]` shipping in your resume by accident
- **Multi-role tenures render correctly** (Meta → Senior Director → Director → Manager as sub-bullets under one company entry, not three separate entries)
- **`bin/resumasher-exec` wrapper** — self-locating helper that resolves SKILL_ROOT and venv Python in one call
- **Intermediate files moved** from `/tmp/` to `$CWD/.resumasher/run/` (gitignored, scoped to your folder, privacy-safe on shared machines)
- **AskUserQuestion pattern fix** for free-text fields — one round of questions instead of two
- **`resume.pdf` accepted directly** (no manual markdown conversion needed)
- **`.claude/` folder ignored** during mining (skills don't mine themselves on project-scope installs)
- **Photos downscaled** before embedding (PDF sizes went from ~1MB to ~150KB)
- **`**bold**` markdown** rendered as bold in PDFs instead of literal asterisks
- **GitHub profile mining** during first-run setup
- **Resume length / recency / multi-role-tenure guidance** in the tailor prompt

## Usage

### First-run setup (one time per folder)

The first time you run `/resumasher` in a folder, it will ask for your contact info, default style (EU or US), and whether to include a photo by default. Short one-time setup.

Everything is stored locally in `.resumasher/config.json`. Nothing is uploaded.

### Folder layout it expects

```
my-job-search/
├── resume.md            # Your base resume (see below for accepted formats)
├── photo.jpg            # Optional, for EU-style resumes
├── applications/        # resumasher writes PDFs here
└── projects/            # Your work — code, notebooks, READMEs, PDFs
    ├── capstone/
    ├── ml-final/
    └── text-mining/
```

See `GOLDEN_FIXTURES/` in this repo for a full example.

**Tip when iterating in the same folder:** each run's JD file sits alongside your resume. If you apply to several roles from one folder, delete or archive the old JD file before the next run, or put each JD in its own subfolder. Otherwise the folder miner will pick up every JD you've tried and hand them to the tailor as context, which wastes tokens and can confuse the sub-agent.

### GitHub profile (optional, auto-used when configured)

If your work lives on GitHub more than on your current laptop — or you're applying from a borrowed machine — resumasher can mine your public GitHub profile for evidence. Setup is one prompt at first-run: *"Do you have a GitHub? We can leverage it for this."* Paste your username (or a profile URL, we strip the prefix), and every subsequent run automatically mixes your repos into the evidence pool.

What resumasher fetches per repo:

- Name, description, topics, primary language
- Last push date, stargazer count
- README content (up to 50KB, base64-decoded from the GitHub API)

What it deliberately skips: forks, archived repos, empty repos, source code (too noisy), issues, PRs, and contribution graphs. Default cap is 15 most-recently-pushed repos.

**Auth & rate limits.** resumasher uses the GitHub CLI (`gh api`) if it's installed and authenticated — that gives you a 5000/hour rate limit and reuses your existing auth with zero PAT handling. If `gh` isn't installed, resumasher falls back to unauthenticated requests (60/hour); enough for small profiles, tight for anything bigger. If you hit the limit, resumasher prints a clear message and keeps going without GitHub evidence. To unlock the 5000/hour limit:

```bash
brew install gh   # or see https://cli.github.com
gh auth login
```

**One-off override.** For a borrowed laptop or an alternate account, pass `--github <username>` on the command line — it beats whatever's in your config for that single run.

**Caching.** GitHub responses are cached for 1 hour under `.resumasher/github-cache/<username>.json`. Iterate on the same JD multiple times without re-hitting the API. Delete the file to force a refresh.

### Accepted resume formats

resumasher looks for these files in the working directory, in priority order:

1. `resume.md` / `resume.markdown`
2. `cv.md` / `CV.md`
3. `resume.pdf` / `Resume.pdf`
4. `cv.pdf` / `CV.pdf`

**Markdown is preferred** because it's the source-of-truth you should be editing anyway (diff-friendly, easy to update, no rendering stack needed). If both a `.md` and a `.pdf` exist, the `.md` wins.

**PDF works if that's all you have** — resumasher will extract the selectable text via `pdfminer.six` and hand it to the tailor sub-agent. Caveats:

- Scanned / image-only PDFs will fail with a clear error. resumasher does not OCR.
- PDF text extraction loses some structure (columns, tables). The tailor sub-agent will restructure it into the expected markdown schema, but results are cleaner if you start from a `resume.md`.
- If you want to keep iterating, export your tailored `tailored-resume.md` from the first run as your new base — future runs will be markdown-driven.

### Flags

```bash
/resumasher <job> --style us       # US style (no photo, different section order)
/resumasher <job> --style eu       # EU style (photo optional)
/resumasher <job> --photo me.jpg   # Override photo path
/resumasher <job> --no-photo       # Suppress photo for this run
```

`--style` always wins over `--photo`. US-style resumes never include a photo.

## ATS safety

Every generated PDF passes `pdfminer.six` round-trip extraction. We've also manually verified the output through Jobscan's free parser to confirm section detection.

**Before applying through a major ATS** (Workday, Taleo, iCIMS), we recommend uploading your `resume.pdf` to [jobscan.co](https://www.jobscan.co/) (free preview) with the JD pasted in, just to eyeball that sections parse the way you'd expect.

## Architecture

- `SKILL.md` — the orchestration prompt Claude follows when you invoke `/resumasher`. Nine phases from first-run setup through log + summary.
- `bin/resumasher-exec` — self-locating bash wrapper. Finds its own SKILL_ROOT and execs the venv Python with the right script. Saves SKILL.md from having to discover paths on every Bash tool call.
- `scripts/orchestration.py` — deterministic helpers (job source parsing, resume discovery with PDF support, folder mining with `.claude/` ignored, fit-score extraction, history logging, company slug, first-run config, etc.).
- `scripts/render_pdf.py` — pure-Python PDF renderer using reportlab + DejaVu Sans (bundled). Handles EU / US styles, multi-role sub-blocks, bold markdown, and photo downscaling to keep output under 200KB.
- `scripts/github_mine.py` — fetches a student's public GitHub profile via `gh api` (or unauthenticated fallback), filters forks/archived/empty repos, returns prose evidence for the folder-miner.
- `assets/DejaVuSans.ttf` — fallback font with broad Unicode coverage. Renders Björn, François, Jiří, 🐍 correctly.
- `docs/DESIGN.md` — the design rationale. Read before a large PR.

The LLM pipeline runs prose between phases (no JSON), with small sentinel lines (`FIT_SCORE: 7`, `COMPANY: Deloitte`, `FAILURE: ...`) where structure actually matters. Sub-agents dispatch via each host's equivalent subagent mechanism (Claude Code's Task tool with `subagent_type="general-purpose"`, Gemini's `@generalist`, or Codex's inline execution). Interactive prompts similarly use each host's tool (`AskUserQuestion` / `request_user_input` / `ask_user`) with a hard-fail fallback for non-interactive contexts. Job descriptions and company-research output are wrapped in `<<<UNTRUSTED_*>>>` markers before reaching sub-agents that have file/web access — basic prompt-injection containment.

## Development

```bash
# Run the test suite (95 tests, ~1 second)
source .venv/bin/activate
pytest tests/ -v

# Try the skill on the fixtures
cd GOLDEN_FIXTURES
/resumasher sample-jd.md
```

### Project layout

```
resumasher/
├── SKILL.md              # Orchestration prompt Claude follows at runtime
├── bin/
│   └── resumasher-exec   # Self-locating wrapper around venv Python
├── scripts/
│   ├── orchestration.py  # Deterministic helpers (CLI + importable)
│   ├── render_pdf.py     # Pure-Python PDF renderer (reportlab + DejaVu Sans)
│   └── github_mine.py    # GitHub profile evidence fetcher
├── assets/
│   ├── DejaVuSans.ttf        # Bundled Unicode font (regular)
│   └── DejaVuSans-Bold.ttf   # Bundled Unicode font (bold)
├── docs/
│   └── DESIGN.md         # Design rationale — read before a large PR
├── GOLDEN_FIXTURES/      # Sample portfolio for testing and demo
├── tests/                # pytest suite (95 tests)
├── install.sh            # One-liner installer + venv setup
├── requirements.txt      # reportlab, pdfminer.six, chardet, nbconvert
└── requirements-dev.txt  # + pytest, jupyter
```

## Roadmap

**v0.1 (shipped, and improving weekly):**
- EU + US resume styles, ATS-safe single-column layout
- English-only JD input (pasted, file, or URL)
- Nine-phase pipeline: first-run setup → intake → folder + GitHub mine → fit analysis → company research → tailor → parallel cover-letter + interview-prep → interactive placeholder fill → PDF render → log + summary
- Multi-role tenures rendered correctly (e.g., Meta progression shown as one company entry with sub-role bullets, not three separate entries)
- Photos auto-downscaled to keep output PDFs <200KB (phone-camera headshots previously bloated PDFs to 1MB+)
- `resume.pdf` accepted when no markdown source exists
- GitHub profile mining as additional evidence source (`gh api` preferred, unauthenticated fallback)
- `[INSERT ...]` placeholder pattern for missing metrics, with interactive fill-in before PDFs render (student chooses Specifics / Soften / Drop per bullet)
- Prompt-injection defense via `<<<UNTRUSTED_*>>>` markers around JD / company-research content
- ATS round-trip gate (pdfminer.six + Jobscan calibration)
- Local history log per student (`.resumasher/history.jsonl`)

**v0.2 (after first cohort feedback):**
- `--review` mode: step-by-step interactive rewriting for every bullet (pedagogy first, not just placeholders)
- GitHub Actions CI with automated PDF round-trip on every push
- Incremental folder-mine cache invalidation
- German / French JD translation pre-pass
- Facts persistence: remember placeholder-fill answers across runs so the second application to a similar role doesn't re-ask the same `[INSERT TEAM SIZE]` questions

## License

MIT — see [LICENSE](LICENSE). Fork it, extend it, ship it to your students.

## Credits

Built by [Eduardo Ariño de la Rubia](https://github.com/earino) for his wonderful students, and anyone else who may find it useful.

Designed with [gstack](https://github.com/garrytan/gstack) (office-hours + plan-eng-review skills) and built with [Claude Code](https://claude.com/claude-code). Runs on Claude Code, OpenAI Codex CLI, and Google Gemini CLI.

## Contributing

PRs welcome. Before opening a large one, please read [`docs/DESIGN.md`](docs/DESIGN.md) — it captures why the skill is shaped the way it is (prose between LLM phases, pure-Python PDF, scope-reduced file tree, etc.) so you can propose changes that work with the design rather than against it.

If you hit a bug or have an idea, open an issue. v0.2 is explicitly shaped by feedback from early users.

Before submitting a PR:
- `pytest tests/ -v` should pass (95 tests, ~1 second).
- If you change any rendering logic, regenerate the GOLDEN_FIXTURES output and eyeball it through [jobscan.co](https://www.jobscan.co/) to confirm ATS parsing still works.
