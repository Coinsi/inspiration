import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, blobUrl } from "@/lib/api";
import { mediaTime, type MediaVersion } from "@/lib/library";
import { MediaImage } from "@/components/MediaImage";
import { MediaSegmentPlayer } from "@/components/MediaSegmentPlayer";
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
    ],
    queryFn: () =>
      api.get<{ items: Version[]; total: number }>(
        `${base}/version-catalog?query=${encodeURIComponent(query)}&offset=${offset}&limit=12`,
      ),
  });
  const startMs = Math.round(Number(start) * 1000),
    endMs = Math.round(Number(end) * 1000);
  const valid =
    !!selected &&
    start !== "" &&
    end !== "" &&
    Number.isFinite(startMs) &&
    Number.isFinite(endMs) &&
    startMs >= 0 &&
    endMs - startMs >= 100 &&
    endMs <= (selected.duration_ms ?? 0);
  const save = useMutation({
    mutationFn: () =>
      api.post(`${base}/usages`, {
        version_id: selected!.id,
        shot_id: shotId,
        start_ms: startMs,
        end_ms: endMs,
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
              aria-label="搜索参考视频"
              placeholder="搜索视频素材…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setOffset(0);
              }}
            />
          </div>
          {catalog.isPending ? (
            <p role="status">正在读取视频…</p>
          ) : catalog.isError ? (
            <p role="alert">
              视频读取失败{" "}
              <Button onClick={() => void catalog.refetch()}>重试</Button>
            </p>
          ) : (
            <>
              <div className="reference-catalog">
                {catalog.data.items.map((v) => (
                  <button
                    key={v.id}
                    className="reference-card"
                    aria-label={`选择视频 ${v.name} 版本 ${v.ordinal}`}
                    onClick={() => {
                      save.reset();
                      setSelected(v);
                      setStart("0");
                      setEnd(String(Math.min(v.duration_ms ?? 0, 5000) / 1000));
                    }}
                  >
                    <div>
                      <MediaImage
                        src={
                          v.poster_hash
                            ? blobUrl(projectId, v.poster_hash)
                            : null
                        }
                        alt={v.name}
                      />
                    </div>
                    <strong>{v.name}</strong>
                    <small>
                      版本 {v.ordinal} · {mediaTime(v.duration_ms ?? 0)}
                    </small>
                  </button>
                ))}
              </div>
              {!catalog.data.total && (
                <p className="py-7 text-center text-sm text-muted-foreground">
                  {query
                    ? "没有匹配的视频"
                    : "还没有处理完成的视频，请先在视频素材库导入。"}
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
              重新选择视频
            </Button>
          </div>
          <div className="max-w-xl mx-auto">
            {selected.proxy_hash && (
              <MediaSegmentPlayer
                key={selected.id}
                src={blobUrl(projectId, selected.proxy_hash)}
                start={valid ? startMs : 0}
                end={valid ? endMs : (selected.duration_ms ?? 0)}
                label={selected.name}
              />
            )}
          </div>
          <fieldset disabled={save.isPending} className="reference-tools">
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
            <label className="text-sm">
              用途
              <select
                aria-label="片段引用用途"
                className="block mt-1"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
              >
                <option value="visual_reference">画面参考</option>
                <option value="editing_source">剪辑备选</option>
              </select>
            </label>
          </fieldset>
          {!valid && (
            <p className="text-sm text-danger">
              请在视频时长内选择至少 0.1 秒的片段。
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
          片段已加入当前镜头，原片版本与时间范围已保留。
        </p>
      )}
    </section>
  );
}
