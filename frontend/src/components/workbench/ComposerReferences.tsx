import { SearchField } from "@/components/ui/search-field";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, blobUrl } from "@/lib/api";
import { MediaImage } from "@/components/MediaImage";
import { Button } from "@/components/ui/button";
import { FileAudio } from "lucide-react";
import type { ReferenceSource } from "./referenceInput";
import { referencePickState, type ReferenceSelection } from "./referenceProblem";
import "./attachments-ux.css";
export type ComposerReference = {
  id: string;
  source_type?: ReferenceSource;
  blob_hash: string;
  title: string;
  role: "reference" | "first_frame" | "last_frame";
  kind?: "image" | "video" | "audio";
  thumbnail_hash?: string | null;
};

export function ReferencePreview({ projectId, item }: { projectId: string; item: ComposerReference }) {
  if (item.kind === "audio") return <div className="composer-reference-video flex items-center justify-center"><FileAudio size={32} /></div>;
  if (item.kind === "video" && !item.thumbnail_hash)
    return <div className="composer-reference-video"><video src={blobUrl(projectId, item.blob_hash)} muted preload="metadata" aria-label={`参考视频 ${item.title}`} style={{ width: "100%", height: "100%", objectFit: "cover" }} /></div>;
  return <MediaImage src={blobUrl(projectId, item.thumbnail_hash || item.blob_hash)} alt={item.title} />;
}
export function ComposerReferences({
  projectId,
  selected,
  pick,
  includeVideos = false,
  includeAudio = false,
  maxSelected = 8,
  selection,
}: {
  projectId: string;
  selected: ComposerReference[];
  pick: (item: ComposerReference) => void;
  includeVideos?: boolean;
  includeAudio?: boolean;
  maxSelected?: number;
  selection?: ReferenceSelection;
}) {
  const [query, setQuery] = useState(""),
    [offset, setOffset] = useState(0);
  const images = useQuery({
    queryKey: ["workbench-references", projectId, query, offset, includeVideos, includeAudio],
    queryFn: () =>
      api.get<{ items: Omit<ComposerReference, "role">[]; has_more: boolean }>(
        `/projects/${projectId}/workbench/references?offset=${offset}&query=${encodeURIComponent(query)}&media_type=${includeAudio ? "any" : includeVideos ? "all" : "image"}`,
      ),
  });
  return (
    <div className="composer-reference-picker">
      <p>从项目已有的生成结果或上传素材中选择。{!selection && `最多 ${maxSelected} 项。`}{!selection && (includeAudio ? "可选择图片、视频和音频。" : includeVideos ? "可混合选择图片和视频。" : "可选择图片。")}参考会随本次创作保存。</p>
      <SearchField
        className="w-full"
        label="搜索参考素材"
        placeholder="搜索素材名称、描述或提示词"
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
        <p role="status">正在读取素材…</p>
      ) : (
        <div className="composer-reference-grid">
          {images.data.items.map((item) => {
            const isSelected = selected.some(r => r.id === item.id && (r.source_type || "generation") === (item.source_type || "generation"));
            const candidate = { ...item, role: "reference" as const };
            const state = selection ? referencePickState(selection, candidate) : { role: candidate.role, problem: "" };
            const problem = isSelected ? "" : state.problem || (selected.length >= maxSelected ? "已达到本次添加上限，请先移除已选内容。" : "");
            return (
            <button
              type="button"
              key={`${item.source_type || "generation"}:${item.id}`}
              aria-label={`引用 ${item.title}`}
              aria-pressed={isSelected}
              disabled={!!problem}
              title={problem || (isSelected ? "已选择，点击移除" : undefined)}
              onClick={() => pick({ ...item, role: state.role })}
            >
              <ReferencePreview projectId={projectId} item={candidate} />
              <span>{item.kind === "video" ? "视频 · " : item.kind === "audio" ? "音频 · " : "图片 · "}{item.title}</span>
              {problem && <small className="reference-selection-reason">{problem}</small>}
              {isSelected && <small className="reference-selection-status">已选择 · 点击移除</small>}
              {!problem && !isSelected && state.role !== "reference" && <small className="reference-selection-status">将用作{state.role === "first_frame" ? "首帧" : "尾帧"}</small>}
            </button>
            );
          })}
        </div>
      )}
      {images.data?.items.length === 0 && (
        <p>
          暂无匹配素材。可先在素材或镜头中上传，也可以用工作台的生成结果作为下一次参考。
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
