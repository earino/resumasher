# TODOS

Deferred work captured during development. When picking something up, read the full context before acting — the "why" matters more than the "what."

---

## Open

### Pin dependencies with hash-verified lockfile

**What.** Replace `requirements.txt` `>=` pins with exact versions and generate a `requirements-lock.txt` via `pip-compile --generate-hashes`. Update `install.sh` to use `pip install --require-hashes -r requirements-lock.txt` when the lockfile exists.

**Why.** Supply-chain attack surface. Current `requirements.txt` uses `reportlab>=4.0.0`, `pdfminer.six>=20221105`, `chardet>=5.0.0`, `nbconvert>=7.0.0`. Every `bash install.sh` resolves to whatever version is latest on PyPI at install time. A compromised PyPI account for any of the 4 direct deps (or their transitive deps) propagates to every student's laptop on the next install. `pdfminer.six` is single-maintainer — higher hijack risk.

**Pros.** Future-proofs against PyPI compromise. Reproducible installs. Explicit upgrade moments (you regenerate the lockfile when you want to update).

**Cons.** Lockfile needs regeneration on deliberate dep updates (~1 min with `pip-compile`). If you forget, the lockfile drifts from `requirements.txt`.

**Context.** Found in `/cso` audit 2026-04-18 as Finding #2 (MEDIUM). Saved to `.gstack/security-reports/2026-04-18-130000.json`. Fix is mechanical: pin versions → `pip-compile --generate-hashes` → update `install.sh`. ~20 minutes of work.

**Depends on / blocked by.** Nothing.

**Category.** Security (supply chain integrity).

---

### Validate path containment in `parse_job_source`

**What.** In `scripts/orchestration.py:74`, resolve `candidate.resolve()` and assert `candidate.relative_to(cwd.resolve())` succeeds before reading the file. If the path escapes CWD, fall through to literal mode (treat `arg` as pasted JD text, not a file path).

**Why.** Defense in depth against a prompt-injection chain. Current code reads any file the student's OS user can access, including `~/.ssh/id_rsa` or `/etc/passwd`, if `arg` resolves to one. A prompt-injected Claude session could invoke `/resumasher ../../../.ssh/id_rsa` and the SSH key becomes "JD content" flowing to every sub-agent and on-disk output files.

**Pros.** Removes one link in the chain. Primary defense (UNTRUSTED markers + sub-agent tool prohibitions) still holds if this layer fails.

**Cons.** Slight behavior change — absolute paths outside CWD become "literal text" instead of "file contents." Edge case: a student who intentionally wants to pass a JD from `/tmp/some-jd.txt` will find the CWD-containment rejects it. Mitigation: surface a clear message when this happens ("Path outside your working directory. Treating as literal text. If you meant to read this file, copy it into the working directory first.").

**Context.** Found in `/cso` audit 2026-04-18 as Finding #3 (MEDIUM). The exploit chain requires successful prompt injection on an earlier turn, so the immediate risk is low — it's belt-and-suspenders. Code snippet for the fix is in the audit report.

**Depends on / blocked by.** Nothing. Add a test covering the path-traversal case alongside the fix.

**Category.** Security (input validation / defense in depth).

---

## Completed

(none yet)
