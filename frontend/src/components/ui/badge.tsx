import { type HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type Variant = "default" | "primary" | "success" | "warning" | "danger" | "info" | "outline" | "code";

const styles: Record<Variant, string> = {
  default: "bg-muted text-muted-foreground",
  primary: "bg-primary/15 text-primary",
  success: "bg-success/15 text-success",
  warning: "bg-warning/15 text-warning",
  danger: "bg-danger/15 text-danger",
  info: "bg-info/15 text-info",
  outline: "border border-border text-muted-foreground",
  code: "border border-border text-muted-foreground font-code",
};

export function Badge({
  variant = "default",
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { variant?: Variant }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium leading-tight",
        styles[variant],
        className,
      )}
      {...props}
    />
  );
}
