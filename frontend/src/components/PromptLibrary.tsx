import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Copy, Search, Type } from "lucide-react";
import { api, type Prompt, type Fragment } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";

export const PROMPT_CATEGORIES: Record<string, string> = {
  quality: "画质",
  camera: "镜头",
  lighting: "光线",
  style: "风格",
  custom: "其他",
};
type Entry = {
  id: string;
  code: string;
  name: string;
  text: string;
  negative: string;
  category: string;
  prompt?: Prompt;
  fragment?: Fragment;
};
export function PromptLibrary({
  projectId,
  onInsert,
  onEdit,
  onEditFragment,
  onDeleteFragment,
  deleting,
}: {
  projectId: string;
  onInsert?: (text: string) => void;
  onEdit?: (prompt: Prompt) => void;
  onEditFragment?: (fragment: Fragment) => void;
  onDeleteFragment?: (fragment: Fragment) => void;
  deleting?: boolean;
}) {
  const [query, setQuery] = useState(""),
    [category, setCategory] = useState(""),
    [selectedId, setSelectedId] = useState<string | null>(null);
  const toast = useToast();
  const prompts = useQuery({
    queryKey: ["prompts", projectId],
    queryFn: () => api.get<Prompt[]>(`/projects/${projectId}/prompts`),
  });
  const fragments = useQuery({
    queryKey: ["fragments", projectId],
    queryFn: () =>
      api.get<Fragment[]>(`/projects/${projectId}/prompt-fragments`),
  });
  const entries: Entry[] = [
    ...(prompts.data ?? []).map((p) => ({
      id: p.id,
      code: p.code,
      name: p.name,
      text: p.positive,
      negative: p.negative,
      category: "template",
      prompt: p,
    })),
    ...(fragments.data ?? []).map((f) => ({
      id: f.id,
      code: f.code,
      name: f.name,
      text: f.text,
      negative: "",
      category: f.category,
      fragment: f,
    })),
  ];
  const selected = entries.find((e) => e.id === selectedId);
  const filtered = entries.filter(
    (e) =>
      (!category || e.category === category) &&
      `${e.name} ${e.code} ${e.text}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  const categories: Record<string, string> = {
    template: "完整提示词",
    ...PROMPT_CATEGORIES,
  };
  if (selected)
    return (
      <div className="space-y-5">
        <Button variant="ghost" onClick={() => setSelectedId(null)}>
          <ArrowLeft size={15} />
          返回提示词列表
        </Button>
        <div>
          <p className="text-xs text-muted-foreground mb-2">
            {categories[selected.category] ?? selected.category} ·{" "}
            {selected.code}
          </p>
          <h3 className="text-xl font-semibold">{selected.name}</h3>
        </div>
        <div className="rounded-xl bg-elevated p-5 text-sm leading-7 whitespace-pre-wrap break-words max-h-[45vh] overflow-auto">
          {selected.text || "尚未填写内容"}
        </div>
        {!!selected.negative && (
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground">
              反向提示词（单独保存，不追加到正向指令）
            </summary>
            <p className="mt-3 whitespace-pre-wrap break-words">
              {selected.negative}
            </p>
          </details>
        )}
        <div className="flex flex-wrap gap-2">
          {onInsert && (
            <Button
              disabled={!selected.text.trim()}
              onClick={() => onInsert(selected.text)}
            >
              追加到创作指令
            </Button>
          )}
          <Button
            variant="outline"
            disabled={!selected.text.trim()}
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(selected.text);
                toast.push("提示词已复制", "success");
              } catch {
                toast.push("复制失败，请选中文字后复制", "error");
              }
            }}
          >
            <Copy size={14} />
            复制内容
          </Button>
          {selected.fragment && onEditFragment && (
            <Button
              variant="ghost"
              disabled={deleting}
              onClick={() => onEditFragment(selected.fragment!)}
            >
              编辑片段
            </Button>
          )}
          {selected.fragment && onDeleteFragment && (
            <Button
              variant="ghost"
              disabled={deleting}
              onClick={() => onDeleteFragment(selected.fragment!)}
            >
              {deleting ? "正在删除…" : "删除片段"}
            </Button>
          )}
          {selected.prompt && onEdit && (
            <Button variant="ghost" onClick={() => onEdit(selected.prompt!)}>
              编辑提示词
            </Button>
          )}
        </div>
      </div>
    );
  return (
    <div className="space-y-5">
      <label className="flex items-center gap-2 border rounded-xl px-4 py-3 bg-card">
        <Search size={16} className="text-muted-foreground" />
        <input
          type="search"
          aria-label="搜索提示词"
          placeholder="搜索名称、内容或编号…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="bg-transparent outline-none flex-1 min-w-0 text-sm"
        />
      </label>
      <div className="flex flex-wrap gap-2" aria-label="提示词分类">
        {Object.entries({ "": "全部", ...categories }).map(([key, label]) => (
          <button
            key={key}
            aria-pressed={category === key}
            onClick={() => setCategory(key)}
            className={`px-3 py-1.5 text-sm rounded-lg ${category === key ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-elevated"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {(prompts.isError || fragments.isError) && (
        <div role="alert" className="rounded-lg border p-4 text-sm">
          部分提示词未能读取，已有内容仍可查看。
          <Button
            variant="ghost"
            onClick={() => {
              void prompts.refetch();
              void fragments.refetch();
            }}
          >
            重试
          </Button>
        </div>
      )}
      {prompts.isPending || fragments.isPending ? (
        <p role="status" className="py-12 text-center text-muted-foreground">
          正在读取提示词…
        </p>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
            {filtered.map((e) => (
              <button
                key={e.id}
                aria-label={`查看提示词 ${e.name}`}
                onClick={() => setSelectedId(e.id)}
                className="rounded-xl border bg-card p-5 text-left hover:border-primary/50 transition-colors min-w-0"
              >
                <div className="flex items-center justify-between text-xs text-muted-foreground mb-5">
                  <Type size={18} />
                  <span>{categories[e.category] ?? e.category}</span>
                </div>
                <h3 className="text-base font-medium truncate">{e.name}</h3>
                <p className="mt-2 text-sm leading-6 text-muted-foreground line-clamp-3 min-h-[72px] break-words">
                  {e.text || "尚未填写内容"}
                </p>
                <p className="text-xs text-muted-foreground mt-5">
                  查看与使用 →
                </p>
              </button>
            ))}
          </div>
          {!filtered.length && !prompts.isError && !fragments.isError && (
            <div className="py-14 text-center">
              <Type size={28} className="mx-auto mb-4 text-muted-foreground" />
              <p>
                {query || category
                  ? "没有匹配的提示词"
                  : "把常用的画面描述留在这里"}
              </p>
              <p className="text-sm text-muted-foreground mt-2">
                {query || category
                  ? "换个关键词或分类再试试。"
                  : "保存镜头、光线和风格描述，创作时随时取用。"}
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
