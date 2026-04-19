-- Add `model` column to telemetry_events for tracking which LLM the student
-- is using (e.g. claude-opus-4-7, gpt-5-codex, gemini-2.5-pro). The value
-- is self-reported by the AI orchestrator running the pipeline — the LLM
-- knows what it is, and SKILL.md instructs it to pass the value through
-- `--model` on every telemetry-log call.
--
-- Nullable: pre-v0.2.1 events do not have this field, and the log script
-- omits the JSON field entirely if --model is not provided (so old clients
-- continue to work).
--
-- No enum validation: the value space is too large and moves too fast
-- (Anthropic / OpenAI / Google all ship new model IDs on their own
-- schedules). Edge function caps the string at 40 chars; anything else
-- is accepted as-is.

ALTER TABLE telemetry_events ADD COLUMN model TEXT;

-- Useful index for "which model has highest fit scores" / "which model
-- fails more often" dashboard queries.
CREATE INDEX idx_telemetry_model ON telemetry_events (model, event_type)
  WHERE model IS NOT NULL;
