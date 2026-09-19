import { useState, type ReactNode } from "react";
import { Search, Plus, Check } from "lucide-react";
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
}: {
  items: { id: string; name: string; context: string }[];
  selected: string[];
  onSelect: (id: string) => void;
  disabled?: boolean;
  loading?: boolean;
  error?: boolean;
  onRetry?: () => void;
  renderPreview?: (id: string) => ReactNode;
}) {
  const [open, setOpen] = useState(false),
    [query, setQuery] = useState("");
  const [limit, setLimit] = useState(40);
  const matches = items.filter((i) =>
    `${i.name} ${i.context}`.toLowerCase().includes(query.trim().toLowerCase()),
  );
  return (
    <>
      <Button
        variant="outline"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        <Plus size={14} />
        选择创作对象{selected.length ? ` · 已选 ${selected.length}` : ""}
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="选择创作对象"
        width={760}
      >
        <label className="flex gap-2 items-center border rounded-lg px-3 py-2 mb-4">
          <Search size={16} />
          <input
            autoFocus
            aria-label="搜索创作对象"
            placeholder="搜索名称、镜头编号或章节…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setLimit(40);
            }}
            className="bg-transparent outline-none min-w-0 flex-1 text-sm"
          />
        </label>
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
                key={i.id}
                disabled={disabled || selected.includes(i.id)}
                aria-label={`选择对象 ${i.context} ${i.name}`}
                onClick={() => onSelect(i.id)}
                className="w-full text-left flex items-center justify-between gap-3 p-4 border rounded-lg hover:bg-elevated disabled:opacity-60"
              >
                {renderPreview && (
                  <span className="w-16 h-12 shrink-0 rounded-lg overflow-hidden bg-elevated">
                    {renderPreview(i.id)}
                  </span>
                )}
                <span className="min-w-0 flex-1">
                  <strong className="block text-sm font-medium truncate">
                    {i.name}
                  </strong>
                  <small className="block text-xs text-muted-foreground mt-1">
                    {i.context}
                  </small>
                </span>
                {selected.includes(i.id) ? (
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
