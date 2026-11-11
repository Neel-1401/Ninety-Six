// Display formatting (lead-owned). Backend sends UTC; we display Asia/Kolkata, 12-hour clock.
import type { ISO } from "./types";

export const DISPLAY_TZ = "Asia/Kolkata";

const dateTimeFmt = new Intl.DateTimeFormat("en-IN", {
  timeZone: DISPLAY_TZ,
  weekday: "short",
  day: "numeric",
  month: "short",
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
});
const timeFmt = new Intl.DateTimeFormat("en-IN", {
  timeZone: DISPLAY_TZ,
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
});
const dateFmt = new Intl.DateTimeFormat("en-IN", {
  timeZone: DISPLAY_TZ,
  weekday: "short",
  day: "numeric",
  month: "short",
  year: "numeric",
});

function toDate(v: ISO | Date | null | undefined): Date | null {
  if (!v) return null;
  const d = v instanceof Date ? v : new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "Wed, 11 Nov, 8:00 pm" */
export function fmtDateTime(v: ISO | Date | null | undefined, fallback = "—"): string {
  const d = toDate(v);
  return d ? dateTimeFmt.format(d).replace(/\b(am|pm)\b/i, (m) => m.toUpperCase()) : fallback;
}

/** "8:00 PM" */
export function fmtTime(v: ISO | Date | null | undefined, fallback = "—"): string {
  const d = toDate(v);
  return d ? timeFmt.format(d).replace(/\b(am|pm)\b/i, (m) => m.toUpperCase()) : fallback;
}

/** "Wed, 11 Nov 2026" */
export function fmtDate(v: ISO | Date | null | undefined, fallback = "—"): string {
  const d = toDate(v);
  return d ? dateFmt.format(d) : fallback;
}

/** "T+24 h", "T+1.5 h" — hours after discharge. */
export function fmtTPlus(hours: number | null | undefined): string {
  if (hours == null || Number.isNaN(hours)) return "T+—";
  const h = Math.round(hours * 10) / 10;
  return `T+${Number.isInteger(h) ? h : h.toFixed(1)} h`;
}

/** Human duration "3 h 20 min", "2 days 4 h". */
export function fmtDuration(hours: number): string {
  const totalMin = Math.max(0, Math.round(hours * 60));
  const d = Math.floor(totalMin / (60 * 24));
  const h = Math.floor((totalMin % (60 * 24)) / 60);
  const m = totalMin % 60;
  if (d > 0) return `${d} day${d === 1 ? "" : "s"}${h ? ` ${h} h` : ""}`;
  if (h > 0) return `${h} h${m ? ` ${m} min` : ""}`;
  return `${m} min`;
}

/** Relative to a reference time (simulated "now"): "in 3 h", "2 h ago". */
export function fmtRelative(v: ISO | null | undefined, now: ISO | Date | null | undefined): string {
  const d = toDate(v);
  const n = toDate(now ?? new Date());
  if (!d || !n) return "—";
  const diffH = (d.getTime() - n.getTime()) / 3_600_000;
  if (Math.abs(diffH) < 1 / 60) return "now";
  return diffH > 0 ? `in ${fmtDuration(diffH)}` : `${fmtDuration(-diffH)} ago`;
}

/** Waiting time label for review items. */
export function fmtWaiting(minutes: number): string {
  return fmtDuration(minutes / 60);
}

/** Show a phone number with the middle digits hidden: +91 •••••• 0001 */
export function maskPhone(p: string | null | undefined): string {
  if (!p) return "—";
  const digits = p.replace(/[^\d+]/g, "");
  if (digits.length < 6) return p;
  return `${digits.slice(0, 3)} •••••• ${digits.slice(-4)}`;
}

/** "datetime-local" input value (Asia/Kolkata) <-> ISO UTC. */
export function toLocalInputValue(v: ISO | Date | null | undefined): string {
  const d = toDate(v);
  if (!d) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: DISPLAY_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "00";
  const hour = get("hour") === "24" ? "00" : get("hour");
  return `${get("year")}-${get("month")}-${get("day")}T${hour}:${get("minute")}`;
}

/** Interpret a datetime-local string as Asia/Kolkata (UTC+05:30, no DST) and return ISO UTC. */
export function fromLocalInputValue(local: string): ISO | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(local)) return null;
  const d = new Date(`${local.slice(0, 16)}:00+05:30`);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}
