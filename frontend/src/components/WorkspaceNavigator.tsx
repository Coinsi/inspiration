import { useState } from "react";
import { ArrowUpRight, Search, X } from "lucide-react";
import { Link } from "react-router-dom";
import { useDialogFocus } from "@/components/ui/useDialogFocus";
import { WORKSPACE_DESTINATIONS } from "@/lib/workspace-navigation";
import { useI18n } from "@/lib/i18n";

export function WorkspaceNavigator({
  projectId,
  onClose,
}: {
  projectId: string;
  onClose: () => void;
}) {
  const { t, lang } = useI18n();
  const zh = lang === "zh";
  const [search, setSearch] = useState("");
  const ref = useDialogFocus(true, onClose);
  const label = (item: (typeof WORKSPACE_DESTINATIONS)[number]) =>
    item.to === "assets/trash" ? (zh ? "回收站" : "Trash") : t(item.label);
  const results = WORKSPACE_DESTINATIONS.filter((item) =>
    `${label(item)} ${item.keywords}`
      .toLocaleLowerCase()
      .includes(search.trim().toLocaleLowerCase()),
  );
  return (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center bg-black/50 px-4 pt-[12vh] backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby="navigator-title"
        className="w-full max-w-xl overflow-hidden rounded-2xl border bg-card shadow-2xl"
      >
        <div className="flex items-center gap-3 border-b p-4">
          <Search className="h-5 w-5 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <h2
              id="navigator-title"
              className="mb-1 text-xs text-muted-foreground"
            >
              {zh
                ? "快速导航 · 当前项目"
                : "Quick navigation · current project"}
            </h2>
            <input
              autoFocus
              aria-label={zh ? "查找功能" : "Find a tool"}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={
                zh
                  ? "前往素材、分镜、剪辑、模型设置…"
                  : "Find assets, storyboard, editing, settings…"
              }
              className="w-full bg-transparent text-sm outline-none"
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  ref.current
                    ?.querySelector<HTMLAnchorElement>("[data-destination]")
                    ?.focus();
                }
              }}
            />
          </div>
          <button
            onClick={onClose}
            aria-label={zh ? "关闭快速导航" : "Close quick navigation"}
            className="rounded-lg p-2 text-muted-foreground hover:bg-elevated"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div
          className="max-h-[52vh] overflow-y-auto p-2"
          onKeyDown={(e) => {
            if (!["ArrowDown", "ArrowUp"].includes(e.key)) return;
            const links = Array.from(
              ref.current?.querySelectorAll<HTMLAnchorElement>(
                "[data-destination]",
              ) ?? [],
            );
            const index = links.indexOf(
              document.activeElement as HTMLAnchorElement,
            );
            if (index < 0 || !links.length) return;
            e.preventDefault();
            links[
              (index + (e.key === "ArrowDown" ? 1 : -1) + links.length) %
                links.length
            ]?.focus();
          }}
        >
          {results.map((item) => (
            <Link
              key={item.to}
              data-destination
              to={`/projects/${projectId}/${item.to}`}
              onClick={onClose}
              className="flex items-center gap-3 rounded-lg px-3 py-3 text-sm hover:bg-elevated focus-visible:bg-elevated"
            >
              <item.icon className="h-4 w-4 text-muted-foreground" />
              <span className="flex-1">{label(item)}</span>
              <ArrowUpRight className="h-3.5 w-3.5 text-faint" />
            </Link>
          ))}
          {!results.length && (
            <p
              role="status"
              className="p-8 text-center text-sm text-muted-foreground"
            >
              {zh
                ? "没有匹配的功能，换个关键词试试。"
                : "No matching tools. Try another keyword."}
            </p>
          )}
        </div>
        <p className="border-t px-4 py-3 text-xs text-faint">
          {zh
            ? "↑ ↓ 选择 · Enter 打开 · Esc 关闭。这里查找功能入口。"
            : "↑ ↓ select · Enter open · Esc close. Search workspace tools here."}
        </p>
      </div>
    </div>
  );
}
