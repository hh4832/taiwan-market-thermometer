const RAW_PAYLOAD = "https://raw.githubusercontent.com/hh4832/taiwan-market-thermometer/main/public/data/work_site_payload.json";

const requiredSections = ["provenance", "forecast_calendar", "target_date_calendar", "latest_signals", "historical_validation", "research_evidence", "system_health"];
const forecastSections = ["next_trading_day_forecast", "current_day_validation", "post_close_forward_signals", "signal_identity"];
const supportedSchemas = new Set(["1.0", "1.1"]);

export async function GET() {
  try {
    const response = await fetch(RAW_PAYLOAD, { cache: "no-store", headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`GitHub publication returned ${response.status}`);
    const payload = await response.json() as Record<string, unknown>;
    if (!supportedSchemas.has(String(payload.schema_version)) || requiredSections.some(section => !(section in payload))) throw new Error("GitHub publication schema is invalid");
    if (payload.schema_version === "1.1" && forecastSections.some(section => !(section in payload))) throw new Error("GitHub forecast publication schema is incomplete");
    const provenance = payload.provenance as Record<string, unknown> | undefined;
    if (!provenance || provenance.pipeline_status !== "SUCCESS" || provenance.freshness !== "FRESH") throw new Error("GitHub publication provenance is not publishable");
    return Response.json(payload, { headers: { "Cache-Control": "no-store, max-age=0" } });
  } catch (error) {
    const message = error instanceof Error ? error.message : "publication payload unavailable";
    return Response.json({ status: "DATA_UNAVAILABLE", message }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
