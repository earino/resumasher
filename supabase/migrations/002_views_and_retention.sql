-- Dashboard views + retention job.
--
-- Views are recreated in 006 with WITH (security_invoker=true) to satisfy
-- the Supabase security linter. Retention runs daily via pg_cron.

CREATE VIEW daily_summary AS
SELECT
  DATE_TRUNC('day', event_timestamp) AS day,
  event_type,
  host,
  COUNT(*) AS events,
  COUNT(DISTINCT installation_id) AS unique_installations
FROM telemetry_events
GROUP BY 1, 2, 3
ORDER BY 1 DESC, 2, 3;

CREATE VIEW failure_clusters AS
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

CREATE VIEW company_distribution AS
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

CREATE VIEW placeholder_choice_mix AS
SELECT
  choice_type,
  COUNT(*) AS occurrences
FROM telemetry_events
WHERE event_type = 'placeholder_fill_choice'
GROUP BY 1;

CREATE VIEW fit_score_distribution AS
SELECT
  fit_score,
  COUNT(*) AS occurrences,
  AVG(num_placeholders_emitted) AS avg_placeholders
FROM telemetry_events
WHERE event_type = 'run_completed' AND fit_score IS NOT NULL
GROUP BY 1
ORDER BY 1;

CREATE VIEW seniority_distribution AS
SELECT
  job_seniority,
  COUNT(*) AS applications,
  AVG(fit_score) AS avg_fit
FROM telemetry_events
WHERE event_type IN ('run_completed', 'run_failed')
  AND job_seniority IS NOT NULL
GROUP BY 1;

-- Explicitly revoke anon access to views.
REVOKE SELECT ON daily_summary, failure_clusters, company_distribution,
               placeholder_choice_mix, fit_score_distribution,
               seniority_distribution
  FROM anon;

-- Retention: daily cleanup via pg_cron.
CREATE EXTENSION IF NOT EXISTS pg_cron;

CREATE OR REPLACE FUNCTION run_telemetry_retention() RETURNS void AS $$
BEGIN
  DELETE FROM telemetry_events WHERE received_at < now() - interval '90 days';
  DELETE FROM installations WHERE last_seen < now() - interval '180 days';
END;
$$ LANGUAGE plpgsql;

SELECT cron.schedule(
  'telemetry-retention-daily',
  '0 3 * * *',
  $$SELECT run_telemetry_retention()$$
);
