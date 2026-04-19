-- Partial revert of 003: the column-level GRANT conflicted with PostgREST's
-- upsert path (which writes all body columns, not just the restricted set).
-- Restoring full UPDATE grant. Migration 005 supersedes this entire line
-- of attempts by dropping anon writes altogether.

GRANT UPDATE ON installations TO anon;
