-- Address Supabase security linter findings:
--
--   ERROR: views had SECURITY DEFINER (Postgres default) — recreate with
--          security_invoker=true so views respect the caller's RLS.
--   WARN:  run_telemetry_retention had mutable search_path — pin it.
--
-- Views still REVOKE SELECT FROM anon, so only service_role can query them.
-- security_invoker adds a second layer: even if someone grants a view to
-- anon by mistake, the caller's RLS on the underlying tables blocks the read.

DROP VIEW IF EXISTS daily_summary;
DROP VIEW IF EXISTS failure_clusters;
DROP VIEW IF EXISTS company_distribution;
DROP VIEW IF EXISTS placeholder_choice_mix;
DROP VIEW IF EXISTS fit_score_distribution;
DROP VIEW IF EXISTS seniority_distribution;

CREATE VIEW daily_summary WITH (security_invoker=true) AS
SELECT
  DATE_TRUNC('day', event_timestamp) AS day,
  event_type,
  host,
  COUNT(*) AS events,
  COUNT(DISTINCT installation_id) AS unique_installations
FROM telemetry_events
GROUP BY 1, 2, 3
ORDER BY 1 DESC, 2, 3;

CREATE VIEW failure_clusters WITH (security_invoker=true) AS
SELECT
  error_class,
  failed_phase,
  host,
  resumasher_version,
  COUNT(*) AS total_occurrences,
  COUNT(DISTINCT installation_id) AS affected_installations,
  COUNT(*) - COUNT(installation_id) AS anonymous_occurrences,
  MIN(event_timestamp) AS first_seen,
  MAX(event_timestamp) AS last_seen
FROM telemetry_events
WHERE outcome = 'failure' AND error_class IS NOT NULL
GROUP BY 1, 2, 3, 4
ORDER BY total_occurrences DESC;

CREATE VIEW company_distribution WITH (security_invoker=true) AS
SELECT
  company_normalized,
  COUNT(*) AS total_applications,
  AVG(fit_score) AS avg_fit_score,
  COUNT(DISTINCT installation_id) AS distinct_applicants
FROM telemetry_events
WHERE event_type IN ('run_completed', 'run_failed')
  AND company_normalized IS NOT NULL
GROUP BY 1
HAVING COUNT(*) >= 3
ORDER BY total_applications DESC;

CREATE VIEW placeholder_choice_mix WITH (security_invoker=true) AS
SELECT
  choice_type,
  COUNT(*) AS occurrences
FROM telemetry_events
WHERE event_type = 'placeholder_fill_choice'
GROUP BY 1;

CREATE VIEW fit_score_distribution WITH (security_invoker=true) AS
SELECT
  fit_score,
  COUNT(*) AS occurrences,
  AVG(num_placeholders_emitted) AS avg_placeholders
FROM telemetry_events
WHERE event_type = 'run_completed' AND fit_score IS NOT NULL
GROUP BY 1
ORDER BY 1;

CREATE VIEW seniority_distribution WITH (security_invoker=true) AS
SELECT
  job_seniority,
  COUNT(*) AS applications,
  AVG(fit_score) AS avg_fit
FROM telemetry_events
WHERE event_type IN ('run_completed', 'run_failed')
  AND job_seniority IS NOT NULL
GROUP BY 1;

REVOKE SELECT ON daily_summary, failure_clusters, company_distribution,
               placeholder_choice_mix, fit_score_distribution,
               seniority_distribution
  FROM anon;

CREATE OR REPLACE FUNCTION run_telemetry_retention() RETURNS void
LANGUAGE plpgsql
SET search_path = public, pg_catalog
AS $$
BEGIN
  DELETE FROM telemetry_events WHERE received_at < now() - interval '90 days';
  DELETE FROM installations WHERE last_seen < now() - interval '180 days';
END;
$$;
