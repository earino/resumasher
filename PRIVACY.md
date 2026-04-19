# resumasher privacy notice

resumasher is a personal research tool built for MS Business Analytics
students at CEU (and, after the cohort pilot, anyone who finds it useful).
This document describes what data is collected if you opt into usage
analytics, and what we do with it.

## What happens by default

By default, **no data is collected or transmitted**. Telemetry is off until
you actively choose a tier during first-run setup, or by running
`resumasher telemetry set-tier <option>`.

"Off" is the default even if you press Enter past the consent prompt. Under
GDPR, ignoring a consent prompt is not consent. Active opt-in only.

## Three tiers

- **Off** (default). No logging, no transmission. Nothing leaves your machine.
- **Anonymous**. Events are logged locally and sent to a Supabase backend,
  stripped of any installation identifier before transmission. Individual
  runs cannot be linked to each other or to a specific installation.
- **Community**. Events are logged locally and sent with a random
  installation ID (UUID v4 generated at first opt-in, stored in
  `~/.resumasher/installation-id`). The UUID is NOT derived from your
  hostname, username, MAC address, or any other identifier. It is
  randomly generated once and lets the maintainer correlate your runs
  (e.g., "this user is hitting the same bug repeatedly") without knowing
  who you are.

## What gets sent (under anonymous or community tier)

For each event:

- Event type, timestamp, resumasher version, host (Claude Code / Codex /
  Gemini), OS, CPU arch
- Model identifier (e.g. `claude-opus-4-7`, `gpt-5-codex`,
  `gemini-2.5-pro`) — self-reported by the AI orchestrator so the
  maintainer can see which models produce which fit scores or hit
  which bugs
- Duration, outcome, error class (from a pre-declared enum — never a raw
  error message)
- Company name applied to (lowercased), job title, seniority (from a
  pre-declared enum: intern / junior / mid / senior / staff / manager /
  director / vp / cxo / unknown)
- Fit score (0–10), counts of strengths and gaps, recommendation enum
  (yes / yes\_with\_caveats / no)
- Number of placeholders emitted, choices at placeholder-fill time
- Resume style (eu / us), whether a photo is included, whether GitHub
  is configured
- Counts of GitHub repos and folder files mined
- JD source mode (file / url / literal)
- Resume format detected, install scope
- Time of day, rounded to a 4-hour bucket in **your local timezone**
- Under community tier only: a random installation ID (UUID v4)

## What does NOT get sent

- Your name, email, phone, LinkedIn URL, physical address
- Your GitHub username
- Your resume content
- The job description text
- File paths, folder structure, OS username
- IP address (Supabase may log request IPs for infrastructure reasons
  but these are not stored in the telemetry database itself)
- Any raw text from error messages — only a pre-declared error class enum

## Retention

Events older than **90 days** are deleted automatically. Installation
records with no recent events for **180 days** are deleted. Aggregate
dashboard views survive retention (they are counts, not individual rows).

## Your rights under GDPR

- **Access.** Run `resumasher telemetry export` to see every event that
  has been logged locally on your machine. (The local log is the source
  of truth for what has been transmitted.)
- **Deletion.** Run `resumasher telemetry delete` to wipe local data AND
  send a delete request to the backend for your installation ID. This
  removes every row whose installation\_id matches yours from both tables
  and returns a count so you can verify.
- **Opt-out.** Run `resumasher telemetry set-tier off` anytime. Future
  events will not be logged or sent.
- **Tier downgrade.** Switching from community to anonymous does NOT
  retroactively strip installation\_id from already-sent events. If you
  want those gone, use `telemetry delete` first, then `set-tier
  anonymous`.

## Where data is stored

The backend runs on Supabase in the **Ireland region (eu-west-1)**.
Data does not leave the EU. The Supabase anon key used by clients is
public (committed to the repo); row-level security denies it direct
read/write access to any table — the only path from client to database
is through a validated edge function.

## Data controller

Eduardo Ariño de la Rubia (github.com/earino) is the data controller.
Contact via GitHub issues on the resumasher repo:
https://github.com/earino/resumasher/issues

## Sensitive applications

If you are applying to employers whose identity could reveal sensitive
information about you (health, political affiliations, religious
organizations, etc.), we recommend using the **Off** tier for those
runs. The company-name field is the only place such information could
leak, and Off tier never sends any data.

## Audit

The edge functions that receive telemetry are committed to the repo at
`supabase/functions/telemetry-ingest/` and `supabase/functions/telemetry-delete/`.
The migrations that define the database schema are in `supabase/migrations/`.
You can read the exact code that handles your data.
