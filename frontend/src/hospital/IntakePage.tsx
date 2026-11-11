// New discharge intake (Agent C). The nurse records contact details, the ACTUAL discharge time,
// the care team, risk inputs and the pasted summary. Extraction runs on the server; the next
// screen shows the original summary beside the editable extracted facts.
import { useMutation } from "@tanstack/react-query";
import { FileText, Info } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api, errorMessage, isApiError } from "../lib/api";
import { useMe } from "../lib/auth";
import { useSimClock } from "../lib/clock";
import { fromLocalInputValue, toLocalInputValue } from "../lib/format";
import type { EpisodeDetail, SampleOut } from "../lib/types";
import { Badge, Banner, Button, Card, CardHeader, Checkbox, Field, PageHeader, Select, Spinner, TextArea, TextInput, useToast } from "../ui";
import { useSamples, useStaff } from "./hooks";

const LANGUAGES = ["English", "Hindi", "Kannada", "Tamil", "Telugu", "Malayalam", "Marathi", "Bengali", "Other"];

interface FormState {
  full_name: string;
  age: string;
  sex: string;
  phone_e164: string;
  preferred_language: string;
  hospital_number: string;
  caregiver_name: string;
  caregiver_relation: string;
  caregiver_phone_e164: string;
  caregiver_authorised: boolean;
  discharge_local: string;
  doctor_id: string;
  nurse_id: string;
  lives_alone: string;
  prior_admission_6m: string;
  caregiver_available: string;
  summary_text: string;
  sample_key: string | null;
}

const EMPTY: FormState = {
  full_name: "",
  age: "",
  sex: "",
  phone_e164: "",
  preferred_language: "English",
  hospital_number: "",
  caregiver_name: "",
  caregiver_relation: "",
  caregiver_phone_e164: "",
  caregiver_authorised: false,
  discharge_local: "",
  doctor_id: "",
  nurse_id: "",
  lives_alone: "",
  prior_admission_6m: "",
  caregiver_available: "",
  summary_text: "",
  sample_key: null,
};

const triToBool = (v: string): boolean | undefined => (v === "yes" ? true : v === "no" ? false : undefined);
const boolToTri = (v: unknown): string => (v === true ? "yes" : v === false ? "no" : "");

export default function IntakePage() {
  const { data: me } = useMe();
  const navigate = useNavigate();
  const toast = useToast();
  const [params] = useSearchParams();
  const samples = useSamples();
  const doctors = useStaff("doctor");
  const nurses = useStaff("nurse");
  const { data: clock } = useSimClock();
  const [f, setF] = useState<FormState>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const autoLoaded = useRef(false);

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => {
    setF((s) => ({ ...s, [k]: v }));
    setErrors((e) => {
      const n = { ...e };
      delete n[k as string];
      return n;
    });
  };

  // default nurse = me; default discharge = now (simulated)
  useEffect(() => {
    setF((s) => ({
      ...s,
      nurse_id: s.nurse_id || (me?.user.role === "nurse" ? String(me.user.id) : ""),
      discharge_local: s.discharge_local || (clock ? toLocalInputValue(clock.now) : ""),
    }));
  }, [me, clock]);

  const sortedSamples = useMemo(() => [...(samples.data ?? [])].sort((a, b) => Number(b.primary) - Number(a.primary)), [samples.data]);

  function loadSample(s: SampleOut) {
    const now = clock ? new Date(clock.now) : new Date();
    const discharged = new Date(now.getTime() - (s.discharge_hours_ago ?? 0) * 3_600_000);
    const doc = doctors.data?.find((d) => d.username === s.suggested_doctor_username);
    const p = s.patient;
    setF((cur) => ({
      ...cur,
      full_name: p.full_name ?? "",
      age: p.age != null ? String(p.age) : "",
      sex: p.sex ?? "",
      phone_e164: p.phone_e164 ?? "",
      preferred_language: p.preferred_language ?? "English",
      hospital_number: p.hospital_number ?? "",
      caregiver_name: p.caregiver_name ?? "",
      caregiver_relation: p.caregiver_relation ?? "",
      caregiver_phone_e164: p.caregiver_phone_e164 ?? "",
      caregiver_authorised: !!p.caregiver_authorised,
      discharge_local: toLocalInputValue(discharged),
      doctor_id: doc ? String(doc.id) : cur.doctor_id,
      lives_alone: boolToTri(s.risk_inputs.lives_alone),
      prior_admission_6m: boolToTri(s.risk_inputs.prior_admission_6m),
      caregiver_available: boolToTri(s.risk_inputs.caregiver_available),
      summary_text: s.summary_text,
      sample_key: s.key,
    }));
    setErrors({});
    toast({ tone: "info", text: `Loaded fictional sample: ${s.label}` });
  }

  // ?sample=key (from the demo drawer)
  useEffect(() => {
    const key = params.get("sample");
    if (!key || autoLoaded.current || !samples.data || !doctors.data || !clock) return;
    const s = samples.data.find((x) => x.key === key);
    if (s) {
      autoLoaded.current = true;
      loadSample(s);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, samples.data, doctors.data, clock]);

  const create = useMutation({
    mutationFn: (body: unknown) => api.post<EpisodeDetail>("/api/episodes", body),
    onSuccess: (ep) => {
      toast({ tone: "success", text: "Draft created. Review the extracted information." });
      navigate(`/hospital/patients/${ep.id}`);
    },
    onError: (e) => {
      if (isApiError(e) && e.status === 422) {
        const fe = e.fieldErrors;
        const mapped: Record<string, string> = {};
        for (const [k, msg] of Object.entries(fe)) {
          const key = k.replace(/^body\./, "").replace(/^patient\./, "").replace(/^risk_inputs\./, "");
          mapped[key === "discharge_at" ? "discharge_local" : key] = msg;
        }
        setErrors(mapped);
      }
    },
  });

  function validate(): Record<string, string> {
    const e: Record<string, string> = {};
    if (!f.full_name.trim()) e.full_name = "Enter the patient's name.";
    if (f.age && (!/^\d{1,3}$/.test(f.age) || Number(f.age) > 120)) e.age = "Enter an age between 0 and 120.";
    if (!/^\+\d{8,15}$/.test(f.phone_e164.replace(/\s/g, ""))) e.phone_e164 = "Use international format, e.g. +919876543210.";
    if (f.caregiver_phone_e164 && !/^\+\d{8,15}$/.test(f.caregiver_phone_e164.replace(/\s/g, "")))
      e.caregiver_phone_e164 = "Use international format, e.g. +919876543210.";
    if (f.caregiver_authorised && !f.caregiver_name.trim()) e.caregiver_name = "Name the authorised contact.";
    if (!fromLocalInputValue(f.discharge_local)) e.discharge_local = "Enter the actual discharge date and time.";
    if (!f.doctor_id) e.doctor_id = "Assign the responsible doctor.";
    if (f.summary_text.trim().length < 20) e.summary_text = "Paste the discharge summary (at least a few lines).";
    return e;
  }

  function submit(ev: FormEvent) {
    ev.preventDefault();
    const e = validate();
    setErrors(e);
    if (Object.keys(e).length) {
      const first = Object.keys(e)[0];
      document.querySelector<HTMLElement>(`[data-field="${first}"] input, [data-field="${first}"] select, [data-field="${first}"] textarea`)?.focus();
      return;
    }
    const opt = (v: string) => (v.trim() ? v.trim() : null);
    create.mutate({
      patient: {
        full_name: f.full_name.trim(),
        age: f.age ? Number(f.age) : null,
        sex: opt(f.sex),
        phone_e164: f.phone_e164.replace(/\s/g, ""),
        preferred_language: f.preferred_language,
        caregiver_name: opt(f.caregiver_name),
        caregiver_relation: opt(f.caregiver_relation),
        caregiver_phone_e164: opt(f.caregiver_phone_e164.replace(/\s/g, "")),
        caregiver_authorised: f.caregiver_authorised,
        hospital_number: opt(f.hospital_number),
      },
      discharge_at: fromLocalInputValue(f.discharge_local),
      doctor_id: Number(f.doctor_id),
      nurse_id: f.nurse_id ? Number(f.nurse_id) : null,
      risk_inputs: {
        lives_alone: triToBool(f.lives_alone),
        prior_admission_6m: triToBool(f.prior_admission_6m),
        caregiver_available: triToBool(f.caregiver_available),
      },
      summary_text: f.summary_text,
      sample_key: f.sample_key,
    });
  }

  if (me && me.user.role !== "nurse") {
    return (
      <div>
        <PageHeader title="New discharge" />
        <Banner tone="info">Discharge intake is prepared by nurses. Doctors review and activate the plan afterwards.</Banner>
      </div>
    );
  }

  const fieldErr = (k: string) => errors[k] ?? null;
  const generalErr = create.error && !(isApiError(create.error) && create.error.status === 422) ? errorMessage(create.error) : null;

  return (
    <div>
      <PageHeader
        title="New discharge"
        description="Record what the discharging team decided. Ninety-Six extracts the details for review; it never invents missing doses or instructions."
      />

      <Card className="mb-5">
        <CardHeader title="Fictional sample summaries" description="For the demo only. Loading a sample fills the form; you can still edit everything." level={2} />
        <div className="flex flex-wrap gap-2 px-4 py-3">
          {samples.isLoading && <Spinner label="Loading samples…" />}
          {samples.isError && <span className="text-sm text-muted">Samples unavailable: {errorMessage(samples.error)}</span>}
          {sortedSamples.map((s) => (
            <Button key={s.key} variant={f.sample_key === s.key ? "primary" : "secondary"} icon={<FileText aria-hidden className="size-4" />} onClick={() => loadSample(s)}>
              {s.label}
              {s.primary && (
                <Badge tone="primary" className="ml-1">
                  Primary demo
                </Badge>
              )}
            </Button>
          ))}
        </div>
      </Card>

      <form onSubmit={submit} noValidate className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-5">
          <Card>
            <CardHeader title="Patient and contact" />
            <div className="grid gap-4 px-4 py-4 sm:grid-cols-2">
              <F k="full_name" label="Full name" required err={fieldErr("full_name")} className="sm:col-span-2">
                {(p) => <TextInput {...p} autoComplete="off" value={f.full_name} onChange={(e) => set("full_name", e.target.value)} />}
              </F>
              <F k="age" label="Age" err={fieldErr("age")}>
                {(p) => <TextInput {...p} inputMode="numeric" value={f.age} onChange={(e) => set("age", e.target.value)} />}
              </F>
              <F k="sex" label="Sex" err={fieldErr("sex")}>
                {(p) => (
                  <Select {...p} value={f.sex} onChange={(e) => set("sex", e.target.value)}>
                    <option value="">Not stated</option>
                    <option value="F">Female</option>
                    <option value="M">Male</option>
                    <option value="Other">Other</option>
                  </Select>
                )}
              </F>
              <F k="phone_e164" label="Patient phone (sign-in and messages)" required err={fieldErr("phone_e164")} hint="International format. Used for the invitation and sign-in code.">
                {(p) => <TextInput {...p} type="tel" autoComplete="off" value={f.phone_e164} onChange={(e) => set("phone_e164", e.target.value)} placeholder="+91…" />}
              </F>
              <F k="preferred_language" label="Preferred language" err={fieldErr("preferred_language")} hint="Recorded for the team. This prototype shows reviewed instructions in English only.">
                {(p) => (
                  <Select {...p} value={f.preferred_language} onChange={(e) => set("preferred_language", e.target.value)}>
                    {LANGUAGES.map((l) => (
                      <option key={l}>{l}</option>
                    ))}
                  </Select>
                )}
              </F>
              <F k="hospital_number" label="Hospital number (fictional)" err={fieldErr("hospital_number")}>
                {(p) => <TextInput {...p} value={f.hospital_number} onChange={(e) => set("hospital_number", e.target.value)} />}
              </F>
            </div>
          </Card>

          <Card>
            <CardHeader title="Caregiver / authorised contact" description="Optional. Nothing is shared with a caregiver unless the patient has authorised it, and every caregiver message still needs doctor approval." />
            <div className="grid gap-4 px-4 py-4 sm:grid-cols-2">
              <F k="caregiver_name" label="Name" err={fieldErr("caregiver_name")}>
                {(p) => <TextInput {...p} value={f.caregiver_name} onChange={(e) => set("caregiver_name", e.target.value)} />}
              </F>
              <F k="caregiver_relation" label="Relationship" err={fieldErr("caregiver_relation")}>
                {(p) => <TextInput {...p} value={f.caregiver_relation} onChange={(e) => set("caregiver_relation", e.target.value)} />}
              </F>
              <F k="caregiver_phone_e164" label="Phone" err={fieldErr("caregiver_phone_e164")}>
                {(p) => <TextInput {...p} type="tel" value={f.caregiver_phone_e164} onChange={(e) => set("caregiver_phone_e164", e.target.value)} placeholder="+91…" />}
              </F>
              <div className="sm:col-span-2">
                <Checkbox
                  label="Patient has authorised the hospital to contact this person"
                  description="Record only what the patient actually agreed to."
                  checked={f.caregiver_authorised}
                  onChange={(e) => set("caregiver_authorised", e.target.checked)}
                />
              </div>
            </div>
          </Card>

          <Card>
            <CardHeader title="Discharge and care team" />
            <div className="grid gap-4 px-4 py-4 sm:grid-cols-2">
              <F k="discharge_local" label="Actual discharge time (IST)" required err={fieldErr("discharge_local")} hint="The 96-hour window starts here. Past times are fine; missed reminders are skipped, not sent in a burst." className="sm:col-span-2">
                {(p) => <TextInput {...p} type="datetime-local" value={f.discharge_local} onChange={(e) => set("discharge_local", e.target.value)} />}
              </F>
              <F k="doctor_id" label="Responsible doctor" required err={fieldErr("doctor_id")}>
                {(p) => (
                  <Select {...p} value={f.doctor_id} onChange={(e) => set("doctor_id", e.target.value)}>
                    <option value="">Choose…</option>
                    {(doctors.data ?? []).map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.display_name}
                        {d.specialty ? ` (${d.specialty})` : ""}
                      </option>
                    ))}
                  </Select>
                )}
              </F>
              <F k="nurse_id" label="Coordinating nurse" err={fieldErr("nurse_id")}>
                {(p) => (
                  <Select {...p} value={f.nurse_id} onChange={(e) => set("nurse_id", e.target.value)}>
                    <option value="">Me</option>
                    {(nurses.data ?? []).map((n) => (
                      <option key={n.id} value={n.id}>
                        {n.display_name}
                      </option>
                    ))}
                  </Select>
                )}
              </F>
            </div>
          </Card>

          <Card>
            <CardHeader
              title="Risk inputs"
              description="Used for a transparent demo prioritisation (shown with its reasons). It is not a validated clinical prediction."
            />
            <div className="grid gap-4 px-4 py-4 sm:grid-cols-3">
              {(
                [
                  ["lives_alone", "Lives alone"],
                  ["prior_admission_6m", "Admitted in the last 6 months"],
                  ["caregiver_available", "Caregiver available at home"],
                ] as const
              ).map(([k, label]) => (
                <F key={k} k={k} label={label} err={fieldErr(k)}>
                  {(p) => (
                    <Select {...p} value={f[k]} onChange={(e) => set(k, e.target.value)}>
                      <option value="">Not provided</option>
                      <option value="yes">Yes</option>
                      <option value="no">No</option>
                    </Select>
                  )}
                </F>
              ))}
            </div>
          </Card>
        </div>

        <div className="space-y-5">
          <Card className="xl:sticky xl:top-20">
            <CardHeader title="Discharge summary" description="Paste the summary written by the discharging team. PDF upload is not supported in this prototype." />
            <div className="px-4 py-4">
              <F k="summary_text" label="Summary text" required err={fieldErr("summary_text")}>
                {(p) => (
                  <TextArea
                    {...p}
                    rows={22}
                    spellCheck={false}
                    className="font-mono text-[13px]"
                    value={f.summary_text}
                    onChange={(e) => set("summary_text", e.target.value)}
                  />
                )}
              </F>
              <p className="mt-3 flex items-start gap-1.5 text-sm text-muted">
                <Info aria-hidden className="mt-0.5 size-4 shrink-0" />
                After you create the draft, each extracted item shows the exact line it came from. Anything absent is marked “not provided”.
              </p>
              {generalErr && (
                <Banner tone="warning" className="mt-3" live>
                  {generalErr}
                </Banner>
              )}
              {Object.keys(errors).length > 0 && (
                <Banner tone="warning" className="mt-3" live>
                  {Object.keys(errors).length} field{Object.keys(errors).length === 1 ? " needs" : "s need"} attention.
                </Banner>
              )}
              <div className="mt-4 flex flex-wrap gap-2">
                <Button type="submit" loading={create.isPending}>
                  Create draft and extract
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setF({ ...EMPTY, nurse_id: me ? String(me.user.id) : "", discharge_local: clock ? toLocalInputValue(clock.now) : "" });
                    setErrors({});
                  }}
                >
                  Clear form
                </Button>
              </div>
            </div>
          </Card>
        </div>
      </form>
    </div>
  );
}

type FieldProps = { id: string; "aria-describedby"?: string; invalid: boolean };

function F({
  k,
  label,
  required,
  err,
  hint,
  className,
  children,
}: {
  k: string;
  label: string;
  required?: boolean;
  err: string | null;
  hint?: string;
  className?: string;
  children: (p: FieldProps) => React.ReactNode;
}) {
  return (
    <div data-field={k} className={className}>
      <Field label={label} required={required} error={err} hint={hint}>
        {({ id, describedBy, invalid }) => children({ id, "aria-describedby": describedBy, invalid })}
      </Field>
    </div>
  );
}
