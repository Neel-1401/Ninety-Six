// Small presentation helpers for the hospital UI (Agent C).
import type { FactOut, PlanStepOut } from "../lib/types";

export const ACTION_LABEL: Record<string, string> = {
  send_message: "Send message",
  create_calendar_event: "Create calendar event",
  care_team_alert: "Alert care team (email)",
  open_checkin: "Open website check-in",
  close_checkin: "Close check-in window",
  medication_review: "Medication information review",
  staff_review: "Staff review",
  generate_summary: "Write 96-hour summary",
  end_window: "End monitoring window",
};

export function actionLabel(t: string): string {
  return ACTION_LABEL[t] ?? t.replace(/_/g, " ");
}

export const RECIPIENT_LABEL: Record<string, string> = {
  patient: "Patient",
  caregiver: "Caregiver",
  care_team: "Care team",
  calendar: "Hospital calendar",
};

export const SOURCE_KIND_LABEL: Record<string, string> = {
  extracted: "Extracted from summary",
  staff_entered: "Entered by staff",
  staff_corrected: "Corrected by staff",
  patient_reported: "Reported by patient",
  inferred: "Inferred by agent rules",
  tool: "From a tool lookup",
};

export const CATEGORY_LABEL: Record<string, string> = {
  diagnosis: "Diagnosis",
  allergy: "Allergies",
  medication: "Medicines",
  follow_up: "Follow-up instructions",
  red_flag: "Warning signs (red flags)",
  instruction: "Other instructions",
  social: "Social context",
  risk_input: "Risk inputs",
  medication_info: "Medication information (tool)",
  symptom: "Patient-reported symptoms",
  adherence: "Medicine adherence",
  note: "Notes",
  missing: "Missing information",
};

export const CATEGORY_ORDER = [
  "diagnosis",
  "allergy",
  "medication",
  "follow_up",
  "red_flag",
  "instruction",
  "social",
  "risk_input",
  "medication_info",
  "symptom",
  "adherence",
  "note",
  "missing",
];

export const TRIGGER_LABEL: Record<string, string> = {
  initial: "Initial plan",
  tool_finding: "Tool finding",
  patient_response: "Patient response",
  staff_correction: "Staff correction",
  rejected_action: "Rejected action",
  manual: "Manual replan request",
};

export const REVIEW_KIND_LABEL: Record<string, string> = {
  urgent_concern: "Urgent concern",
  concern: "Concern",
  failed_action: "Failed action",
  unknown_outcome: "Unknown outcome",
  missed_checkin: "Missed check-in",
  medication_review: "Medicine combination review",
  processing_failure: "Processing failure",
  closure_review: "Closure review",
};

export const TEMPLATE_LABEL: Record<string, string> = {
  checkin_reminder_v1: "Check-in reminder (non-clinical)",
  closing_v1: "End-of-monitoring message (non-clinical)",
  welcome_v1: "Welcome + reviewed medicines",
  concern_reply_v1: "Reply to a concern",
  urgent_safety_v1: "Urgent safety advice",
  caregiver_update_v1: "Caregiver update",
  care_team_alert_v1: "Care-team alert",
};

/** Text for any fact value. */
export function factText(f: Pick<FactOut, "value" | "is_missing" | "category">): string {
  if (f.is_missing) return "Not provided";
  const v = f.value ?? {};
  if (f.category === "medication") {
    const parts = [v.name, v.dose, v.frequency].filter((x) => typeof x === "string" && x.trim());
    const instr = typeof v.instructions === "string" && v.instructions.trim() ? ` — ${v.instructions}` : "";
    return `${parts.join(" ") || "(unnamed medicine)"}${instr}`;
  }
  if (f.category === "follow_up") {
    const bits = [v.title as string];
    if (v.provider) bits.push(`with ${v.provider as string}`);
    if (v.within_days != null) bits.push(`within ${v.within_days as number} days`);
    if (v.location) bits.push(`at ${v.location as string}`);
    return bits.filter(Boolean).join(" ");
  }
  if (typeof v.text === "string") return v.text;
  const entries = Object.entries(v).filter(([, x]) => x != null && x !== "");
  return entries.map(([k, x]) => `${k.replace(/_/g, " ")}: ${typeof x === "object" ? JSON.stringify(x) : String(x)}`).join("; ");
}

/** Line number from a source ref like "document:3#L12" (or null). */
export function sourceLine(ref: string | null | undefined): number | null {
  if (!ref) return null;
  const m = /#L(\d+)/.exec(ref);
  return m ? Number(m[1]) : null;
}

export function stepTemplateId(s: Pick<PlanStepOut, "arguments">): string | null {
  const a = s.arguments ?? {};
  const t = (a.template_id ?? a.template) as unknown;
  return typeof t === "string" ? t : null;
}

export function stepMessageBody(s: Pick<PlanStepOut, "arguments">): string | null {
  const a = s.arguments ?? {};
  const b = (a.body ?? a.message ?? a.text) as unknown;
  return typeof b === "string" ? b : null;
}

/** Lane of the 96-hour timeline for a step. */
export function laneOf(actionType: string): "messages" | "checkins" | "external" | "internal" {
  if (actionType === "send_message") return "messages";
  if (actionType === "open_checkin" || actionType === "close_checkin") return "checkins";
  if (actionType === "create_calendar_event" || actionType === "care_team_alert") return "external";
  return "internal";
}

export const PAST_STEP = new Set(["executed", "failed", "unknown", "skipped", "superseded", "cancelled"]);

export function sexLabel(s: string | null | undefined): string {
  if (!s) return "";
  const m: Record<string, string> = { F: "F", M: "M", female: "F", male: "M" };
  return m[s] ?? s;
}

export function pluralise(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}
