// Feedback & structure components (lead-owned): Dialog, Tabs, Banner, states, Toast.
import { AnimatePresence, motion } from "framer-motion";
import { AlertOctagon, AlertTriangle, CheckCircle2, Info, Loader2, X } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Button, cn } from "./primitives";

// ------------------------------------------------------------------ Dialog (native <dialog>: focus trap + Esc)
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  tone = "default",
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  tone?: "default" | "danger";
  size?: "md" | "lg";
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={description ? descId : undefined}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      className={cn(
        "m-auto w-[calc(100%-2rem)] rounded-lg border border-line bg-surface p-0 text-ink shadow-[var(--shadow-pop)]",
        size === "lg" ? "max-w-3xl" : "max-w-lg",
      )}
    >
      {open && (
        <div className="flex max-h-[85vh] flex-col">
          <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
            <div>
              <h2 id={titleId} className={cn("text-lg font-bold", tone === "danger" && "text-urgent-ink")}>
                {title}
              </h2>
              {description && (
                <p id={descId} className="mt-1 text-sm text-muted">
                  {description}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close dialog"
              className="inline-flex size-9 shrink-0 items-center justify-center rounded-md text-muted hover:bg-sunken hover:text-ink cursor-pointer"
            >
              <X aria-hidden className="size-5" />
            </button>
          </div>
          <div className="overflow-y-auto px-5 py-4">{children}</div>
          {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
        </div>
      )}
    </dialog>
  );
}

// ------------------------------------------------------------------ Tabs (WAI-ARIA, roving focus)
export interface TabDef {
  id: string;
  label: ReactNode;
  badge?: ReactNode;
}

export function Tabs({
  tabs,
  value,
  onChange,
  label,
  className,
}: {
  tabs: TabDef[];
  value: string;
  onChange: (id: string) => void;
  label: string;
  className?: string;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = tabs.findIndex((t) => t.id === value);
    let next = -1;
    if (e.key === "ArrowRight") next = (i + 1) % tabs.length;
    if (e.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
    if (e.key === "Home") next = 0;
    if (e.key === "End") next = tabs.length - 1;
    if (next >= 0) {
      e.preventDefault();
      onChange(tabs[next].id);
      refs.current[tabs[next].id]?.focus();
    }
  };
  return (
    <div role="tablist" aria-label={label} onKeyDown={onKey} className={cn("flex gap-1 overflow-x-auto border-b border-line", className)}>
      {tabs.map((t) => {
        const selected = t.id === value;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[t.id] = el;
            }}
            role="tab"
            type="button"
            id={`tab-${t.id}`}
            aria-selected={selected}
            aria-controls={`panel-${t.id}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(t.id)}
            className={cn(
              "relative -mb-px inline-flex min-h-10 shrink-0 items-center gap-2 border-b-2 px-3 text-[15px] font-bold cursor-pointer",
              selected ? "border-primary text-primary-ink" : "border-transparent text-muted hover:text-ink",
            )}
          >
            {t.label}
            {t.badge}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({ id, active, children }: { id: string; active: boolean; children: ReactNode }) {
  if (!active) return null;
  return (
    <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`} tabIndex={0} className="pt-4 focus-visible:outline-offset-4">
      {children}
    </div>
  );
}

// ------------------------------------------------------------------ Banner
const bannerCls = {
  info: "bg-primary-soft border-primary/25 text-primary-ink",
  warning: "bg-attention border-attention-line text-attention-ink",
  urgent: "bg-urgent-soft border-urgent/40 text-urgent-ink",
  success: "bg-surface border-primary/40 text-primary-ink",
};
const bannerIcon = { info: Info, warning: AlertTriangle, urgent: AlertOctagon, success: CheckCircle2 };

export function Banner({
  tone = "info",
  title,
  children,
  actions,
  className,
  live,
}: {
  tone?: keyof typeof bannerCls;
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
  /** announce to screen readers when it appears */
  live?: boolean;
}) {
  const Icon = bannerIcon[tone];
  return (
    <div
      role={live ? (tone === "urgent" ? "alert" : "status") : undefined}
      className={cn("flex flex-wrap items-start gap-3 rounded-lg border px-4 py-3", bannerCls[tone], className)}
    >
      <Icon aria-hidden className="mt-0.5 size-5 shrink-0" />
      <div className="min-w-0 flex-1">
        {title && <p className="font-bold">{title}</p>}
        {children && <div className={cn("text-[15px]", title && "mt-0.5")}>{children}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ States
export function Spinner({ label = "Loading…", className }: { label?: string; className?: string }) {
  return (
    <div role="status" className={cn("flex items-center gap-2 text-muted", className)}>
      <Loader2 aria-hidden className="size-4 animate-spin" />
      <span>{label}</span>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-md bg-sunken", className)} />;
}

export function EmptyState({
  title,
  children,
  action,
  icon,
  className,
}: {
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-start gap-2 rounded-lg border border-dashed border-line-strong px-5 py-6", className)}>
      {icon && <div className="text-muted">{icon}</div>}
      <p className="font-bold text-ink">{title}</p>
      {children && <div className="max-w-[60ch] text-[15px] text-muted">{children}</div>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

export function ErrorState({
  title = "This couldn't be loaded",
  error,
  onRetry,
  className,
}: {
  title?: ReactNode;
  error?: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  const msg = error instanceof Error ? error.message : typeof error === "string" ? error : null;
  return (
    <Banner tone="warning" title={title} className={className} actions={onRetry && <Button variant="secondary" size="sm" onClick={onRetry}>Try again</Button>}>
      {msg ?? "Please try again. If it keeps happening, refresh the page."}
    </Banner>
  );
}

// ------------------------------------------------------------------ Toast (polite live region)
interface ToastItem {
  id: number;
  tone: "info" | "success" | "warning" | "urgent";
  text: ReactNode;
}
const ToastCtx = createContext<(t: Omit<ToastItem, "id">) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((t: Omit<ToastItem, "id">) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs.slice(-3), { ...t, id }]);
    window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), t.tone === "urgent" ? 10000 : 5000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div aria-live="polite" aria-atomic="false" className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4 sm:items-end sm:pr-6">
        <AnimatePresence initial={false}>
          {items.map((t) => (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, transition: { duration: 0.15, ease: "easeIn" } }}
              transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}
              className="pointer-events-auto w-full max-w-sm"
            >
              <Banner tone={t.tone === "success" ? "success" : t.tone} className="shadow-[var(--shadow-pop)]">
                {t.text}
              </Banner>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  return useContext(ToastCtx);
}
