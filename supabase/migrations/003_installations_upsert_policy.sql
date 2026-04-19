-- Attempted fix for PostgREST upsert via anon key.
--
-- Rationale when written: the anon_upsert_installations policy had USING
-- (true) + WITH CHECK (true), but ON CONFLICT DO UPDATE was still failing
-- with 42501 on direct REST calls. Hypothesis: PostgREST's upsert path
-- wants policies scoped TO anon explicitly, not the default public role.
--
-- Follow-up: this alone did not fix the RLS gotcha. Migration 005
-- ultimately solved it by shifting all writes to the service role
-- (bypassing RLS entirely) inside the edge function.
--
-- Keeping this migration in the history for reproducibility; 005 supersedes.

DROP POLICY IF EXISTS "anon_insert_installations" ON installations;
DROP POLICY IF EXISTS "anon_upsert_installations" ON installations;

CREATE POLICY "anon_insert_installations" ON installations
  FOR INSERT TO anon WITH CHECK (true);

CREATE POLICY "anon_update_installations" ON installations
  FOR UPDATE TO anon USING (true) WITH CHECK (true);

REVOKE UPDATE ON installations FROM anon;
GRANT UPDATE (last_seen, resumasher_version, host, os) ON installations TO anon;
