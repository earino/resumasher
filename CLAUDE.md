# Project guidance for Claude Code agents

This file is loaded automatically when an agent starts work in this repo.
It documents conventions specific to working on resumasher as Claude.

## Sandbox vs host venv coordination

This repo's `.venv/` is a per-platform artifact owned by **whoever installed
the project on the host** (typically macOS or Windows native, via
`install.sh`). It contains platform-specific Python binaries (Linux ELF,
macOS Mach-O, or Windows PE) baked-in paths in `pyvenv.cfg`, etc.

**If you are a sandbox / Docker / remote-container agent sharing the host's
filesystem: do NOT write to `.venv/`.** Doing so will either silently
break the host's installation (you'll overwrite their venv with one whose
binaries can't execute on their platform) or your own builds will silently
break (you'll inherit a venv whose binaries can't execute in your sandbox).

### What to do instead

Use a sandbox-local venv at `$HOME/.venv-resumasher-sandbox/`. `$HOME` is
typically container-local in Docker (not part of the shared mount), so
the two venvs never collide.

If the sandbox venv doesn't exist yet, bootstrap it with:

```bash
bash tools/bootstrap-sandbox-venv.sh
```

This is idempotent (no-op if it exists), takes ~30s on a fresh container.

### How tools find the right venv

`bin/resumasher-exec` searches in this order:

1. `$RESUMASHER_VENV/bin/python` — explicit override (CI, custom setups)
2. `$HOME/.venv-resumasher-sandbox/bin/python` — sandbox convention
3. `$SKILL_ROOT/.venv/bin/python` — POSIX host install
4. `$SKILL_ROOT/.venv/Scripts/python.exe` — Windows host install

So once `tools/bootstrap-sandbox-venv.sh` has run, all wrapper invocations
do the right thing automatically. Hosts whose `.venv/` works keep using it
unchanged.

### When to use which

- **Running tests for development iteration in a sandbox:** invoke
  `~/.venv-resumasher-sandbox/bin/python -m pytest -q` directly. Faster
  than going through the wrapper.
- **Anything that involves cross-process invocation of project scripts**
  (subprocess from a test, SKILL.md `$RS` calls, etc.): always go through
  `bin/resumasher-exec`. It picks the right venv per the search order.

## Live-LLM tests are venv-independent by design

Tests under `tests/test_*_live.py` invoke `claude -p` to verify real-model
behavior (issue #29 was the first; more will follow). They are designed
to NOT depend on the project venv:

- They import only pure-Python project modules (`scripts.prompts`)
- They subprocess only to `claude` itself
- They auto-skip when `claude` is not on PATH or `RESUMASHER_SKIP_LIVE=1`

This means live tests run on whatever Python pytest is using, on either
side of the filesystem split. New live tests should follow the same
pattern: in-process function calls for prompt construction; subprocess
only for the LLM CLI; auto-skip via the `live_llm` marker (registered in
`conftest.py`) plus a `claude`-on-PATH check.

## Why the live tests use the developer's Claude Code subscription

Hitting the Anthropic API directly via the Python SDK would require an
API key in CI / local secrets. Going through `claude -p` with whichever
model is configured uses the developer's existing Claude Code billing
(per-message, generally rounding to zero for short Haiku calls) and
needs no additional credentials. CI skips automatically (no `claude`
binary on the runner), so this design naturally restricts live tests to
"run on a dev box before merge."

## Test discipline

- Deterministic tests (everything not under `*_live.py`) MUST run green
  in CI. Non-negotiable.
- Live tests run on dev boxes only. They are signal, not gate. The
  belt-and-suspenders pattern (issue #29's cleanup scan + structural
  prompt assertions) ensures regressions are caught even if the live
  test isn't run.

## Running tests on the host

`install.sh` installs runtime deps only — pytest/jupyter are excluded so
students who only want to use the skill don't have to download them. If
you're contributing and want to run tests on the host, install with the
dev flag:

```bash
bash install.sh --dev   # installs requirements-dev.txt (runtime + pytest + jupyter)
```

Sandbox installs via `tools/bootstrap-sandbox-venv.sh` always install dev
deps — test infrastructure is the whole point of a dev sandbox.

## See also

- `install.sh` — host install (creates `.venv/`). Use `--dev` to include pytest/jupyter.
- `tools/bootstrap-sandbox-venv.sh` — sandbox install (creates
  `~/.venv-resumasher-sandbox/`, always includes dev deps)
- `bin/resumasher-exec` — venv-aware Python wrapper
- `SKILL.md` — the orchestration logic; uses `$RS` (set to
  `bin/resumasher-exec`) for all subprocess work
