import { cn } from "@/lib/utils";

export function Tabs({
  tabs,
  value,
  onChange,
}: {
  tabs: { key: string; label: string }[];
  value: string;
  onChange: (k: string) => void;
}) {
  return (
    <div role="tablist" className="flex max-w-full gap-1 overflow-x-auto border-b border-border">
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={value === t.key}
          tabIndex={value === t.key ? 0 : -1}
          onKeyDown={(e) => {
            const index = tabs.findIndex((tab) => tab.key === t.key);
            const next =
              e.key === "ArrowRight"
                ? (index + 1) % tabs.length
                : e.key === "ArrowLeft"
                  ? (index - 1 + tabs.length) % tabs.length
                  : e.key === "Home"
                    ? 0
                    : e.key === "End"
                      ? tabs.length - 1
                      : -1;
            if (next < 0) return;
            e.preventDefault();
            onChange(tabs[next].key);
            (e.currentTarget.parentElement?.children[next] as HTMLElement)?.focus();
          }}
          onClick={() => onChange(t.key)}
          className={cn(
            "shrink-0 whitespace-nowrap px-3 py-2 text-sm border-b-2 -mb-px transition-colors",
            value === t.key
              ? "border-primary text-foreground font-medium"
              : "border-transparent text-muted-foreground hover:text-foreground",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
