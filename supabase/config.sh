#!/usr/bin/env bash
# Supabase project config for resumasher telemetry.
#
# These values are PUBLIC. Safe to commit. RLS denies anon direct access to
# all tables; the only write path is the /functions/v1/telemetry-ingest
# edge function, which uses SUPABASE_SERVICE_ROLE_KEY server-side (that key
# is NOT in this repo).
#
# The key below is the legacy JWT anon key (eyJ...) rather than the newer
# publishable key (sb_publishable_...) because edge functions have
# verify_jwt: true, which requires a JWT for the gateway validation layer.
#
# If you need to rotate: create a new legacy anon key in the Supabase
# dashboard (API settings), update this file, and bump RESUMASHER_VERSION.
# Old clients with the previous key will gracefully fail-silent.

RESUMASHER_SUPABASE_URL="https://ippinwwsgcycddqbnrnf.supabase.co"
RESUMASHER_SUPABASE_PROJECT_REF="ippinwwsgcycddqbnrnf"
RESUMASHER_SUPABASE_ANON_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImlwcGlud3dzZ2N5Y2RkcWJucm5mIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY1ODc5NjcsImV4cCI6MjA5MjE2Mzk2N30.QLuy-K2g1Cz3wqMRrJC-_Ol0WWnAuQA6JxUCFq-Y1uE"
RESUMASHER_SUPABASE_REGION="eu-west-1"
