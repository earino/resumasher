# resumasher

A Claude Code skill that tailors your resume + writes a cover letter + builds an interview prep bundle for a specific job, by mining the evidence already in your working directory.

Built for CEU Vienna's MS in Business Analytics cohort, but the shape generalizes to any student with a portfolio of code, notebooks, and PDF reports.

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

### 1. Clone into your Claude Code skills directory

```bash
git clone https://github.com/<your-org>/resumasher.git ~/.claude/skills/resumasher
cd ~/.claude/skills/resumasher
```

### 2. Install Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Works on macOS, Linux, and Windows (WSL or native Python). No native dependencies — reportlab and pdfminer.six are pure Python.

### 3. Restart Claude Code

The skill should now be available as `/resumasher`.

## Usage

### First-run setup (one time per folder)

The first time you run `/resumasher` in a folder, it will ask for your contact info, default style (EU or US), and whether to include a photo by default. Takes about 2 minutes.

Everything is stored locally in `.resumasher/config.json`. Nothing is uploaded.

### Folder layout it expects

```
my-job-search/
├── resume.md            # Your base resume (required)
├── photo.jpg            # Optional, for EU-style resumes
├── applications/        # resumasher writes PDFs here
└── projects/            # Your work — code, notebooks, READMEs, PDFs
    ├── capstone/
    ├── ml-final/
    └── text-mining/
```

See `GOLDEN_FIXTURES/` in this repo for a full example.

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
# Run the test suite
source .venv/bin/activate
pytest tests/ -v

# Try the skill on the fixtures
cd GOLDEN_FIXTURES
/resumasher sample-jd.md
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

Built for the CEU Vienna MS in Business Analytics program. Designed with [gstack](https://github.com/garrytan/gstack) (office-hours + plan-eng-review) and [Claude Code](https://claude.com/claude-code).
