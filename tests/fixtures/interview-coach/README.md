# Interview-coach fixture set

Synthetic-persona inputs for the interview-coach sub-agent prompt. Used by:

- `tests/test_interview_coach_live.py` — the Tier 3 live LLM test that invokes
  `claude -p --model claude-haiku-4-5` against the real prompt and checks
  for stray-file behavior (issue #29).
- `tools/verify-issue-29.sh` — manual reproduction harness for humans.

The persona is **Ana Müller**, an MS Business Analytics student at CEU Vienna.
Every detail is fabricated. No real student data appears here. It is safe to
extend / modify these fixtures.

Files:

- `resume.md` — Ana's source resume (the file `discover_resume` would find)
- `tailored-resume.md` — the tailor sub-agent's output for the fixture JD
- `cache.txt` — the folder-miner sub-agent's prose summary of Ana's project work
- `jd.txt` — the job description used as input

Layout the live test materializes in tmp_path:

    tmp_path/
      resume.md
      .resumasher/
        cache.txt
        run/
          jd.txt
      applications/test-run/
        tailored-resume.md
