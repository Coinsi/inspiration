import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { X, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { MediaImage } from "@/components/MediaImage";
import { api, blobUrl, type Asset, type ShotAssetRef } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function ShotAssetRefs({
  projectId,
  shotId,
  onChange,
}: {
  projectId: string;
  shotId: string;
  onChange?: () => void;
}) {
  const base = `/projects/${projectId}`,
    qc = useQueryClient(),
    { t: tr } = useI18n();
  const [query, setQuery] = useState(""),
    [type, setType] = useState("");
  const refs = useQuery({
    queryKey: ["shot-refs", shotId],
    queryFn: () =>
      api.get<ShotAssetRef[]>(`${base}/shots/${shotId}/asset-refs`),
  });
  const assets = useQuery({
    queryKey: ["assets-pick", projectId],
    queryFn: () => api.get<Asset[]>(`${base}/assets`),
  });
  const save = useMutation({
    mutationFn: (next: ShotAssetRef[]) =>
      api.put(`${base}/shots/${shotId}/asset-refs`, {
        refs: next.map((r) => ({
          asset_id: r.asset_id,
          role: r.role,
          ref_mode: r.ref_mode,
          pinned_version_id: r.pinned_version_id,
        })),
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["shot-refs", shotId] });
      onChange?.();
    },
  });
  const disabled =
    save.isPending ||
    refs.isFetching ||
    refs.isError ||
    assets.isPending ||
    assets.isError;
  function toggle(a: Asset) {
    if (disabled) return;
    const current = refs.data ?? [];
    save.mutate(
      current.some((r) => r.asset_id === a.id)
        ? current.filter((r) => r.asset_id !== a.id)
        : [
            ...current,
            {
              id: "",
              asset_id: a.id,
              role: a.type,
              ref_mode: "floating",
              pinned_version_id: null,
              ordinal: current.length,
            },
          ],
    );
  }
  const filtered = (assets.data ?? []).filter(
    (a) =>
      (!type || a.type === type) &&
      `${a.name} ${a.code} ${a.summary ?? ""}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  return (
    <div>
      <div className="reference-selected" aria-label="已选角色与场景">
        {refs.data?.map((r) => (
          <button
            key={r.asset_id}
            disabled={disabled}
            aria-label={`移除参考 ${assets.data?.find((a) => a.id === r.asset_id)?.name ?? r.asset_id}`}
            onClick={() =>
              save.mutate(
                (refs.data ?? []).filter((x) => x.asset_id !== r.asset_id),
              )
            }
          >
            {assets.data?.find((a) => a.id === r.asset_id)?.name ?? "读取参考…"}
            <X size={13} />
          </button>
        ))}
      </div>
      <div className="reference-tools">
        <input
          aria-label="搜索参考资产"
          placeholder="搜索角色、场景或编号…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          aria-label="参考资产分类"
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          <option value="">全部类型</option>
          {["character", "location", "prop", "costume", "vehicle", "style"].map(
            (t) => (
              <option key={t} value={t}>
                {tr(`asset.${t}`)}
              </option>
            ),
          )}
        </select>
      </div>
      {(refs.error || assets.error || save.error) && (
        <p role="alert" className="text-sm text-danger mb-3">
          {(refs.error || assets.error || save.error)?.message}
          <Button
            variant="ghost"
            onClick={() => {
              void refs.refetch();
              void assets.refetch();
              save.reset();
            }}
          >
            重新读取
          </Button>
        </p>
      )}
      {assets.isPending || refs.isPending ? (
        <p role="status">正在读取素材…</p>
      ) : (
        <div className="reference-catalog">
          {filtered.map((a) => {
            const selected = refs.data?.some((r) => r.asset_id === a.id);
            return (
              <button
                key={a.id}
                className="reference-card"
                aria-label={`参考资产 ${a.name} ${a.code}`}
                aria-pressed={!!selected}
                disabled={disabled}
                onClick={() => toggle(a)}
              >
                <div>
                  <MediaImage
                    src={
                      a.representative_blob_hash
                        ? blobUrl(projectId, a.representative_blob_hash)
                        : null
                    }
                    alt={a.name}
                  />
                </div>
                <strong>
                  {selected && <Check size={13} className="inline mr-1" />}
                  {a.name}
                </strong>
                <small>
                  {tr(`asset.${a.type}`)} · {a.code}
                </small>
              </button>
            );
          })}
        </div>
      )}
      {!assets.isPending && !assets.isError && !filtered.length && (
        <p className="p-8 text-center text-sm text-muted-foreground">
          {query || type
            ? "没有匹配的素材，试试其他关键词。"
            : "项目还没有角色或场景，可先到资产库创建。"}
        </p>
      )}
      <p role="status" className="mt-3 text-xs text-muted-foreground">
        {save.isPending
          ? "正在保存参考…"
          : `已选择 ${refs.data?.length ?? 0} 项 · 点击卡片添加或移除，自动保存`}
      </p>
    </div>
  );
}
