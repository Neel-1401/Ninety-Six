// A react-router Link styled like the shared Button (avoids nesting <button> inside <a>).
import type { ReactNode } from "react";
import { Link } from "react-router";
import { cn } from "../ui";

export function LinkButton({
  to,
  children,
  icon,
  variant = "primary",
  className,
}: {
  to: string;
  children: ReactNode;
  icon?: ReactNode;
  variant?: "primary" | "secondary";
  className?: string;
}) {
  return (
    <Link
      to={to}
      className={cn(
        "inline-flex min-h-10 items-center justify-center gap-2 rounded-md px-4 text-[15px] font-bold transition-colors duration-150",
        variant === "primary" ? "bg-primary text-white hover:bg-primary-hover" : "border border-line-strong bg-surface text-ink hover:bg-sunken",
        className,
      )}
    >
      {icon}
      {children}
    </Link>
  );
}
