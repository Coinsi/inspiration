import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Star } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { useI18n } from "@/lib/i18n";
import { usePersistentState } from "@/lib/usePersistentState";
import { ZoomableImage } from "@/components/ImageViewer";
import { api, blobUrl, type GenJob, type Generation, type ProviderConfig, type Quota } from "@/lib/api";

// 各供应商支持的模态(gpt_image 仅图片)
const PROVIDER_LABEL: Record<string, string> = { mock: "Mock", jimeng: "即梦", gpt_image: "GPT Image" };
const VIDEO_PROVIDERS = new Set(["mock", "jimeng"]);

export default function GenerationPanel({
  projectId,
  targetType,
  targetId,
}: {
  projectId: string;
  targetType: "shot" | "asset";
  targetId: string;
}) {
  const base = `/projects/${projectId}`;
  const t = `${targetType}s`; // shots | assets
  const qc = useQueryClient();
  const toast = useToast();
  const { t: tr } = useI18n();
  const [type, setType] = useState("image");
  const [count, setCount] = useState(targetType === "asset" ? 4 : 3);
  const [useRefs, setUseRefs] = useState(true);
  const [est, setEst] = useState<number | null>(null);
  const [provider, setProvider] = usePersistentState<string>(`gen.provider.${projectId}`, "mock");

  const { data: providerConfigs } = useQuery({
    queryKey: ["providers", projectId],
    queryFn: () => api.get<ProviderConfig[]>(`${base}/providers`),
  });
  // 可选供应商:mock + 已配置且启用的生成供应商
  const providerOptions = [
    "mock",
    ...(providerConfigs ?? []).filter((p) => p.kind === "generation" && p.enabled && p.provider_name !== "mock").map((p) => p.provider_name),
  ];
  // 当前供应商不支持视频时强制回到图片
  useEffect(() => {
    if (type === "video" && !VIDEO_PROVIDERS.has(provider)) setType("image");
  }, [provider, type]);

  const { data: gens } = useQuery({
    queryKey: ["gens", targetType, targetId],
    queryFn: () => api.get<Generation[]>(`${base}/generations?target_type=${targetType}&target_id=${targetId}`),
  });
  const { data: quota } = useQuery({
    queryKey: ["quota", projectId],
    queryFn: () => api.get<Quota | null>(`${base}/quota`),
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["gens", targetType, targetId] });
    void qc.invalidateQueries({ queryKey: ["quota", projectId] });
    void qc.invalidateQueries({ queryKey: ["asset", targetId] });
    void qc.invalidateQueries({ queryKey: ["assets", projectId] });
    void qc.invalidateQueries({ queryKey: ["all-shots", projectId] });
  };

  const estimate = useMutation({
    mutationFn: () =>
      api.post<{ points: number }>(`${base}/${t}/${targetId}/estimate`, {
        provider,
        request_type: type,
        count,
      }),
    onSuccess: (d) => setEst(d.points),
  });
  const generate = useMutation({
    mutationFn: () =>
      api.post<GenJob>(`${base}/${t}/${targetId}/generate`, {
        provider,
        request_type: type,
        count,
        use_references: targetType === "asset" ? useRefs : undefined,
      }),
    onSuccess: (j) => {
      refresh();
      if (j.status === "failed") toast.push(j.error || tr("toast.genFailed"), "error");
      else toast.push(tr("toast.genDone"), "success");
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const select = useMutation({
    mutationFn: (id: string) => api.post(`${base}/generations/${id}/select`),
    onSuccess: refresh,
  });
  const favorite = useMutation({
    mutationFn: ({ id, fav }: { id: string; fav: boolean }) =>
      api.patch(`${base}/generations/${id}`, { is_favorite: fav }),
    onSuccess: refresh,
  });

  const selectLabel = targetType === "asset" ? tr("gen.setAvatar") : tr("gen.pick");
  const selectedLabel = targetType === "asset" ? tr("gen.curAvatar") : tr("gen.picked");

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap rounded-lg border border-border bg-card p-2.5">
        <span className="text-xs text-muted-foreground">
          {targetType === "asset" ? tr("gen.concept") : tr("gen.gen")}
        </span>
        <select
          className="h-8 rounded-md border border-border bg-bg px-2 text-sm"
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          title={tr("gen.provider")}
        >
          {providerOptions.map((p) => (
            <option key={p} value={p}>{PROVIDER_LABEL[p] ?? p}</option>
          ))}
        </select>
        <select
          className="h-8 rounded-md border border-border bg-bg px-2 text-sm"
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          <option value="image">{tr("gen.image")}</option>
          {targetType === "shot" && VIDEO_PROVIDERS.has(provider) && <option value="video">{tr("gen.video")}</option>}
        </select>
        <select
          className="h-8 rounded-md border border-border bg-bg px-2 text-sm"
          value={count}
          onChange={(e) => setCount(Number(e.target.value))}
        >
          {[1, 2, 3, 4].map((n) => (
            <option key={n} value={n}>
              {n} {tr("gen.count")}
            </option>
          ))}
        </select>
        {targetType === "asset" && (
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
            <input type="checkbox" checked={useRefs} onChange={(e) => setUseRefs(e.target.checked)} className="accent-[var(--primary)]" />
            {tr("gen.useRefs")}
          </label>
        )}
        <Button size="sm" variant="outline" onClick={() => estimate.mutate()}>
          {tr("gen.estimate")}
        </Button>
        {est !== null && <span className="text-xs">≈ {est} {tr("gen.points")}</span>}
        <Button size="sm" onClick={() => generate.mutate()} disabled={generate.isPending}>
          {tr("gen.gen")}
        </Button>
        {quota && (
          <span className="ml-auto text-xs text-muted-foreground">
            {tr("gen.quota")} {quota.used_cost}/{quota.limit_cost} {tr("gen.points")}
          </span>
        )}
      </div>

      {gens && gens.length > 0 ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-2.5">
          {gens.map((g) => (
            <div
              key={g.id}
              className={`group relative overflow-hidden rounded-xl border bg-card transition-all ${
                g.is_selected ? "border-primary shadow-glow-sm ring-1 ring-primary" : "border-border hover:border-primary/50"
              }`}
            >
              {/* 钦定金角标 */}
              {g.is_selected && (
                <span className="absolute left-0 top-0 z-10 rounded-br-lg bg-gradient-primary px-2 py-0.5 text-[10px] font-semibold text-primary-foreground shadow-glow-sm">
                  {selectedLabel}
                </span>
              )}
              {g.output_blob_hash &&
                (g.output_type === "video" ? (
                  <div className="flex h-28 items-center justify-center bg-elevated text-xs text-muted-foreground">🎬 {tr("gen.video")}</div>
                ) : (
                  <ZoomableImage
                    src={blobUrl(projectId, g.output_blob_hash)}
                    filename={`${targetType}-${g.id.slice(0, 8)}`}
                    className="h-28 w-full bg-elevated object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                  />
                ))}
              <div className="flex items-center justify-between px-2 py-1.5">
                <button
                  className={`transition ${g.is_favorite ? "text-primary" : "text-faint hover:text-foreground"}`}
                  onClick={() => favorite.mutate({ id: g.id, fav: !g.is_favorite })}
                  title={tr("gen.favorite")}
                >
                  <Star className="h-3.5 w-3.5" fill={g.is_favorite ? "currentColor" : "none"} />
                </button>
                <button
                  className={`text-xs font-medium transition ${g.is_selected ? "text-primary" : "text-muted-foreground hover:text-primary"}`}
                  onClick={() => select.mutate(g.id)}
                >
                  {g.is_selected ? selectedLabel : selectLabel}
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="py-8 text-center text-sm text-muted-foreground">{tr("gen.emptyVariants")}</p>
      )}
    </div>
  );
}
