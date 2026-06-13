import { type ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Panel —— v2 影院级控制台玻璃面板
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
        "glass flex min-h-0 flex-col overflow-hidden rounded-xl border border-border shadow-panel backdrop-blur-md",
        className,
      )}
    >
      <div className="flex h-10 flex-shrink-0 items-center gap-2 border-b border-border px-3.5">
        {icon && <span className="text-faint">{icon}</span>}
        <span className="text-[12.5px] font-semibold">{title}</span>
        <span className="flex-1" />
        {meta && <span className="font-code text-[11px] text-faint">{meta}</span>}
        {action}
      </div>
      <div className={cn("min-h-0 flex-1 overflow-auto p-3.5", bodyClassName)}>{children}</div>
    </section>
  );
}
