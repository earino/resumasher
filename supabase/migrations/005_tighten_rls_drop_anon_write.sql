-- Final RLS model: anon has NO direct-table privileges. The only write path
-- is the /functions/v1/telemetry-ingest edge function, which uses
-- SUPABASE_SERVICE_ROLE_KEY internally (bypasses RLS).
--
-- This avoids a PostgREST-specific upsert-vs-RLS gotcha and matches gstack's
-- live pattern. The anon key is purely a gateway ticket for verify_jwt; it
-- cannot read, write, or update any telemetry data.

DROP POLICY IF EXISTS "anon_insert_events" ON telemetry_events;
DROP POLICY IF EXISTS "anon_insert_installations" ON installations;
DROP POLICY IF EXISTS "anon_upsert_installations" ON installations;
DROP POLICY IF EXISTS "anon_update_installations" ON installations;

-- REVOKE all direct-write privileges. Service role retains everything.
REVOKE INSERT, UPDATE, DELETE ON telemetry_events FROM anon;
REVOKE INSERT, UPDATE, DELETE ON installations FROM anon;
REVOKE SELECT ON telemetry_events FROM anon;
REVOKE SELECT ON installations FROM anon;
