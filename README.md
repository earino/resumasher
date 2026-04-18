# resumasher

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Tests: 65 passing](https://img.shields.io/badge/tests-65%20passing-brightgreen.svg)](tests/)

A Claude Code skill that tailors your resume + writes a cover letter + builds an interview prep bundle for a specific job, by mining the evidence already in your working directory.

Built for an MS Business Analytics cohort, but the shape generalizes to any student with a portfolio of code, notebooks, and PDF reports.

**TL;DR:** Every other AI resume tool is a web app that only sees the summary you paste in. resumasher runs locally, so it reads your actual project files — code, notebooks, READMEs, PDFs — and cites concrete evidence in your resume. Generic bullets become source-backed claims.

## What it does

From inside your resume folder, run:

```bash
/resumasher job.md
/resumasher https://careers.deloitte.com/job/12345
/resumasher "Senior Data Analyst at Acme Corp. Requirements: SQL, Python..."
```

Within ~3 minutes you get `./applications/<company>-<date>/` containing:

| File | What it is |
|---|---|
| `resume.pdf` | Tailored resume, ATS-safe, single column, EU or US style |
| `cover-letter.pdf` | 3-paragraph cover letter weaving in recent company news |
| `interview-prep.pdf` | Likely SQL / case / behavioral questions with draft answers pulled from your actual projects |
| `fit-assessment.md` | Honest fit score (0-10) with strengths and gaps — not a pep talk |
| `company-research.md` | 3-5 recent facts about the company with citations |
| `tailored-resume.md`, `cover-letter.md`, `interview-prep.md` | Markdown sources (edit and re-render) |

## The unfair advantage: folder access

Every other resume-tailoring tool is a web app that only sees the summary you paste in. resumasher runs locally, so it reads your `capstone/`, `ml-final/`, `text-mining/` folders and cites specific evidence in your resume.

Your bullet becomes: "Built an XGBoost churn classifier on 2.3M rows, F1=0.82, deployed to Flask — see `/projects/churn-model/`" instead of "built a machine learning model."

Competitors cannot do this. resumasher can because it lives in the filesystem.

## Install

### Quick install (one-liner)

```bash
git clone https://github.com/earino/resumasher.git ~/.claude/skills/resumasher \
  && bash ~/.claude/skills/resumasher/install.sh
```

Then restart Claude Code.

### Manual install

**1. Clone into your Claude Code skills directory:**

```bash
git clone https://github.com/earino/resumasher.git ~/.claude/skills/resumasher
cd ~/.claude/skills/resumasher
```

**2. Install Python dependencies in a venv:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Works on macOS, Linux, and Windows (WSL or native Python). No native dependencies — reportlab and pdfminer.six are pure Python.

**3. Restart Claude Code.**

The skill should now be available as `/resumasher`.

### Install for a single project only

The default install above is **user-scope** — `/resumasher` is available in every folder you open in Claude Code. If you'd rather have it **project-scope** (available only inside one specific folder, like `~/my-job-applications/`), clone into that project's `.claude/skills/` instead:

```bash
cd ~/my-job-applications
git clone https://github.com/earino/resumasher.git .claude/skills/resumasher \
  && bash .claude/skills/resumasher/install.sh
```

Now `/resumasher` only appears when Claude Code is running inside `~/my-job-applications/`. It won't pollute any other project. Use this if you want all your job-application work (resumes, JDs, the skill itself) checked into one folder, or if you just prefer clean scoping.

You probably want to add `.claude/skills/resumasher/.venv/` to that project's `.gitignore` so the Python venv doesn't get checked in.

### Verify the install

```bash
cd ~/.claude/skills/resumasher/GOLDEN_FIXTURES
# From a fresh Claude Code session:
/resumasher sample-jd.md
```

In ~2 minutes you should see three PDFs in `./applications/deloitte-consulting-<today>/`.

(If you used the project-scope install, replace `~/.claude/skills/resumasher/` with `~/my-job-applications/.claude/skills/resumasher/`.)

## Usage

### First-run setup (one time per folder)

The first time you run `/resumasher` in a folder, it will ask for your contact info, default style (EU or US), and whether to include a photo by default. Takes about 2 minutes.

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

- `SKILL.md` — the orchestration prompt Claude follows when you invoke `/resumasher`.
- `scripts/render_pdf.py` — pure-Python PDF renderer using reportlab + DejaVu Sans (bundled). Handles EU / US styles.
- `scripts/orchestration.py` — deterministic helpers (job source parsing, folder mining, fit-score extraction, history logging).
- `assets/DejaVuSans.ttf` — fallback font with broad Unicode coverage. Renders Björn, François, Jiří, 🐍 correctly.

The LLM pipeline runs prose between phases (no JSON), with small sentinel lines (`FIT_SCORE: 7`, `COMPANY: Deloitte`, `FAILURE: ...`) where structure actually matters. Sub-agents dispatch via Claude Code's Task tool with `subagent_type="general-purpose"`.

## Development

```bash
# Run the test suite (65 tests, ~1 second)
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
├── scripts/
│   ├── render_pdf.py     # Pure-Python PDF renderer (reportlab + DejaVu Sans)
│   └── orchestration.py  # Deterministic helpers (CLI + importable)
├── assets/
│   ├── DejaVuSans.ttf        # Bundled Unicode font (regular)
│   └── DejaVuSans-Bold.ttf   # Bundled Unicode font (bold)
├── GOLDEN_FIXTURES/      # Sample portfolio for testing and demo
├── tests/                # pytest suite (65 tests)
├── install.sh            # One-liner installer
├── requirements.txt      # reportlab, pdfminer.six, chardet, nbconvert
└── requirements-dev.txt  # + pytest, jupyter
```

## Roadmap

**v0.1 (ships today):**
- EU + US resume styles
- English-only JD input (pasted, file, or URL)
- Pipeline: fit check → company research → tailor → cover letter → interview prep → PDFs
- ATS round-trip gate
- Local history log per student

**v0.2 (after first cohort feedback):**
- `--review` mode: step-by-step interactive rewriting (pedagogy first)
- GitHub Actions CI with automated PDF round-trip
- Incremental folder-mine cache invalidation
- German JD translation pre-pass

## License

MIT — see [LICENSE](LICENSE). Fork it, extend it, ship it to your students.

## Credits

Built by [Eduardo Ariño de la Rubia](https://github.com/earino) for his wonderful students, and anyone else who may find it useful.

Designed with [gstack](https://github.com/garrytan/gstack) (office-hours + plan-eng-review skills) and built with [Claude Code](https://claude.com/claude-code).

## Contributing

PRs welcome. Before opening a large one, please read [`docs/DESIGN.md`](docs/DESIGN.md) — it captures why the skill is shaped the way it is (prose between LLM phases, pure-Python PDF, scope-reduced file tree, etc.) so you can propose changes that work with the design rather than against it.

If you hit a bug or have an idea, open an issue. v0.2 is explicitly shaped by feedback from early users.

Before submitting a PR:
- `pytest tests/ -v` should pass (65 tests, ~1 second).
- If you change any rendering logic, regenerate the GOLDEN_FIXTURES output and eyeball it through [jobscan.co](https://www.jobscan.co/) to confirm ATS parsing still works.
