import { type ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Panel — shared studio surface
 * head(标题 + 右侧动作/计数)+ body(可滚动内容)
 */
export function Panel({
  title,
  icon,
  action,
  meta,
  className,
  bodyClassName,
  children,
}: {
  title: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  meta?: ReactNode;
  className?: string;
  bodyClassName?: string;
  children: ReactNode;
}) {
  return (
    <section
      className={cn(
        "bg-card flex min-h-0 flex-col overflow-hidden rounded-lg border border-border ",
        className,
      )}
    >
      <div className="flex min-h-12 flex-shrink-0 items-center gap-2 border-b border-border px-4">
        {icon && <span className="text-faint">{icon}</span>}
        <span className="text-sm font-semibold">{title}</span>
        <span className="flex-1" />
        {meta && <span className="font-code text-xs text-faint">{meta}</span>}
        {action}
      </div>
      <div className={cn("min-h-0 flex-1 overflow-auto p-4", bodyClassName)}>{children}</div>
    </section>
  );
}
