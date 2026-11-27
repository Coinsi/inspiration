import { SearchField } from "@/components/ui/search-field";
import { useState, type ReactNode } from "react";
import { Plus, Check } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";

export function ObjectPicker({
  items,
  selected,
  onSelect,
  disabled,
  loading,
  error,
  onRetry,
  renderPreview,
  title = "选择创作对象",
  closeOnSelect = false,
}: {
  items: { value: string; label: string; description: string; keywords?: string }[];
  selected: string[];
  onSelect: (id: string) => void;
  disabled?: boolean;
  loading?: boolean;
  error?: boolean;
  onRetry?: () => void;
  renderPreview?: (id: string) => ReactNode;
  title?: string;
  closeOnSelect?: boolean;
}) {
  const [open, setOpen] = useState(false),
    [query, setQuery] = useState("");
  const [limit, setLimit] = useState(40);
  const matches = items.filter((i) =>
    [i.label, i.description, i.keywords].join(" ").toLowerCase().includes(query.trim().toLowerCase()),
  );
  return (
    <>
      <Button
        variant="outline"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        <Plus size={14} />
        {title}
        {selected.length ? ` · 已选 ${selected.length}` : ""}
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={title}
        width={760}
      >
        <SearchField
          autoFocus
          data-autofocus
          label="搜索创作对象"
          placeholder="搜索名称、场景或章节"
          value={query}
          onValueChange={(value) => {
            setQuery(value);
            setLimit(40);
          }}
          className="mb-4"
        />
        <div className="max-h-[50vh] overflow-auto space-y-2">
          {loading ? (
            <p>正在读取对象…</p>
          ) : error ? (
            <p role="alert">
              对象读取失败。
              <Button variant="ghost" onClick={onRetry}>
                重试
              </Button>
            </p>
          ) : (
            matches.slice(0, limit).map((i) => (
              <button
                key={i.value}
                disabled={disabled || selected.includes(i.value)}
                aria-label={`选择对象 ${i.description} ${i.label}`}
                onClick={() => {
                  onSelect(i.value);
                  if (closeOnSelect) setOpen(false);
                }}
                className="w-full text-left flex items-center justify-between gap-3 p-4 border rounded-lg hover:bg-elevated disabled:opacity-60"
              >
                {renderPreview && (
                  <span className="w-16 h-12 shrink-0 rounded-lg overflow-hidden bg-elevated">
                    {renderPreview(i.value)}
                  </span>
                )}
                <span className="min-w-0 flex-1">
                  <strong className="block text-sm font-medium truncate">
                    {i.label}
                  </strong>
                  <small className="block text-xs text-muted-foreground mt-1">
                    {i.description}
                  </small>
                </span>
                {selected.includes(i.value) ? (
                  <Check size={16} />
                ) : (
                  <Plus size={16} />
                )}
              </button>
            ))
          )}
          {!loading && !error && !matches.length && (
            <p className="text-center py-10 text-muted-foreground">
              没有匹配的创作对象
            </p>
          )}
          {matches.length > limit && (
            <Button variant="ghost" onClick={() => setLimit(limit + 40)}>
              加载更多（{limit}/{matches.length}）
            </Button>
          )}
        </div>
        <div className="flex justify-between items-center mt-4">
          <span className="text-xs text-muted-foreground">
            已选择 {selected.length} 项
          </span>
          <Button onClick={() => setOpen(false)}>完成选择</Button>
        </div>
      </Modal>
    </>
  );
}
