// resumasher telemetry delete (right-to-erasure endpoint).
// POST body: {installation_id: "uuid"}
// Deletes all rows matching installation_id from both tables.
// Uses service role internally (anon has no DELETE privilege via RLS).
//
// No authentication beyond the installation_id itself: if you hold the UUID,
// you own the data. This matches GDPR right-to-erasure without requiring
// any personal data (no email, no login) to invoke.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return new Response("POST required", { status: 405 });

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL") ?? "",
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? ""
  );

  try {
    const { installation_id } = await req.json();
    if (!installation_id || typeof installation_id !== "string") {
      return new Response("installation_id required", { status: 400 });
    }

    const id = String(installation_id).slice(0, 64);

    const { count: eventsDeleted, error: eventsError } = await supabase
      .from("telemetry_events")
      .delete({ count: "exact" })
      .eq("installation_id", id);
    if (eventsError) {
      return new Response(JSON.stringify({ error: eventsError.message }), {
        status: 500, headers: { "Content-Type": "application/json" }
      });
    }

    const { count: installsDeleted, error: installsError } = await supabase
      .from("installations")
      .delete({ count: "exact" })
      .eq("installation_id", id);
    if (installsError) {
      return new Response(JSON.stringify({ error: installsError.message }), {
        status: 500, headers: { "Content-Type": "application/json" }
      });
    }

    return new Response(JSON.stringify({
      deleted_events: eventsDeleted ?? 0,
      deleted_installations: installsDeleted ?? 0,
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  } catch (_err) {
    return new Response("Invalid request", { status: 400 });
  }
});
