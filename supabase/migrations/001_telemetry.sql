-- resumasher telemetry schema
-- Events table: one row per telemetry event from the client.
-- Installations table: one row per installation_id (community tier only).
--
-- RLS model: no anon policies. Edge functions use SUPABASE_SERVICE_ROLE_KEY
-- internally (bypasses RLS). Anon REST calls are denied at both the GRANT
-- layer and the RLS layer (belt-and-suspenders). See migration 005.

CREATE TABLE telemetry_events (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  received_at TIMESTAMPTZ DEFAULT now(),
  schema_version INTEGER NOT NULL DEFAULT 1,
  event_type TEXT NOT NULL,
  event_timestamp TIMESTAMPTZ NOT NULL,

  -- Environment
  resumasher_version TEXT NOT NULL,
  host TEXT NOT NULL,
  os TEXT NOT NULL,
  arch TEXT,

  -- Correlation
  run_id TEXT,
  session_id TEXT,

  -- Identity (null under anonymous tier)
  installation_id TEXT,

  -- Event-specific payload
  duration_s NUMERIC,
  outcome TEXT,
  error_class TEXT,
  failed_phase SMALLINT,

  -- Tailor + application fields (run_completed / run_failed only)
  company_normalized TEXT,
  job_title_raw TEXT,
  job_seniority TEXT,
  fit_score SMALLINT,
  fit_strengths_count SMALLINT,
  fit_gaps_count SMALLINT,
  fit_recommendation TEXT,
  num_placeholders_emitted SMALLINT,
  used_multirole_format BOOLEAN,

  -- Configuration fields
  style_chosen TEXT,
  photo_included BOOLEAN,
  github_configured BOOLEAN,
  used_github_evidence BOOLEAN,
  used_folder_evidence BOOLEAN,
  github_repos_count SMALLINT,
  folder_files_count SMALLINT,
  jd_source_mode TEXT,
  resume_format_detected TEXT,
  install_scope_path TEXT,
  all_pdfs_rendered BOOLEAN,

  -- Phase 7 placeholder_fill_choice only
  choice_type TEXT,

  -- rerender_used only
  rerender_kind TEXT,

  -- First-run setup only
  setup_duration_s NUMERIC,
  setup_outcome TEXT,

  -- Ambient
  time_of_day_bucket TEXT
);

CREATE INDEX idx_telemetry_run ON telemetry_events (run_id, event_timestamp);
CREATE INDEX idx_telemetry_type_ts ON telemetry_events (event_type, event_timestamp);
CREATE INDEX idx_telemetry_error ON telemetry_events (error_class, resumasher_version)
  WHERE outcome = 'failure';
CREATE INDEX idx_telemetry_company ON telemetry_events (company_normalized)
  WHERE company_normalized IS NOT NULL;

CREATE TABLE installations (
  installation_id TEXT PRIMARY KEY,
  first_seen TIMESTAMPTZ DEFAULT now(),
  last_seen TIMESTAMPTZ DEFAULT now(),
  resumasher_version TEXT,
  host TEXT,
  os TEXT
);

-- Initial RLS state (INSERT policies for anon). Tightened further in 005.
ALTER TABLE telemetry_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE installations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon_insert_events" ON telemetry_events
  FOR INSERT WITH CHECK (true);
CREATE POLICY "anon_insert_installations" ON installations
  FOR INSERT WITH CHECK (true);
CREATE POLICY "anon_upsert_installations" ON installations
  FOR UPDATE USING (true) WITH CHECK (true);
