-- Drop the HAVING COUNT(*) >= 3 low-count threshold on company_distribution.
--
-- The threshold was meant to suppress unique applications (e.g., "one
-- student applied to one Finnish AI startup") from the view. But:
--   - At cohort scale (19 students) the threshold will never be met,
--     so the view is empty and useless during the first month.
--   - The maintainer has service_role access and can query telemetry_events
--     directly, bypassing the view entirely. The threshold is theater.
--
-- Under our consent model, the student actively opts in to "company name
-- logged (lowercased)". No threshold required at the view layer.

DROP VIEW IF EXISTS company_distribution;

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
ORDER BY total_applications DESC;

REVOKE SELECT ON company_distribution FROM anon;
