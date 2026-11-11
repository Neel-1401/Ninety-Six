// Settings (Agent C): honest view of modes. LIVE only when a real provider is configured.
import { fmtDateTime } from "../lib/format";
import { Banner, Card, CardHeader, ErrorState, KeyValue, ModeBadge, PageHeader, Skeleton } from "../ui";
import { useSettings } from "./hooks";

export default function SettingsPage() {
  const q = useSettings();
  const s = q.data;
  return (
    <div>
      <PageHeader title="Settings" description="Tool modes, clock and environment. Credentials stay on the server and are never shown here." />
      {q.isLoading && <Skeleton className="h-40" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {s && (
        <div className="grid gap-5 xl:grid-cols-2">
          <Card>
            <CardHeader title="Environment" />
            <div className="px-4 py-4">
              {s.demo_mode && (
                <Banner tone="warning" className="mb-3" title="Demo mode">
                  Role switching, simulated sign-in codes, clock fast-forward and reset are enabled. They are disabled in production mode.
                </Banner>
              )}
              <KeyValue
                items={[
                  { k: "App mode", v: s.app_mode },
                  { k: "Hospital", v: s.hospital_name },
                  { k: "Emergency number", v: s.emergency_number },
                  { k: "Display time zone", v: s.display_timezone },
                  { k: "Simulated time", v: `${fmtDateTime(s.sim_now)} IST (offset +${Math.round(s.sim_offset_hours * 10) / 10} h)` },
                  { k: "Background worker", v: `${s.worker.enabled ? "Running" : "Disabled"} · last tick ${fmtDateTime(s.worker.last_tick_at, "not yet")}` },
                ]}
              />
            </div>
          </Card>
          <Card>
            <CardHeader title="Language model" description="The model only proposes structured output; deterministic code validates it and falls back to rules." />
            <div className="px-4 py-4">
              <KeyValue
                items={[
                  { k: "Provider", v: s.llm.provider },
                  { k: "Mode", v: <ModeBadge mode={s.llm.mode} /> },
                  { k: "Model", v: s.llm.model ?? "—" },
                  { k: "Configured", v: s.llm.configured ? "Yes" : "No — rule-based extraction, planning and triage are used" },
                ]}
              />
            </div>
          </Card>
          <Card className="xl:col-span-2">
            <CardHeader
              title="Tools"
              description="Simulated calls are recorded locally and never count as real actions. A failed live call stays failed or unknown."
            />
            <ul className="divide-y divide-line">
              {s.tools.map((t) => (
                <li key={t.name} className="grid gap-2 px-4 py-3 md:grid-cols-[minmax(10rem,14rem)_auto_1fr] md:items-start">
                  <span className="font-bold">{t.label}</span>
                  <ModeBadge mode={t.mode} />
                  <div className="text-sm">
                    <p>{t.detail}</p>
                    <p className="text-muted">
                      {t.configured ? "Credentials configured" : "Not configured"}
                      {t.last_live_success_at ? ` · last live success ${fmtDateTime(t.last_live_success_at)}` : ""}
                      {t.last_failure_at ? ` · last failure ${fmtDateTime(t.last_failure_at)}` : ""}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
            <div className="border-t border-line px-4 py-3 text-sm">
              <span className="font-bold">DEMO_PHONE redirect: </span>
              {s.demo_phone_redirect.active
                ? `active — every outbound patient or caregiver message goes to ${s.demo_phone_redirect.masked}.`
                : "not set — live messaging refuses to send to real recipients unless explicitly allowed on the server."}
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
