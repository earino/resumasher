// resumasher telemetry ingest
// POST body: JSON event or JSON array of events (up to 100).
// Validates schema, clamps field sizes, lowercases company name.
// Inserts events; upserts installations with updated last_seen.
// Returns {inserted: N, installations_upserted: N}.
//
// Auth model:
//   - Client sends legacy JWT anon key in Authorization header.
//   - Supabase gateway validates the JWT (verify_jwt: true).
//   - Edge function body uses SUPABASE_SERVICE_ROLE_KEY internally to
//     bypass RLS. The service role is never exposed to the client.
//   - RLS on the tables denies all anon reads/writes as a defense layer
//     (belt-and-suspenders, in case anyone tries to hit /rest/v1 directly).
//   - This edge function IS the validation layer and the only write path.
//
// Seniority classification is LLM-side; edge function only validates enum.
// Company normalization is lowercase + slice; no suffix stripping (GmbH/K.K./有限公司 etc.).

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const MAX_BATCH_SIZE = 100;
const MAX_PAYLOAD_BYTES = 50_000;

const VALID_EVENT_TYPES = new Set([
  "first_run_setup_completed",
  "run_started",
  "fit_computed",
  "tailor_completed",
  "placeholder_fill_choice",
  "run_completed",
  "run_failed",
  "rerender_used",
]);

const VALID_SENIORITY = new Set([
  "intern", "junior", "mid", "senior", "staff",
  "manager", "director", "vp", "cxo", "unknown"
]);

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return new Response("POST required", { status: 405 });

  const cl = parseInt(req.headers.get("content-length") || "0");
  if (cl > MAX_PAYLOAD_BYTES) return new Response("Payload too large", { status: 413 });

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL") ?? "",
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? ""
  );

  try {
    const body = await req.json();
    const events = Array.isArray(body) ? body : [body];
    if (events.length > MAX_BATCH_SIZE) {
      return new Response(`Batch too large (max ${MAX_BATCH_SIZE})`, { status: 400 });
    }

    const rows: Record<string, unknown>[] = [];
    const installUpserts = new Map<string, Record<string, unknown>>();

    for (const e of events) {
      if (e.v !== 1) continue;
      if (!VALID_EVENT_TYPES.has(e.event_type)) continue;
      if (!e.ts || !e.resumasher_version || !e.host || !e.os) continue;

      const row: Record<string, unknown> = {
        schema_version: e.v,
        event_type: e.event_type,
        event_timestamp: e.ts,
        resumasher_version: String(e.resumasher_version).slice(0, 20),
        host: String(e.host).slice(0, 20),
        os: String(e.os).slice(0, 20),
        arch: e.arch ? String(e.arch).slice(0, 20) : null,
        run_id: e.run_id ? String(e.run_id).slice(0, 50) : null,
        session_id: e.session_id ? String(e.session_id).slice(0, 50) : null,
        installation_id: e.installation_id ? String(e.installation_id).slice(0, 64) : null,
        duration_s: typeof e.duration_s === "number" ? e.duration_s : null,
        outcome: e.outcome ? String(e.outcome).slice(0, 20) : null,
        error_class: e.error_class ? String(e.error_class).slice(0, 50) : null,
        failed_phase: typeof e.failed_phase === "number" ? e.failed_phase : null,
        fit_score: typeof e.fit_score === "number" ? e.fit_score : null,
        fit_strengths_count: typeof e.fit_strengths_count === "number" ? e.fit_strengths_count : null,
        fit_gaps_count: typeof e.fit_gaps_count === "number" ? e.fit_gaps_count : null,
        fit_recommendation: e.fit_recommendation ? String(e.fit_recommendation).slice(0, 20) : null,
        num_placeholders_emitted: typeof e.num_placeholders_emitted === "number" ? e.num_placeholders_emitted : null,
        used_multirole_format: typeof e.used_multirole_format === "boolean" ? e.used_multirole_format : null,
        style_chosen: e.style_chosen ? String(e.style_chosen).slice(0, 10) : null,
        photo_included: typeof e.photo_included === "boolean" ? e.photo_included : null,
        github_configured: typeof e.github_configured === "boolean" ? e.github_configured : null,
        used_github_evidence: typeof e.used_github_evidence === "boolean" ? e.used_github_evidence : null,
        used_folder_evidence: typeof e.used_folder_evidence === "boolean" ? e.used_folder_evidence : null,
        github_repos_count: typeof e.github_repos_count === "number" ? e.github_repos_count : null,
        folder_files_count: typeof e.folder_files_count === "number" ? e.folder_files_count : null,
        jd_source_mode: e.jd_source_mode ? String(e.jd_source_mode).slice(0, 20) : null,
        resume_format_detected: e.resume_format_detected ? String(e.resume_format_detected).slice(0, 20) : null,
        install_scope_path: e.install_scope_path ? String(e.install_scope_path).slice(0, 40) : null,
        all_pdfs_rendered: typeof e.all_pdfs_rendered === "boolean" ? e.all_pdfs_rendered : null,
        choice_type: e.choice_type ? String(e.choice_type).slice(0, 20) : null,
        rerender_kind: e.rerender_kind ? String(e.rerender_kind).slice(0, 20) : null,
        setup_duration_s: typeof e.setup_duration_s === "number" ? e.setup_duration_s : null,
        setup_outcome: e.setup_outcome ? String(e.setup_outcome).slice(0, 30) : null,
        time_of_day_bucket: e.time_of_day_bucket ? String(e.time_of_day_bucket).slice(0, 20) : null,
        model: e.model ? String(e.model).slice(0, 40) : null,
      };

      // Company: lowercase + length cap. No suffix stripping (GmbH/K.K./有限公司 etc.).
      if (e.company) {
        row.company_normalized = String(e.company).toLowerCase().slice(0, 100);
      }

      // Job title: raw text (lowercased). Seniority is classified LLM-side.
      if (e.job_title) {
        row.job_title_raw = String(e.job_title).toLowerCase().slice(0, 100);
      }
      if (e.seniority) {
        const s = String(e.seniority).toLowerCase();
        row.job_seniority = VALID_SENIORITY.has(s) ? s : null;
      }

      rows.push(row);

      if (e.installation_id) {
        const id = String(e.installation_id).slice(0, 64);
        installUpserts.set(id, {
          installation_id: id,
          last_seen: new Date().toISOString(),
          resumasher_version: row.resumasher_version,
          host: row.host,
          os: row.os,
        });
      }
    }

    if (rows.length === 0) {
      return new Response(JSON.stringify({ inserted: 0 }), {
        status: 200, headers: { "Content-Type": "application/json" }
      });
    }

    const { error: insertError } = await supabase.from("telemetry_events").insert(rows);
    if (insertError) {
      return new Response(JSON.stringify({ error: insertError.message }), {
        status: 500, headers: { "Content-Type": "application/json" }
      });
    }

    const upsertErrors: string[] = [];
    for (const data of installUpserts.values()) {
      const { error } = await supabase.from("installations").upsert(data, { onConflict: "installation_id" });
      if (error) upsertErrors.push(error.message);
    }

    return new Response(JSON.stringify({
      inserted: rows.length,
      installations_upserted: installUpserts.size - upsertErrors.length,
      install_errors: upsertErrors.length ? upsertErrors : undefined,
    }), {
      status: 200, headers: { "Content-Type": "application/json" }
    });
  } catch (_err) {
    return new Response("Invalid request", { status: 400 });
  }
});
