# GOLDEN_FIXTURES

A sample CEU Vienna MS Business Analytics student portfolio, used for:
- resumasher end-to-end testing
- Demoing the skill to students before they use their own data
- Regression testing when a future change might affect output quality

## Contents

- `resume.md` — Ana Müller's base resume (intentionally uses non-ASCII characters
  in the name to exercise the Unicode path).
- `sample-jd.md` — a Deloitte Vienna Data Analyst job description, representative
  of what CEU graduates see on the market.
- `projects/` — three sample projects (capstone, ML final, text mining) with
  READMEs, notebooks, Python files, and a generated PDF report (created on
  demand by the dogfood test).

## Using the fixture

From this directory, run:

```bash
/resumasher sample-jd.md
```

and verify three PDFs land in `./applications/deloitte-<date>/`.

## Why Ana Müller

Made-up person. The umlaut is deliberate, so the skill's DejaVu Sans font path
gets exercised on every fixture run. If we ever ship a version where "Müller"
renders as "M ller" in the PDF, tests catch it before students do.
