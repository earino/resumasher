-- Widen the junior bucket to catch "entry" as a standalone seniority value.
--
-- The 009 CASE WHEN required "%entry%level%" — i.e. both "entry" and "level"
-- had to appear. That over-specificity missed LLMs that emit just "entry" as
-- shorthand (observed in live data 2026-04-20: one row with raw_value="entry"
-- fell through to the "other" bucket).
--
-- Fix: use "%entry%" instead of "%entry%level%". Catches "entry", "entry-level",
-- "entry level", "entry-level analyst", etc. No false-positive risk because
-- the seniority field is the classified level, not the raw job title — a "Data
-- Entry Clerk" role would have its seniority classified by the LLM as "junior"
-- or "intern", not emitted as the literal title.
--
-- Also recreates the entire telemetry_stats() function (CREATE OR REPLACE) —
-- one source of truth, easy to diff against 009.

CREATE OR REPLACE FUNCTION public.telemetry_stats()
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
  result JSON;
BEGIN
  SELECT json_build_object(
    'generated_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),

    'summary', (
      SELECT json_build_object(
        'total_events', COUNT(*),
        'total_runs', COUNT(*) FILTER (WHERE event_type = 'run_completed'),
        'total_failures', COUNT(*) FILTER (WHERE event_type = 'run_failed'),
        'total_installations', (SELECT COUNT(*) FROM installations),
        'first_event_at', to_char(MIN(event_timestamp) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'latest_event_at', to_char(MAX(received_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
      )
      FROM telemetry_events
    ),

    'runs_per_day', COALESCE((
      SELECT json_agg(t ORDER BY day)
      FROM (
        SELECT
          to_char(DATE_TRUNC('day', event_timestamp), 'YYYY-MM-DD') AS day,
          COUNT(*) AS runs
        FROM telemetry_events
        WHERE event_type IN ('run_completed', 'run_failed')
          AND event_timestamp > now() - interval '90 days'
        GROUP BY 1
        ORDER BY 1
      ) t
    ), '[]'::json),

    'host_distribution', COALESCE((
      SELECT json_agg(t ORDER BY runs DESC)
      FROM (
        SELECT
          COALESCE(host, 'unknown') AS host,
          COUNT(*) AS runs
        FROM telemetry_events
        WHERE event_type = 'run_completed'
        GROUP BY 1
      ) t
    ), '[]'::json),

    'model_distribution', COALESCE((
      SELECT json_agg(t ORDER BY runs DESC)
      FROM (
        SELECT
          model,
          COUNT(*) AS runs
        FROM telemetry_events
        WHERE event_type = 'run_completed'
          AND model IS NOT NULL
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT 15
      ) t
    ), '[]'::json),

    'fit_score_distribution', COALESCE((
      SELECT json_agg(t ORDER BY score)
      FROM (
        SELECT
          fit_score AS score,
          COUNT(*) AS runs
        FROM telemetry_events
        WHERE event_type IN ('run_completed', 'fit_computed')
          AND fit_score IS NOT NULL
        GROUP BY 1
      ) t
    ), '[]'::json),

    'seniority_distribution', COALESCE((
      SELECT json_agg(t ORDER BY runs DESC)
      FROM (
        SELECT
          CASE
            WHEN LOWER(job_seniority) = 'intern' OR LOWER(job_seniority) LIKE '%intern%' OR LOWER(job_seniority) LIKE '%praktik%' THEN 'intern'
            WHEN LOWER(job_seniority) = 'junior' OR LOWER(job_seniority) LIKE '%junior%' OR LOWER(job_seniority) LIKE '%early%career%' OR LOWER(job_seniority) LIKE '%graduate%' OR LOWER(job_seniority) LIKE '%entry%' OR LOWER(job_seniority) LIKE '%associate%' THEN 'junior'
            WHEN LOWER(job_seniority) = 'mid' OR LOWER(job_seniority) = 'middle' OR LOWER(job_seniority) LIKE '%mid%level%' THEN 'mid'
            WHEN LOWER(job_seniority) = 'senior' OR LOWER(job_seniority) LIKE '%senior%' OR LOWER(job_seniority) LIKE 'sr%' THEN 'senior'
            WHEN LOWER(job_seniority) = 'staff' OR LOWER(job_seniority) LIKE '%staff%' OR LOWER(job_seniority) LIKE '%principal%' OR LOWER(job_seniority) LIKE '%lead%' THEN 'staff'
            WHEN LOWER(job_seniority) = 'manager' OR LOWER(job_seniority) LIKE '%manag%' THEN 'manager'
            WHEN LOWER(job_seniority) = 'director' OR LOWER(job_seniority) LIKE '%director%' THEN 'director'
            WHEN LOWER(job_seniority) = 'vp' OR LOWER(job_seniority) LIKE '%vp%' OR LOWER(job_seniority) LIKE '%vice%president%' THEN 'vp'
            WHEN LOWER(job_seniority) IN ('cxo', 'ceo', 'cto', 'cfo', 'coo') OR LOWER(job_seniority) LIKE '%chief%' THEN 'cxo'
            WHEN LOWER(job_seniority) = 'unknown' THEN 'unknown'
            ELSE 'other'
          END AS bucket,
          COUNT(*) AS runs
        FROM telemetry_events
        WHERE job_seniority IS NOT NULL
          AND event_type IN ('run_completed', 'fit_computed')
        GROUP BY 1
      ) t
    ), '[]'::json),

    'placeholder_choice_mix', COALESCE((
      SELECT json_agg(t ORDER BY count DESC)
      FROM (
        SELECT
          choice_type AS choice,
          COUNT(*) AS count
        FROM telemetry_events
        WHERE event_type = 'placeholder_fill_choice'
          AND choice_type IS NOT NULL
        GROUP BY 1
      ) t
    ), '[]'::json),

    'failure_by_phase', COALESCE((
      SELECT json_agg(t ORDER BY phase, count DESC)
      FROM (
        SELECT
          failed_phase AS phase,
          COALESCE(error_class, 'unknown') AS error_class,
          COUNT(*) AS count
        FROM telemetry_events
        WHERE event_type = 'run_failed'
        GROUP BY 1, 2
      ) t
    ), '[]'::json)

  ) INTO result;

  RETURN result;
END;
$$;

-- Permissions identical to 009 — CREATE OR REPLACE preserves existing GRANTs,
-- but restating them is defensive in case the function was dropped + recreated.
REVOKE EXECUTE ON FUNCTION public.telemetry_stats() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.telemetry_stats() TO anon;
GRANT EXECUTE ON FUNCTION public.telemetry_stats() TO authenticated;
