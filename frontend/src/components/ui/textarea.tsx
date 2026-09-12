import { forwardRef, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/**
 * Textarea —— 多行长文本输入(简介 / 提示词 / 片段内容等)
 * 默认可纵向拉伸,保留换行;与 Input 同一视觉语言。
 */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, rows = 4, ...props }, ref) => (
    <textarea
      ref={ref}
      rows={rows}
      className={cn(
        "flex w-full resize-y rounded-md border border-border bg-surface px-3 py-2 text-sm leading-6 transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50 disabled:cursor-not-allowed",
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";
