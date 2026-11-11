// Core UI primitives (lead-owned). Hospital and patient views share these.
// Patient screens pass size="lg" (>= 48px targets); hospital screens use the default "md" (40px)
// or "sm" in dense tables. Every interactive element keeps the global :focus-visible ring.
import { AlertTriangle, Loader2 } from "lucide-react";
import {
  forwardRef,
  useId,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

export function cn(...parts: unknown[]): string {
  return parts.filter((p): p is string => typeof p === "string" && p.length > 0).join(" ");
}

// ------------------------------------------------------------------ Button
type Variant = "primary" | "secondary" | "ghost" | "danger" | "quiet";
type Size = "sm" | "md" | "lg";

const variantCls: Record<Variant, string> = {
  primary: "bg-primary text-white hover:bg-primary-hover disabled:bg-line-strong",
  secondary: "bg-surface text-ink border border-line-strong hover:bg-sunken disabled:text-faint",
  ghost: "bg-transparent text-primary-ink hover:bg-primary-soft disabled:text-faint",
  danger: "bg-urgent text-white hover:bg-urgent-hover disabled:bg-line-strong",
  quiet: "bg-transparent text-muted hover:text-ink hover:bg-sunken disabled:text-faint",
};
const sizeCls: Record<Size, string> = {
  sm: "min-h-8 px-3 text-sm gap-1.5",
  md: "min-h-10 px-4 text-[15px] gap-2",
  lg: "min-h-12 px-5 text-base gap-2.5 w-full sm:w-auto",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", loading = false, icon, className, children, disabled, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex items-center justify-center rounded-md font-bold select-none",
        "transition-[background-color,color,transform] duration-150 ease-out active:scale-[0.98]",
        "disabled:cursor-not-allowed disabled:active:scale-100 cursor-pointer",
        variantCls[variant],
        sizeCls[size],
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 aria-hidden className="size-4 animate-spin" /> : icon}
      {children}
    </button>
  );
});

export function IconButton({
  label,
  children,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cn(
        "inline-flex size-10 items-center justify-center rounded-md text-muted hover:bg-sunken hover:text-ink cursor-pointer",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

// ------------------------------------------------------------------ Card
export function Card({
  children,
  className,
  as: As = "section",
  ...rest
}: { children: ReactNode; className?: string; as?: "section" | "div" | "article" | "li" } & Record<string, unknown>) {
  return (
    <As className={cn("rounded-lg border border-line bg-surface", className)} {...rest}>
      {children}
    </As>
  );
}

export function CardHeader({
  title,
  description,
  actions,
  level = 2,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  level?: 2 | 3;
  className?: string;
}) {
  const H = level === 2 ? "h2" : "h3";
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3", className)}>
      <div className="min-w-0">
        <H className="text-base font-bold text-ink">{title}</H>
        {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ Form fields
export function Field({
  label,
  hint,
  error,
  required,
  children,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  required?: boolean;
  className?: string;
  children: (ids: { id: string; describedBy: string | undefined; invalid: boolean }) => ReactNode;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errId = error ? `${id}-err` : undefined;
  const describedBy = [hintId, errId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-sm font-bold text-ink">
        {label}
        {required && (
          <span className="ml-1 font-normal text-muted">
            (required)
          </span>
        )}
      </label>
      {hint && (
        <p id={hintId} className="text-sm text-muted">
          {hint}
        </p>
      )}
      {children({ id, describedBy, invalid: !!error })}
      {error && (
        <p id={errId} className="flex items-start gap-1.5 text-sm font-bold text-urgent-ink">
          <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0" />
          {error}
        </p>
      )}
    </div>
  );
}

const inputBase =
  "w-full rounded-md border bg-surface px-3 text-ink placeholder:text-faint " +
  "border-line-strong hover:border-muted aria-[invalid=true]:border-urgent aria-[invalid=true]:bg-urgent-soft/30 " +
  "disabled:bg-sunken disabled:text-muted";

export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }>(
  function TextInput({ className, invalid, ...rest }, ref) {
    return <input ref={ref} aria-invalid={invalid || undefined} className={cn(inputBase, "min-h-10 py-2", className)} {...rest} />;
  },
);

export const TextArea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement> & { invalid?: boolean }>(
  function TextArea({ className, invalid, ...rest }, ref) {
    return <textarea ref={ref} aria-invalid={invalid || undefined} className={cn(inputBase, "py-2 leading-relaxed", className)} {...rest} />;
  },
);

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement> & { invalid?: boolean }>(
  function Select({ className, invalid, children, ...rest }, ref) {
    return (
      <select ref={ref} aria-invalid={invalid || undefined} className={cn(inputBase, "min-h-10 py-2 pr-8 cursor-pointer", className)} {...rest}>
        {children}
      </select>
    );
  },
);

export function Checkbox({
  label,
  description,
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode; description?: ReactNode }) {
  const id = useId();
  return (
    <div className={cn("flex items-start gap-3", className)}>
      <input id={id} type="checkbox" className="mt-1 size-5 shrink-0 accent-primary cursor-pointer" {...rest} />
      <label htmlFor={id} className="cursor-pointer">
        <span className="font-bold text-ink">{label}</span>
        {description && <span className="block text-sm text-muted">{description}</span>}
      </label>
    </div>
  );
}

// ------------------------------------------------------------------ Layout helpers
export function PageHeader({
  title,
  description,
  actions,
  meta,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4 pb-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-bold text-ink">{title}</h1>
        {description && <p className="mt-1 max-w-[70ch] text-muted">{description}</p>}
        {meta && <div className="mt-2 flex flex-wrap items-center gap-2">{meta}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function KeyValue({ items, className }: { items: { k: ReactNode; v: ReactNode }[]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(8rem,max-content)_1fr] gap-x-4 gap-y-2 text-sm", className)}>
      {items.map((it, i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{it.k}</dt>
          <dd className="min-w-0 text-ink break-words">{it.v}</dd>
        </div>
      ))}
    </dl>
  );
}
