# Bug report template

Skeleton for agents to fill in when a student reports a resumasher bug.
Copy this structure, replace placeholders, include only sections that
apply, and submit via `gh issue create --title "..." --body-file <path>`.

---

```markdown
## What the student reported

<paraphrase of the student's complaint in their own language; quote
verbatim where possible so the signal isn't lost to AI translation>

## What I found

<1-3 paragraphs explaining the specific failure, anchored in evidence
from `orchestration inspect --resume/--pdf/--photo`. Name the parser
field or the code line if possible.>

**Matches known failure mode:** #<N> — <title from KNOWN_FAILURE_MODES.md>
(or: "novel failure, not currently catalogued")

## Reproduction

**Environment**
- resumasher version: <from VERSION file>
- Host: <claude_code / codex_cli / gemini_cli>
- Model: <e.g. claude-opus-4-7, gpt-5-codex, gemini-2.5-pro>
- Style: <eu / us>
- Python: <sys.version.split()[0]>
- OS: <platform.platform()>

**Artifacts** (anonymized)

The student's tailored markdown:

\`\`\`markdown
<content of applications/<slug>-<date>/tailored-resume.md,
with name/email/phone/LinkedIn/GitHub username redacted to
<CANDIDATE>/<EMAIL>/<PHONE>/<LINKEDIN>/<GITHUB>>
\`\`\`

Parser inspection:

\`\`\`json
<relevant slice of `orchestration inspect --resume` output — the
sections and warnings that show the bug signature, not the whole tree>
\`\`\`

<If applicable:> PDF extracted text:

\`\`\`
<pdfminer output showing the wrong ordering/missing content>
\`\`\`

<If applicable:> Photo inspection:

\`\`\`json
<orchestration inspect --photo output>
\`\`\`

## Suggested fix

<If the bug matches a known failure mode, point at the fix location from
KNOWN_FAILURE_MODES.md. If novel, sketch where in the codebase the bug
likely lives and why.>

## Student impact

<One sentence: who hits this and what it costs them. "Student submitted
PDF to ATS without their name on it — their application was rejected at
the resume-parse step.">
```

---

## Anonymization guide (easy tier, default)

Before filing, replace in the report body:

| Field | Replacement |
|-------|-------------|
| Candidate name | `<CANDIDATE>` |
| Email | `candidate@example.com` |
| Phone | `+00 000 0000000` |
| LinkedIn URL | `linkedin.com/in/<candidate>` |
| GitHub username | `<github-user>` |
| Home city / country | Only if unusually identifying (small town) |

## Anonymization guide — what to LEAVE IN

These are needed for reproduction, don't redact by default:

- Company names in the JD (tells us whether the parser handled
  "Deloitte" vs "Raiffeisen Bank International" differently)
- Specific metrics from the resume (F1=0.82, 2.3M rows) — these
  often interact with markdown parsing edge cases
- Course names, project names, technical keywords
- Section headings verbatim

If the student flags any of these as sensitive, redact them and note
"<redacted at student request>" in the artifact block.

## Filing

Before running `gh issue create`, **show the final body to the student
and get explicit confirmation**. Read it back, ask "ready to file?" —
don't auto-file. Once they say yes:

```bash
gh issue create \
  --repo earino/resumasher \
  --title "<concise symptom>" \
  --label bug \
  --body-file <path-to-rendered-report>
```

After filing, tell the student the issue URL so they can watch for
follow-up.
