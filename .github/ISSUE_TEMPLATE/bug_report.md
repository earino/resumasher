---
name: Bug report
about: Something went wrong when you ran `/resumasher`.
title: "[bug] "
labels: bug
assignees: ''
---

Thanks for filing a bug. Fill in whatever you can — the more detail, the faster it gets fixed. Skip any section that genuinely doesn't apply.

## What you did

<!-- One sentence. "I ran /resumasher job.md from a folder with resume.pdf and a projects/ subdir." -->

## What you expected to happen

<!-- "Three PDFs in applications/<company>-<date>/" or similar. -->

## What actually happened

<!-- Paste the terminal output. If Claude Code produced an error message, paste it VERBATIM — the exact error text matters for diagnosis. Triple-backtick-fence long output. -->

```
paste output here
```

## Environment

**OS:** <!-- macOS 15.1, Ubuntu 24.04, Windows 11 + WSL2, etc. -->
**Python version:** <!-- run: python3 --version -->
**Claude Code version:** <!-- bottom of the terminal banner shows it, e.g. "Claude Code v2.1.114" -->
**resumasher commit:** <!-- run: cd <skill-install-path> && git rev-parse --short HEAD -->
**Install scope:** <!-- user-scope (~/.claude/skills/resumasher) or project-scope (<project>/.claude/skills/resumasher)? -->

## Input files

**Resume format:** <!-- resume.md or resume.pdf? -->
**Resume size:** <!-- wc -l resume.md  OR  ls -lh resume.pdf -->
**JD source:** <!-- file path, URL, or pasted literal text? -->
**Photo:** <!-- Did the run include a photo? What file type + size? (Leave blank if no photo.) -->
**GitHub profile:** <!-- In config.json, yes/no? -->

## Anything else

<!-- Screenshots, weird things you noticed, what you'd already tried. -->
