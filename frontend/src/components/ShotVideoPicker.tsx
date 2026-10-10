import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, blobUrl } from "@/lib/api";
import {
  mediaTime,
  mediaTypeName,
  type MediaVersion,
  type MediaType,
} from "@/lib/library";
import { MediaImage } from "@/components/MediaImage";
import { LibraryMediaPreview } from "@/components/LibraryMediaPreview";
import { AudioLines } from "lucide-react";
import { Button } from "@/components/ui/button";

type Version = MediaVersion & { name: string };
export function ShotVideoPicker({
  projectId,
  shotId,
}: {
  projectId: string;
  shotId: string;
}) {
  const [query, setQuery] = useState(""),
    [mediaType, setMediaType] = useState<MediaType | "all">("all"),
    [offset, setOffset] = useState(0),
    [selected, setSelected] = useState<Version | null>(null);
  const [start, setStart] = useState("0"),
    [end, setEnd] = useState(""),
    [purpose, setPurpose] = useState("visual_reference");
  const qc = useQueryClient(),
    base = `/projects/${projectId}/library`;
  const catalog = useQuery({
    queryKey: [
      "library-version-catalog",
      projectId,
      "shot-picker",
      query,
      offset,
      mediaType,
    ],
    queryFn: () =>
      api.get<{ items: Version[]; total: number }>(
        `${base}/version-catalog?query=${encodeURIComponent(query)}&offset=${offset}&limit=12${mediaType === "all" ? "" : `&media_type=${mediaType}`}`,
      ),
  });
  const startMs = Math.round(Number(start) * 1000),
    endMs = Math.round(Number(end) * 1000);
  const valid =
    !!selected &&
    (selected.media_type === "image" ||
      (start !== "" &&
        end !== "" &&
        Number.isFinite(startMs) &&
        Number.isFinite(endMs) &&
        startMs >= 0 &&
        endMs - startMs >= 100 &&
        endMs <= (selected.duration_ms ?? 0)));
  const save = useMutation({
    mutationFn: () =>
      api.post(`${base}/usages`, {
        version_id: selected!.id,
        shot_id: shotId,
        start_ms: selected?.media_type === "image" ? null : startMs,
        end_ms: selected?.media_type === "image" ? null : endMs,
        purpose,
        note: "",
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({
        queryKey: ["media-usages", projectId, shotId],
      });
      setSelected(null);
    },
  });
  return (
    <section className="mb-6 border-b pb-5">
      {!selected ? (
        <>
          <div className="reference-tools">
            <input
              type="search"
              aria-label="搜索参考素材"
              placeholder="搜索图片、音频或视频…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setOffset(0);
              }}
            />
            <select
              aria-label="引用素材类型"
              value={mediaType}
              onChange={(e) => {
                setMediaType(e.target.value as MediaType | "all");
                setOffset(0);
              }}
            >
              <option value="all">全部类型</option>
              <option value="video">视频</option>
              <option value="image">图片</option>
              <option value="audio">音频</option>
            </select>
          </div>
          {catalog.isPending ? (
            <p role="status">正在读取素材…</p>
          ) : catalog.isError ? (
            <p role="alert">
              素材读取失败{" "}
              <Button onClick={() => void catalog.refetch()}>重试</Button>
            </p>
          ) : (
            <>
              <div className="reference-catalog">
                {catalog.data.items.map((v) => (
                  <button
                    key={v.id}
                    className="reference-card"
                    aria-label={`选择${mediaTypeName(v.media_type, true)} ${v.name} 版本 ${v.ordinal}`}
                    onClick={() => {
                      save.reset();
                      setSelected(v);
                      setStart("0");
                      setEnd(String(Math.min(v.duration_ms ?? 0, 5000) / 1000));
                    }}
                  >
                    <div>
                      {v.media_type === "audio" ? (
                        <span className="flex aspect-video items-center justify-center bg-elevated text-muted-foreground">
                          <AudioLines size={32} />
                        </span>
                      ) : (
                        <MediaImage
                          src={
                            v.poster_hash
                              ? blobUrl(projectId, v.poster_hash)
                              : null
                          }
                          alt={v.name}
                        />
                      )}
                    </div>
                    <strong>{v.name}</strong>
                    <small>
                      版本 {v.ordinal} ·{" "}
                      {v.media_type === "image"
                        ? "完整图片"
                        : mediaTime(v.duration_ms ?? 0)}
                    </small>
                  </button>
                ))}
              </div>
              {!catalog.data.total && (
                <p className="py-7 text-center text-sm text-muted-foreground">
                  {query
                    ? "没有匹配的素材"
                    : "还没有可用素材，请先在素材库导入。"}
                </p>
              )}
              {catalog.data.total > 12 && (
                <div className="flex justify-end gap-2 mt-3">
                  <Button
                    variant="ghost"
                    disabled={!offset}
                    onClick={() => setOffset(offset - 12)}
                  >
                    上一页
                  </Button>
                  <Button
                    variant="ghost"
                    disabled={offset + 12 >= catalog.data.total}
                    onClick={() => setOffset(offset + 12)}
                  >
                    下一页
                  </Button>
                </div>
              )}
            </>
          )}
        </>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <strong>
              {selected.name} · 版本 {selected.ordinal}
            </strong>
            <Button
              variant="ghost"
              disabled={save.isPending}
              onClick={() => setSelected(null)}
            >
              重新选择素材
            </Button>
          </div>
          <div className="max-w-xl mx-auto">
            {selected.proxy_hash && (
              <LibraryMediaPreview
                key={selected.id}
                mediaType={selected.media_type}
                src={blobUrl(projectId, selected.proxy_hash)}
                start={valid ? startMs : 0}
                end={valid ? endMs : (selected.duration_ms ?? 0)}
                label={selected.name}
              />
            )}
          </div>
          <fieldset disabled={save.isPending} className="reference-tools">
            {selected.media_type !== "image" && (
              <>
                <label className="text-sm">
                  起点（秒）
                  <input
                    aria-label="引用起点秒"
                    type="number"
                    min="0"
                    step="0.1"
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                    className="block w-32 mt-1"
                  />
                </label>
                <label className="text-sm">
                  终点（秒）
                  <input
                    aria-label="引用终点秒"
                    type="number"
                    min="0"
                    step="0.1"
                    value={end}
                    onChange={(e) => setEnd(e.target.value)}
                    className="block w-32 mt-1"
                  />
                </label>
              </>
            )}
            <label className="text-sm">
              用途
              <select
                aria-label="片段引用用途"
                className="block mt-1"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
              >
                <option value="visual_reference">创作参考</option>
                <option value="editing_source">剪辑备选</option>
              </select>
            </label>
          </fieldset>
          {!valid && (
            <p className="text-sm text-danger">
              请在音视频时长内选择至少 0.1 秒的片段。
            </p>
          )}
          <Button
            disabled={!valid || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "正在添加…" : "添加到当前镜头"}
          </Button>
        </div>
      )}
      {save.error && (
        <p role="alert" className="text-sm text-danger mt-3">
          {save.error.message}
        </p>
      )}
      {save.isSuccess && (
        <p role="status" className="text-sm text-primary mt-3">
          素材已加入当前镜头，原版本与所选内容已保留。
        </p>
      )}
    </section>
  );
}
