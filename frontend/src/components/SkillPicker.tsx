import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { SkillCard, SkillUse } from "@/lib/skills";
import { Button } from "@/components/ui/button";

export default function SkillPicker({
  projectId,
  value,
  onChange,
  disabled = false,
  max = 4,
}: {
  projectId: string;
  value: SkillUse[];
  onChange: (skills: SkillUse[]) => void;
  disabled?: boolean;
  max?: number;
}) {
  const [open, setOpen] = useState(false),
    [q, setQ] = useState(""),
    [offset, setOffset] = useState(0);
  const list = useQuery({
    queryKey: ["skills", projectId, "picker", q, offset],
    queryFn: () =>
      api.get<{ items: SkillCard[]; total: number }>(
        `/projects/${projectId}/skills/catalog?q=${encodeURIComponent(q)}&offset=${offset}`,
      ),
    enabled: open,
  });
  return (
    <div className="space-y-2 text-xs" data-testid="skill-picker">
      <button
        type="button"
        className="text-muted-foreground hover:text-foreground"
        disabled={disabled}
        onClick={() => setOpen(!open)}
      >
        ✦ 创作技能{" "}
        {value.length ? `· 已选 ${value.length}/${max}` : "· 按需添加"}{" "}
        {open ? "收起" : "+"}
      </button>
      {!!value.length && (
        <div className="flex flex-wrap gap-2">
          {value.map((s) => (
            <button
              type="button"
              key={s.id}
              disabled={disabled}
              className="rounded-full border border-border px-2 py-1"
              onClick={() => onChange(value.filter((v) => v.id !== s.id))}
              aria-label={`移除技能 ${s.name ?? s.id}`}
            >
              {s.name ?? "技能"} · v{s.revision} ×
            </button>
          ))}
        </div>
      )}
      {open && (
        <div className="rounded-lg border border-border bg-background p-3 space-y-2">
          <input
            aria-label="搜索创作技能"
            placeholder="搜索本项目技能"
            className="w-full bg-transparent p-1 outline-none"
            value={q}
            disabled={disabled}
            maxLength={200}
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
            }}
          />
          <p className="text-muted-foreground">
            选定版本用于本次创作，最多 {max} 项。包内脚本不会自动执行。
          </p>
          {list.isLoading && <p>正在读取…</p>}
          {list.error && (
            <p role="alert">
              {list.error.message}
              <button type="button" onClick={() => void list.refetch()}>
                {" "}
                重试
              </button>
            </p>
          )}
          {list.data?.items.map((s) => (
            <label key={s.id} className="flex gap-2 py-1">
              <input
                type="checkbox"
                disabled={
                  disabled ||
                  (value.length >= max && !value.some((v) => v.id === s.id))
                }
                checked={value.some((v) => v.id === s.id)}
                onChange={(e) =>
                  onChange(
                    e.target.checked
                      ? [
                          ...value,
                          { id: s.id!, revision: s.revision!, name: s.name },
                        ]
                      : value.filter((v) => v.id !== s.id),
                  )
                }
              />
              <span>
                {s.name}{" "}
                <span className="text-muted-foreground">
                  v{s.revision} · {s.description}
                </span>
              </span>
            </label>
          ))}
          {list.data?.total === 0 && (
            <p>暂无匹配技能，请到技能库创建或导入。</p>
          )}
          {!!list.data && list.data.total > 24 && (
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={!offset}
                onClick={() => setOffset(offset - 24)}
              >
                上一页
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={offset + 24 >= list.data.total}
                onClick={() => setOffset(offset + 24)}
              >
                下一页
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
