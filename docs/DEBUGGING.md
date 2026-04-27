# Debugging this skill

If a student reports something wrong with a resumasher output — "my name's missing from the PDF," "the photo looks squished," "the sections are in the wrong order," or even a vague "something looks off" — follow this playbook. Do NOT just apologize or guess. The artifacts on disk plus the inspection helpers will tell you exactly what happened.

This playbook is agent-first: it assumes you (the AI CLI running resumasher) are right there in the same session, with full tool access. You are the diagnostic tool. The student only has to describe what they see.

## Step 1 — Find the artifacts

The student's most recent run lives in `applications/<company-slug>-<date>/` at their working directory. Key files:

- `tailored-resume.md` — what the tailor produced (source of truth)
- `resume.pdf` — what the student actually got
- `cover-letter.md` + `cover-letter.pdf`
- `interview-prep.md` + `interview-prep.pdf`
- `fit-assessment.md`, `company-research.md`
- `jd.md` — the JD this run targeted

If multiple application folders exist, pick the most recent by mtime or by the folder name's date suffix. Ask the student if it's ambiguous.

## Step 2 — Read the student's report literally

Quote the student's own words in your internal reasoning. Don't paraphrase into your own technical vocabulary before you've investigated. "Something's weird with the photo" is different from "the photo is stretched" is different from "the photo is the wrong photo." Each points at a different failure.

## Step 3 — Inspect the artifacts

Use `scripts/orchestration.py inspect` to get structured JSON views (`$RS` resolves the wrapper — see SKILL.md path prologue):

```bash
"$RS" orchestration inspect --resume "$OUT_DIR/tailored-resume.md"
"$RS" orchestration inspect --pdf    "$OUT_DIR/resume.pdf"
"$RS" orchestration inspect --photo  "<path-to-source-photo>"
```

Each command returns JSON with counts, contents, and light warnings for the most common bug signatures. The `warnings` field flags:

- `EMPTY_NAME` / `EMPTY_CONTACT_LINE` — parser found no candidate name
- `ORPHANED_BULLETS` — bullets floating at the end of a section with title-like paragraphs stacked before them
- `PHOTO_ASPECT_STRETCH` — source photo aspect differs from render box

Warnings are shortcuts, not the full story. Read the raw parse tree too — section counts, block structure, paragraph previews — because novel bugs won't trip any warning but their signature will be visible in the data.

## Step 4 — Match against known failure modes

Read `docs/KNOWN_FAILURE_MODES.md`. It lists every catalogued bug with:

- Symptom description
- Signature (how to detect it from the inspection output)
- Root cause
- Where in the code the fix lives
- Reference repro in `examples/`

If the student's symptom + inspection signature match an entry, you have a confident hypothesis without further investigation. State the match clearly: "This matches KNOWN_FAILURE_MODES.md #2 (orphaned bullets)."

If nothing matches, proceed to Step 5.

## Step 5 — Investigate novel bugs

For unknown failures, do the usual root-cause work:

- Read `scripts/render_pdf.py` and trace the path from markdown input to the broken output
- Check recent commits on the relevant files for regressions
- Cross-reference with git log, CHANGELOG entries, and prior learnings
- Form a hypothesis and verify it by reading the code (don't guess)

After diagnosis, add a new entry to `docs/KNOWN_FAILURE_MODES.md` so the next agent (and the next student) benefits. Save the repro pair to `examples/<short-bug-name>/` (already gitignored).

## Step 6 — Write the bug report

Use the template in `docs/BUG_REPORT_TEMPLATE.md`. Fill in:

- What the student reported (verbatim)
- What you found (specific evidence from inspection)
- Match to known failure mode, or "novel"
- Environment (version, host, model, style, Python, OS)
- Anonymized artifacts (see anonymization guide in the template)
- Suggested fix location (from KNOWN_FAILURE_MODES.md or your own investigation)

Anonymize the easy tier by default: name, email, phone, LinkedIn, GitHub username. Pull these from the student's `.resumasher/config.json` if available, or from the markdown directly. Leave company names, metrics, project names, and technical keywords intact unless the student specifically asks otherwise — these are load-bearing for reproduction.

## Step 7 — File it (with consent)

Show the final report to the student. Read back the redactions you applied. Ask explicitly: "Ready to file this as a GitHub issue? I can submit it for you via `gh issue create`." Only proceed if they say yes.

```bash
gh issue create --repo earino/resumasher \
  --title "<concise-symptom>" \
  --label bug \
  --body-file "$OUT_DIR/bug-report.md"
```

Give the student the resulting issue URL so they can follow it.

## What NOT to do

- **Don't build a pre-flight `--debug` mode.** The playbook is post-hoc and runs against artifacts already on disk. You don't need the student to re-run anything.
- **Don't paste raw parse-tree JSON as the bug report.** That's input to your diagnosis, not output to the maintainer. Write prose that names the specific failure.
- **Don't auto-file.** Always get explicit consent before `gh issue create` — the student owns their data.
- **Don't anonymize company names or metrics without asking.** These are needed for reproduction. Only redact if the student flags them as sensitive.
