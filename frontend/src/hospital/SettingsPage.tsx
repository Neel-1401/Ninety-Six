// Settings (Agent C): honest view of modes. LIVE only when a real provider is configured.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { PowerOff, Sparkles } from "lucide-react";
import { api, errorMessage } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import { qk } from "../lib/queryKeys";
import type { LlmStatus } from "../lib/types";
import { Badge, Banner, Button, Card, CardHeader, ErrorState, KeyValue, ModeBadge, PageHeader, Skeleton } from "../ui";
import { useSettings } from "./hooks";

export default function SettingsPage() {
  const q = useSettings();
  const s = q.data;
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.post<LlmStatus>("/api/settings/llm", { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.settings() }),
  });
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
            <CardHeader
              title="AI (language model)"
              description="Hybrid: rules first. The AI is used only for weak summary parsing, condition-specific plan additions, free-text triage (can only raise the level), concern / red-flag reply drafts and translations (always approved), and the 96-hour note."
            />
            <div className="px-4 py-4">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <Badge tone={s.llm.live ? "primary" : "neutral"} icon={s.llm.live ? Sparkles : PowerOff} className="text-[15px]">
                  {s.llm.live ? "LIVE" : "OFF"}
                </Badge>
                <span className="font-bold">{s.llm.model ?? s.llm.env_model ?? "rules only"}</span>
                <Button
                  size="sm"
                  variant={s.llm.runtime_enabled === false ? "primary" : "secondary"}
                  disabled={toggle.isPending || (s.llm.runtime_enabled !== false && !s.llm.live)}
                  onClick={() => toggle.mutate(s.llm.runtime_enabled === false)}
                >
                  {s.llm.runtime_enabled === false ? "Turn AI on" : "Turn AI off (rules only)"}
                </Button>
              </div>
              <KeyValue
                items={[
                  { k: "Provider (env)", v: s.llm.provider },
                  { k: "Model", v: s.llm.model ?? s.llm.env_model ?? "—" },
                  { k: "Status", v: s.llm.detail ?? (s.llm.configured ? "Configured" : "Not configured") },
                  { k: "Guardrails", v: `${s.llm.timeout_seconds ?? 8} s hard timeout · strict JSON schema · max 1 retry · rules fallback · ${s.llm.cache_entries ?? 0} cached calls` },
                  { k: "Instant off", v: "This switch, or LLM_PROVIDER=mock in backend/.env" },
                ]}
              />
              {toggle.isError && <p className="mt-2 text-sm text-urgent-ink">{errorMessage(toggle.error)}</p>}
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
