import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  api,
  blobUrl,
  type Asset,
  type Generation,
  type RefImage,
} from "@/lib/api";
import { MediaImage } from "@/components/MediaImage";
import { useI18n } from "@/lib/i18n";
import { Button } from "@/components/ui/button";

export type PromptReference = {
  kind: "generation" | "asset_reference";
  id: string;
  blob_hash: string;
  title: string;
};

/** Uses existing project identities; a preview URL is never a reference identity. */
export function ImageReferencePicker({
  projectId,
  targetType,
  targetId,
  value,
  onChange,
  disabled,
}: {
  projectId: string;
  targetType: "shot" | "asset";
  targetId: string;
  value: PromptReference[];
  onChange: (refs: PromptReference[]) => void;
  disabled: boolean;
}) {
  const zh = useI18n().lang === "zh";
  const [assetId, setAssetId] = useState("");
  const [search, setSearch] = useState("");
  const base = `/projects/${projectId}`;
  const assets = useQuery({
    queryKey: ["assets", projectId, "reference-picker"],
    queryFn: () => api.get<Asset[]>(`${base}/assets`),
  });
  const scopeId = assetId || targetId,
    scopeType = assetId ? "asset" : targetType;
  const images = useQuery({
    queryKey: ["prompt-reference-options", projectId, scopeType, scopeId],
    queryFn: async () => {
      const [generations, references] = await Promise.all([
        api.get<Generation[]>(
          `${base}/generations?target_type=${scopeType}&target_id=${scopeId}`,
        ),
        scopeType === "asset"
          ? api.get<RefImage[]>(`${base}/assets/${scopeId}/reference-images`)
          : Promise.resolve([]),
      ]);
      return [
        ...references.map((r, i): PromptReference => ({
          kind: "asset_reference",
          id: r.id,
          blob_hash: r.blob_hash,
          title: r.note || `${zh ? "参考图" : "Reference"} ${i + 1}`,
        })),
        ...generations
          .filter((g) => g.output_type === "image" && g.output_blob_hash)
          .map((g): PromptReference => ({
            kind: "generation",
            id: g.id,
            blob_hash: g.output_blob_hash!,
            title: `${g.is_selected ? (zh ? "已选定" : "Selected") : g.provider} · ${g.id.slice(0, 8)}`,
          })),
      ];
    },
  });
  return (
    <div className="space-y-3 rounded-lg border bg-surface p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium">
          {zh ? "参考图片" : "Reference images"} · {value.length}/4
        </span>
        <span className="text-xs text-muted-foreground">
          {zh
            ? "只发送所选图片的缩小预览"
            : "Only selected, resized previews are sent"}
        </span>
      </div>
      <input
        className="h-9 w-full rounded-md border bg-bg px-3 text-sm"
        aria-label={zh ? "查找参考资产" : "Find reference assets"}
        placeholder={
          zh ? "查找角色、场景或道具…" : "Find characters, scenes or props…"
        }
        value={search}
        disabled={disabled}
        onChange={(e) => setSearch(e.target.value)}
      />
      <select
        aria-label={zh ? "参考图来源" : "Reference source"}
        className="h-9 w-full rounded-md border bg-bg px-2 text-sm"
        value={assetId}
        disabled={disabled}
        onChange={(e) => setAssetId(e.target.value)}
      >
        <option value="">
          {zh ? "当前对象的图片" : "Images from this object"}
        </option>
        {assets.data
          ?.filter(
            (a) =>
              a.id === assetId ||
              `${a.name} ${a.code}`
                .toLowerCase()
                .includes(search.trim().toLowerCase()),
          )
          .map((a) => (
            <option key={a.id} value={a.id}>
              {a.name} · {a.code}
            </option>
          ))}
      </select>
      {assets.isError && (
        <p role="alert" className="text-xs text-danger">
          {zh ? "资产列表加载失败" : "Asset list unavailable"}{" "}
          <button className="underline" onClick={() => void assets.refetch()}>
            {zh ? "重试" : "Retry"}
          </button>
        </p>
      )}
      {value.length > 0 && (
        <div
          className="flex flex-wrap gap-2"
          aria-label={zh ? "已选参考" : "Selected references"}
        >
          {value.map((r) => (
            <Button
              key={`${r.kind}.${r.id}`}
              size="sm"
              variant="outline"
              className="max-w-full"
              disabled={disabled}
              onClick={() =>
                onChange(
                  value.filter((v) => v.id !== r.id || v.kind !== r.kind),
                )
              }
            >
              <span className="truncate" title={r.title}>
                {r.title}
              </span>{" "}
              ×
            </Button>
          ))}
        </div>
      )}
      {images.isPending ? (
        <p className="text-xs text-muted-foreground">
          {zh ? "加载图片…" : "Loading images…"}
        </p>
      ) : images.isError ? (
        <p role="alert" className="text-xs text-danger">
          {zh ? "参考图片加载失败" : "Images unavailable"}{" "}
          <button className="underline" onClick={() => void images.refetch()}>
            {zh ? "重试" : "Retry"}
          </button>
        </p>
      ) : images.data?.length ? (
        <div className="grid max-h-64 grid-cols-2 gap-2 overflow-auto sm:grid-cols-4">
          {images.data.map((r) => {
            const active = value.some(
              (v) => v.id === r.id && v.kind === r.kind,
            );
            return (
              <button
                type="button"
                key={`${r.kind}.${r.id}`}
                aria-label={r.title}
                aria-pressed={active}
                disabled={disabled || (!active && value.length >= 4)}
                onClick={() =>
                  onChange(
                    active
                      ? value.filter((v) => v.id !== r.id || v.kind !== r.kind)
                      : [
                          ...value,
                          {
                            ...r,
                            title: `${assets.data?.find((a) => a.id === scopeId)?.name || (zh ? "当前镜头" : "Current shot")} · ${r.title}`,
                          },
                        ],
                  )
                }
                className={`overflow-hidden rounded-md border text-left disabled:opacity-40 ${active ? "border-primary ring-1 ring-primary" : "border-border"}`}
              >
                <div className="relative aspect-video">
                  <MediaImage
                    src={blobUrl(projectId, r.blob_hash)}
                    alt={r.title}
                    className="[&_img]:object-contain"
                  />
                </div>
                <p className="truncate p-2 text-xs">
                  {active ? "✓ " : ""}
                  {r.title}
                </p>
              </button>
            );
          })}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          {zh
            ? "此对象暂无图片，可选择其他资产，或先在详情中上传参考图。"
            : "No images here. Choose another asset or upload a reference in its details."}
        </p>
      )}
    </div>
  );
}
