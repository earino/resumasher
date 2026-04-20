# Known failure modes

A growing catalog of ways resumasher can produce wrong output, each matched
to a signature the AI CLI can detect via `scripts/orchestration.py inspect`
or by reading artifact contents directly.

**For agents debugging a student-reported bug:** skim this list first. If
the student's symptom matches a known mode, you can form a confident
hypothesis without deep investigation. If it doesn't match, do the normal
root-cause work, then come back and add the new mode here so the next
agent has it.

**For maintainers:** each entry includes the file + function where the fix
lives, so you can jump straight to the code.

---

## #1 — Missing contact header (no name on PDF)

**Tracked in:** [#18](https://github.com/earino/resumasher/issues/18).
**Status:** renderer now fails loudly on this shape (v0.4 Unreleased) — no
more silent broken PDFs. Tailor-prompt hardening + regression tests shipped
alongside. Original silent-drop bug cannot recur without removing the gate
in `scripts/render_pdf.py::_assert_contact_header_present`, which is
guarded by tests in `tests/test_render_pdf.py`.


**Symptom.** The rendered PDF has no name, no email, no phone, no LinkedIn.
The first visible content is either the photo (EU style) or the "Summary"
heading. An ATS cannot identify the candidate from this PDF.

**Signature** (from `orchestration inspect --resume`):
- `name: ""` and `contact_line: ""`
- `has_h1: false`
- `first_line_raw` contains pipe-separated contact info but no `# ` prefix
- `warnings` includes `EMPTY_NAME` and usually `EMPTY_CONTACT_LINE`

**Root cause.** `parse_resume_markdown` (in `scripts/render_pdf.py`) expects
`# Name` on line 1, followed by a contact line with `email | phone | ...`.
When line 1 is a pipe-separated string with no `# ` prefix, the parser
doesn't recognize anything as the contact header and silently drops
everything before the first `##` section.

**Two places to fix, both should ship together.**

1. **Tailor prompt.** Commit `92169f9d` ("pre-built contact header from
   config") was supposed to guarantee the `# Name\nemail | phone | ...`
   shape. Verify `scripts/prompts.py` tailor template is injecting the
   pre-built header verbatim and instructing the LLM not to modify it.
2. **Renderer fallback.** `parse_resume_markdown` should treat the first
   non-empty line as the contact paragraph when no `# Name` H1 is present,
   OR fail loudly with "no name detected" instead of silently dropping.

**Fix location.** `scripts/render_pdf.py` → `parse_resume_markdown`,
around the `name/contact_line` extraction. Plus `scripts/prompts.py` for
the tailor-side hardening.

**Reference fixture.** Minimal anonymized repro in
`tests/test_orchestration.py` (search for the relevant `test_inspect_*`
test). Full original student-reported pair is kept locally at
`examples/sections_dropped/` (gitignored for privacy).

---

## #2 — Orphaned bullets (bullets float to end of section)

**Tracked in:** [#19](https://github.com/earino/resumasher/issues/19).


**Symptom.** In sections with multiple projects/roles (Research Experience,
Work Experience), all project titles appear stacked with no bullets under
them, then all the bullets from all the projects show up in a single flat
list at the end of the section. The reader can't tell which bullet
belongs to which project.

**Signature** (from `orchestration inspect --resume`):
- `warnings` includes `ORPHANED_BULLETS` for the affected section
- `shape: "B"` is the common case — `block_count == 0`,
  `raw_paragraph_count >= 2`, `raw_bullet_count >= 2`, and
  `raw_paragraph_previews` contains entries starting with `**` and
  containing `**` twice (bold markdown)
- Shape A (`section.blocks` exist but all have 0 bullets) is rarer but
  handled by the same warning

**Root cause.** The parser's sub-block attachment logic expects a
three-level structure:

```markdown
### Company or project heading      ← ### heading creates a ResumeBlock
**Sub-role title**                   ← **bold** attaches to the block
- bullets...
```

When the tailor emits only two levels (skipping the `###` wrapper):

```markdown
## Research Experience
**Project title**                    ← no ### above — no block created
- bullets...
```

…the parser lands the `**Project title**` lines in `section.raw_paragraphs`
and the bullets in `section.raw_bullets`. The renderer emits paragraphs
first (all titles stacked), then bullets (all at the end).

**Fix location.** `scripts/render_pdf.py` → `parse_resume_markdown`.
Teach the parser to synthesize a `ResumeBlock` when it sees a `**Title**`
line directly under `##` (no `###` wrapper), and attach subsequent
bullets to that pseudo-block until the next `**Title**` or `##`.

**Fix for the tailor side (complementary):** update the tailor prompt in
`scripts/prompts.py` to emit `### Project title` for multi-project sections
instead of `**Project title**`. Belt and suspenders.

**Reference fixture.** Minimal anonymized repro in
`tests/test_orchestration.py` (search for the relevant `test_inspect_*`
test). Full original student-reported pair is kept locally at
`examples/sections_dropped/` (gitignored for privacy).

---

## #3 — Section order changes between markdown and PDF

**Symptom.** Sections appear in a different order in the PDF than in the
tailored markdown (e.g., Education jumps from last position in the
markdown to right after Summary in the PDF).

**Signature.**
- Run `orchestration inspect --resume tailored-resume.md` → note
  `section_order`
- Run `orchestration inspect --pdf resume.pdf` → note
  `section_order_in_text`
- Compare the two lists

**Resolution depends on style.** Check `.resumasher/config.json` for the
`style` value (or the `--style` flag on the run that produced this PDF):

- **`style: us`** — expected behavior. US renderer reorders sections
  (Summary → Education → Experience → Skills for new grads). This is by
  design, not a bug. Close as won't-fix or improve the docs.
- **`style: eu`** — bug. EU style should preserve source order. Look at
  `_section_order_eu` in `scripts/render_pdf.py`.

**Fix location.** `scripts/render_pdf.py` → `_section_order_eu` if the
style is EU.

**Reference repro.** `examples/sections_dropped/` (Jiaqi Pan — style was
reported as `eu` but the PDF shows US-style ordering; worth investigating
whether the student's config overrode the default).

---

## #4 — Photo aspect ratio stretched

**Symptom.** The photo on the EU-style PDF looks distorted. Faces may look
wider or narrower than in the source image. Students describe it as
"flattened," "squished," or "stretched."

**Signature** (from `orchestration inspect --photo <source>`):
- `aspect` differs from `render_box_aspect` (currently 1.0) by more than
  5%
- `warnings` includes `PHOTO_ASPECT_STRETCH`
- `aspect_delta_pct` quantifies the distortion

**Root cause.** `scripts/render_pdf.py` line 429 embeds the photo at a
fixed `3cm × 3cm` box. reportlab's `Image` flowable stretches the source
to fill that box. Portrait photos (aspect ~0.75) render horizontally
stretched; landscape photos render vertically compressed.

**Fix location.** `scripts/render_pdf.py` → the `Image(...)` call on
line ~429. Compute width/height from source dimensions, clamping the
longer side to a max cm:

```python
from reportlab.lib.utils import ImageReader
reader = ImageReader(photo_source)
src_w, src_h = reader.getSize()
max_side_cm = 3.0
if src_w >= src_h:
    w_cm, h_cm = max_side_cm, max_side_cm * (src_h / src_w)
else:
    h_cm, w_cm = max_side_cm, max_side_cm * (src_w / src_h)
img = Image(photo_source, width=w_cm * cm, height=h_cm * cm)
```

**Reference repro.** `examples/sections_dropped/` (Jiaqi Pan — portrait
photo rendered at 3cm × 3cm, visible horizontal stretch).

---

## #5 — Photo path not persisted to tailored markdown

**Tracked in:** [#20](https://github.com/earino/resumasher/issues/20).

**Symptom.** A student provides a photo via `--photo <path>` or config, and
resumasher embeds it in `resume.pdf` correctly. But the source
`tailored-resume.md` has no record of the photo — no image syntax, no path
reference, no HTML comment. If the student later edits the markdown and
re-renders (the SKILL.md "Re-rendering PDFs after manual edits" workflow),
the renderer has no way to source the photo from the markdown itself and
depends on external state (config or CLI flag).

**Signature** (from `orchestration inspect --resume <path>`):
- `tailored-resume.md` exists in the application folder
- `resume.pdf` exists and contains an embedded image (visible in extracted
  text or confirmed by file size > ~40KB above a no-photo baseline)
- `inspect --resume` shows no photo-related content: `first_line_raw`
  doesn't contain "photo", no image link syntax in
  `raw_paragraph_previews`, no HTML comment with "photo:" anywhere
- Student's config (`.resumasher/config.json`) has a `photo` field OR the
  `/resumasher` invocation included `--photo <path>`

**Root cause.** The tailor prompt doesn't include the photo reference in
its output. The markdown isn't self-describing about non-text artifacts
the tool used. Re-render currently depends on external state to source
the photo.

**Fix location.**
- `scripts/prompts.py` tailor template — when a photo is provided, emit
  an HTML comment next to the contact header: `<!-- photo: <path> -->`.
  HTML comments render as invisible in markdown previews, so no visual
  weirdness.
- `scripts/render_pdf.py::parse_resume_markdown` — extract the comment
  and surface the path on `ResumeDoc` (new field, e.g., `photo_path`).
- `scripts/render_pdf.py` render flow — use the comment's path when
  `--photo` is not explicitly passed. Precedence: explicit flag > markdown
  comment > no photo.

**Reference fixture.** `examples/sections_dropped/tailored-resume.md` —
has no photo reference anywhere despite the rendered PDF having one.

---

## Contributing to this catalog

When an agent diagnoses a new bug that isn't in this list:

1. Save the repro pair at `examples/<short-bug-name>/` (gitignored, stays
   local).
2. Add an entry here with symptom / signature / root cause / fix location.
3. If the signature is regex-detectable, add a warning to
   `orchestration inspect` in `scripts/orchestration.py`.
4. Add a regression test in `tests/test_orchestration.py` or
   `tests/test_render_pdf.py` using the repro pair as the fixture.

The catalog compounds over time. Every bug that lands here prevents the
next student from hitting the same wall silently.
