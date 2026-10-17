import { SearchField } from "@/components/ui/search-field";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, blobUrl } from "@/lib/api";
import { MediaImage } from "@/components/MediaImage";
import { Button } from "@/components/ui/button";
export type ComposerReference = {
  id: string;
  blob_hash: string;
  title: string;
  role: "reference" | "first_frame" | "last_frame";
};
export function ComposerReferences({
  projectId,
  selected,
  pick,
}: {
  projectId: string;
  selected: ComposerReference[];
  pick: (item: ComposerReference) => void;
}) {
  const [query, setQuery] = useState(""),
    [offset, setOffset] = useState(0);
  const images = useQuery({
    queryKey: ["workbench-references", projectId, query, offset],
    queryFn: () =>
      api.get<{ items: Omit<ComposerReference, "role">[]; has_more: boolean }>(
        `/projects/${projectId}/workbench/references?offset=${offset}&query=${encodeURIComponent(query)}`,
      ),
  });
  return (
    <div className="composer-reference-picker">
      <p>从项目已有的生成结果或上传图片中选择。参考会随本次创作保存。</p>
      <SearchField
        className="w-full"
        label="搜索参考图片"
        placeholder="按提示词查找图片"
        value={query}
        onValueChange={(value) => {
          setQuery(value);
          setOffset(0);
        }}
      />
      {images.error ? (
        <p role="alert">
          {images.error.message}
          <Button onClick={() => void images.refetch()}>重新读取</Button>
        </p>
      ) : images.isPending ? (
        <p role="status">正在读取图片…</p>
      ) : (
        <div className="composer-reference-grid">
          {images.data.items.map((item) => (
            <button
              type="button"
              key={item.id}
              aria-label={`引用 ${item.title}`}
              aria-pressed={selected.some((r) => r.id === item.id)}
              onClick={() => pick({ ...item, role: "reference" })}
            >
              <MediaImage
                src={blobUrl(projectId, item.blob_hash)}
                alt={item.title}
              />
              <span>{item.title}</span>
            </button>
          ))}
        </div>
      )}
      {images.data?.items.length === 0 && (
        <p>
          暂无匹配图片。可先在素材或镜头中上传，也可以用工作台的生成结果作为下一次参考。
        </p>
      )}
      <div className="flex gap-2 mt-4">
        <Button
          variant="outline"
          disabled={!offset}
          onClick={() => setOffset((n) => Math.max(0, n - 24))}
        >
          上一页
        </Button>
        <Button
          variant="outline"
          disabled={!images.data?.has_more}
          onClick={() => setOffset((n) => n + 24)}
        >
          下一页
        </Button>
      </div>
    </div>
  );
}
