// Extraction review (Agent C): original summary beside editable extracted facts. Every fact shows
// where it came from (source line + excerpt + method). Missing fields are marked "not provided" —
// staff may enter them from the real record, the agent never invents them.
import { useQueryClient } from "@tanstack/react-query";
import { History, Pencil, Plus, Quote, Trash2 } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { errorMessage } from "../../lib/api";
import { fmtDateTime } from "../../lib/format";
import type { EpisodeDetail, FactOut } from "../../lib/types";
import { Badge, Banner, Button, Card, CardHeader, Checkbox, Dialog, Field, Select, StatusBadge, TextArea, TextInput, cn, useToast } from "../../ui";
import { factsApi, invalidateScopes } from "../hooks";
import { CATEGORY_LABEL, CATEGORY_ORDER, factText, SOURCE_KIND_LABEL, sourceLine } from "../util";

const EDITABLE_CATEGORIES = ["diagnosis", "allergy", "medication", "follow_up", "red_flag", "instruction", "social"];

export function FactsReview({ ep, editable }: { ep: EpisodeDetail; editable: boolean }) {
  const [showHistory, setShowHistory] = useState(false);
  const [highlight, setHighlight] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const docRef = useRef<HTMLOListElement>(null);
  const lines = useMemo(() => (ep.document?.text ?? "").split(/\r?\n/), [ep.document?.text]);

  const facts = ep.facts.filter((f) => showHistory || f.active);
  const groups = useMemo(() => {
    const m = new Map<string, FactOut[]>();
    for (const f of facts) {
      const k = f.is_missing && !CATEGORY_ORDER.includes(f.category) ? "missing" : f.category;
      m.set(k, [...(m.get(k) ?? []), f]);
    }
    return [...m.entries()].sort((a, b) => {
      const ia = CATEGORY_ORDER.indexOf(a[0]);
      const ib = CATEGORY_ORDER.indexOf(b[0]);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
  }, [facts]);
  const missingCount = ep.facts.filter((f) => f.active && f.is_missing).length;

  function jumpTo(line: number) {
    setHighlight(line);
    docRef.current?.querySelector<HTMLElement>(`[data-line="${line}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
      <Card className="xl:sticky xl:top-20 xl:self-start">
        <CardHeader title="Original discharge summary" description={ep.document ? `As pasted · ${fmtDateTime(ep.document.created_at)}` : undefined} />
        {ep.document ? (
          <ol ref={docRef} aria-label="Discharge summary lines" className="max-h-[70vh] overflow-auto py-2 font-mono text-[13px] leading-6">
            {lines.map((l, i) => (
              <li
                key={i}
                data-line={i + 1}
                className={cn("grid grid-cols-[3rem_1fr] px-2", highlight === i + 1 && "bg-attention")}
              >
                <span aria-hidden className="select-none pr-2 text-right text-faint">
                  {i + 1}
                </span>
                <span className="whitespace-pre-wrap break-words">{l || " "}</span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="px-4 py-3 text-muted">No document stored.</p>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Extracted information"
          description={
            <>
              Method: <strong>{ep.extraction_method ?? "unknown"}</strong>
              {ep.extraction_method?.includes("fallback") ? " — the language model was unavailable or its output failed validation, so deterministic rules were used." : ""}
            </>
          }
          actions={
            <>
              <Checkbox label="Show corrected history" checked={showHistory} onChange={(e) => setShowHistory(e.target.checked)} />
              {editable && (
                <Button size="sm" variant="secondary" icon={<Plus aria-hidden className="size-4" />} onClick={() => setAdding(true)}>
                  Add missing item
                </Button>
              )}
            </>
          }
        />
        <div className="space-y-4 px-4 py-4">
          {ep.extraction_notes.length > 0 && (
            <Banner tone="warning" title="Extraction notes (ambiguities and caveats)">
              <ul className="list-disc pl-5">
                {ep.extraction_notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </Banner>
          )}
          {missingCount > 0 && (
            <p className="text-sm text-attention-ink">
              <strong>{missingCount}</strong> item{missingCount === 1 ? " is" : "s are"} marked “not provided”. Enter them only from the actual record — nothing is
              invented.
            </p>
          )}
          {groups.map(([cat, list]) => (
            <section key={cat} aria-labelledby={`cat-${cat}`}>
              <h3 id={`cat-${cat}`} className="mb-2 text-sm font-bold uppercase tracking-wide text-muted">
                {CATEGORY_LABEL[cat] ?? cat} <span className="font-normal normal-case">({list.length})</span>
              </h3>
              <ul className="space-y-2">
                {list.map((f) => (
                  <FactRow key={f.id} ep={ep} f={f} editable={editable && f.active && EDITABLE_CATEGORIES.includes(f.category)} onJump={jumpTo} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      </Card>
      <AddFactDialog ep={ep} open={adding} onClose={() => setAdding(false)} />
    </div>
  );
}

function FactRow({ ep, f, editable, onJump }: { ep: EpisodeDetail; f: FactOut; editable: boolean; onJump: (line: number) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const line = sourceLine(f.source_ref);

  function startEdit() {
    const v = f.value ?? {};
    if (f.category === "medication") setDraft({ name: str(v.name), dose: str(v.dose), frequency: str(v.frequency), instructions: str(v.instructions) });
    else if (f.category === "follow_up")
      setDraft({ title: str(v.title), provider: str(v.provider), within_days: v.within_days != null ? String(v.within_days) : "", location: str(v.location) });
    else setDraft({ text: f.is_missing ? "" : str(v.text) });
    setReason("");
    setErr(null);
    setEditing(true);
  }

  async function save() {
    const value = toValue(f.category, draft);
    if (!value) {
      setErr(f.category === "medication" ? "A medicine needs at least a name." : "Enter a value.");
      return;
    }
    setBusy(true);
    try {
      await factsApi.patch(ep.id, f.id, { value, reason: reason.trim() || undefined });
      toast({ tone: "success", text: "Correction saved as a new version of this fact." });
      setEditing(false);
      await invalidateScopes(qc, ["episode", "memory", "plan", "activity"], ep.id);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function reject() {
    if (!reason.trim()) {
      setErr("Say why this item is wrong.");
      return;
    }
    setBusy(true);
    try {
      await factsApi.reject(ep.id, f.id, reason.trim());
      toast({ tone: "success", text: "Item rejected." });
      setRejecting(false);
      await invalidateScopes(qc, ["episode", "memory", "plan", "activity"], ep.id);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className={cn("rounded-md border px-3 py-2.5", f.is_missing ? "border-attention-line bg-attention/40" : "border-line", !f.active && "opacity-70")}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm text-muted">{f.label}</p>
          <p className={cn("font-bold", f.is_missing && "text-attention-ink")}>{factText(f)}</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {f.is_missing && <Badge tone="attention">Not provided</Badge>}
          {!f.active && (
            <Badge tone="neutral" icon={History}>
              Superseded
            </Badge>
          )}
          <StatusBadge domain="fact" value={f.review_state} />
        </div>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        <span>{SOURCE_KIND_LABEL[f.source_kind] ?? f.source_kind}</span>
        {f.extraction_method && <span>method: {f.extraction_method}</span>}
        {f.source_ref &&
          (line ? (
            <button type="button" onClick={() => onJump(line)} className="font-bold text-primary-ink underline cursor-pointer">
              {f.source_ref} (show line {line})
            </button>
          ) : (
            <span>{f.source_ref}</span>
          ))}
        {f.reviewed_by && <span>reviewed by {f.reviewed_by}</span>}
        <span>{fmtDateTime(f.created_at)}</span>
      </div>
      {f.source_excerpt && (
        <p className="mt-1.5 flex items-start gap-1.5 rounded bg-sunken px-2 py-1 font-mono text-xs text-neutral-ink">
          <Quote aria-hidden className="mt-0.5 size-3 shrink-0" />
          <span className="break-words">{f.source_excerpt}</span>
        </p>
      )}

      {editable && !editing && !rejecting && (
        <div className="mt-2 flex gap-2">
          <Button size="sm" variant="ghost" icon={<Pencil aria-hidden className="size-3.5" />} onClick={startEdit}>
            {f.is_missing ? "Enter from record" : "Correct"}
          </Button>
          {!f.is_missing && (
            <Button size="sm" variant="quiet" icon={<Trash2 aria-hidden className="size-3.5" />} onClick={() => { setRejecting(true); setReason(""); setErr(null); }}>
              Reject
            </Button>
          )}
        </div>
      )}

      {editing && (
        <div className="mt-3 space-y-3 rounded-md bg-sunken/60 p-3">
          <ValueFields category={f.category} draft={draft} setDraft={setDraft} />
          <Field label="Reason for correction (optional)">
            {({ id }) => <TextInput id={id} value={reason} onChange={(e) => setReason(e.target.value)} />}
          </Field>
          {err && <Banner tone="warning">{err}</Banner>}
          <div className="flex gap-2">
            <Button size="sm" loading={busy} onClick={save}>
              Save correction
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {rejecting && (
        <div className="mt-3 space-y-2 rounded-md bg-sunken/60 p-3">
          <Field label="Why is this item wrong?" required error={err}>
            {({ id, describedBy, invalid }) => (
              <TextInput id={id} aria-describedby={describedBy} invalid={invalid} value={reason} onChange={(e) => { setReason(e.target.value); setErr(null); }} />
            )}
          </Field>
          <div className="flex gap-2">
            <Button size="sm" variant="danger" loading={busy} onClick={reject}>
              Reject item
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setRejecting(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}

function str(v: unknown): string {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

function toValue(category: string, d: Record<string, string>): Record<string, unknown> | null {
  if (category === "medication") {
    if (!d.name?.trim()) return null;
    return { name: d.name.trim(), dose: d.dose?.trim() || null, frequency: d.frequency?.trim() || null, instructions: d.instructions?.trim() || null };
  }
  if (category === "follow_up") {
    if (!d.title?.trim()) return null;
    const days = d.within_days?.trim() ? Number(d.within_days) : null;
    return { title: d.title.trim(), provider: d.provider?.trim() || null, within_days: Number.isFinite(days) ? days : null, location: d.location?.trim() || null };
  }
  if (!d.text?.trim()) return null;
  return { text: d.text.trim() };
}

function ValueFields({ category, draft, setDraft }: { category: string; draft: Record<string, string>; setDraft: (d: Record<string, string>) => void }) {
  const set = (k: string, v: string) => setDraft({ ...draft, [k]: v });
  if (category === "medication")
    return (
      <div className="grid gap-3 sm:grid-cols-2">
        {(["name", "dose", "frequency", "instructions"] as const).map((k) => (
          <Field key={k} label={k[0].toUpperCase() + k.slice(1)} required={k === "name"}>
            {({ id }) => <TextInput id={id} value={draft[k] ?? ""} onChange={(e) => set(k, e.target.value)} />}
          </Field>
        ))}
        <p className="text-xs text-muted sm:col-span-2">Copy dose and frequency exactly from the prescription. Leave blank if not stated.</p>
      </div>
    );
  if (category === "follow_up")
    return (
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Follow-up" required>
          {({ id }) => <TextInput id={id} value={draft.title ?? ""} onChange={(e) => set("title", e.target.value)} />}
        </Field>
        <Field label="Provider">
          {({ id }) => <TextInput id={id} value={draft.provider ?? ""} onChange={(e) => set("provider", e.target.value)} />}
        </Field>
        <Field label="Within (days)">
          {({ id }) => <TextInput id={id} inputMode="numeric" value={draft.within_days ?? ""} onChange={(e) => set("within_days", e.target.value)} />}
        </Field>
        <Field label="Location">
          {({ id }) => <TextInput id={id} value={draft.location ?? ""} onChange={(e) => set("location", e.target.value)} />}
        </Field>
      </div>
    );
  return (
    <Field label="Value" required>
      {({ id }) => <TextArea id={id} rows={2} value={draft.text ?? ""} onChange={(e) => set("text", e.target.value)} />}
    </Field>
  );
}

function AddFactDialog({ ep, open, onClose }: { ep: EpisodeDetail; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [category, setCategory] = useState("medication");
  const [label, setLabel] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add an item from the record"
      description="Use this for information present in the actual record but missing from the pasted summary. It is marked as staff-entered and still needs doctor confirmation."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            loading={busy}
            onClick={async () => {
              const value = toValue(category, draft);
              if (!value) {
                setErr("Fill in the required field.");
                return;
              }
              setBusy(true);
              setErr(null);
              try {
                await factsApi.add(ep.id, { category, label: label.trim() || CATEGORY_LABEL[category] || category, value });
                toast({ tone: "success", text: "Item added." });
                await invalidateScopes(qc, ["episode", "memory", "plan", "activity"], ep.id);
                setDraft({});
                setLabel("");
                onClose();
              } catch (e) {
                setErr(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Add item
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Category" required>
          {({ id }) => (
            <Select
              id={id}
              value={category}
              onChange={(e) => {
                setCategory(e.target.value);
                setDraft({});
              }}
            >
              {EDITABLE_CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABEL[c] ?? c}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Label" hint="Short name, e.g. “Medicine” or “Allergy”.">
          {({ id, describedBy }) => <TextInput id={id} aria-describedby={describedBy} value={label} onChange={(e) => setLabel(e.target.value)} />}
        </Field>
        <ValueFields category={category} draft={draft} setDraft={setDraft} />
        {err && <Banner tone="warning">{err}</Banner>}
      </div>
    </Dialog>
  );
}
