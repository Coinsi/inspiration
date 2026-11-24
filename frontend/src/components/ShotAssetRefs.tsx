import { StudioSelect } from "@/components/ui/studio-select";
import { SearchField } from "@/components/ui/search-field";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { X, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { MediaImage } from "@/components/MediaImage";
import { aspectLabels, visualMetadata } from "@/components/AssetContinuity";
import {
  api,
  blobUrl,
  type Asset,
  type Shot,
  type ShotAssetRef,
} from "@/lib/api";
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
  const shot = useQuery({
    queryKey: ["shot-reference-subjects", projectId, shotId],
    queryFn: () => api.get<Shot>(base + "/shots/" + shotId),
  });
  type Subject = { name: string; type: string; evidence: string };
  const pending = Array.isArray(shot.data?.storyboard?.unresolved_subjects)
    ? (shot.data.storyboard.unresolved_subjects as Subject[])
    : [];
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
    mutationFn: (next: (ShotAssetRef & { resolves_subject?: string })[]) =>
      api.put(`${base}/shots/${shotId}/asset-refs`, {
        refs: next.map((r) => ({
          asset_id: r.asset_id,
          role: r.role,
          resolves_subject: r.resolves_subject,
          ref_mode: r.ref_mode,
          pinned_version_id: r.pinned_version_id,
          usage: r.usage,
        })),
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["shot-refs", shotId] });
      await shot.refetch();
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
      {!!refs.data?.length && (
        <details className="mb-3 rounded-lg border p-3 text-sm">
          <summary>镜头里用到了哪些设定</summary>
          {refs.data.map((ref) => {
            const asset = assets.data?.find((a) => a.id === ref.asset_id);
            if (!asset) return null;
            const usage = ref.usage ?? { aspects: [], look: "", evidence: "" };
            const change = (updates: Partial<ShotAssetRef>) =>
              save.mutate(
                (refs.data ?? []).map((r) =>
                  r.id === ref.id ? { ...r, ...updates } : r,
                ),
              );
            const looks = Object.keys(
              visualMetadata(asset.metadata).looks ?? {},
            );
            const aspects =
              asset.type === "character"
                ? ["face", "wardrobe", "appearance"]
                : asset.type === "location"
                  ? ["layout", "light", "appearance"]
                  : ["appearance"];
            return (
              <fieldset
                key={ref.id}
                disabled={disabled}
                className="my-3 space-y-2"
              >
                <legend className="font-medium">{asset.name}</legend>
                <div className="flex flex-wrap gap-3">
                  {aspects.map((k) => (
                    <label key={k}>
                      <input
                        type="checkbox"
                        checked={usage.aspects.includes(k)}
                        onChange={(e) =>
                          change({
                            usage: {
                              ...usage,
                              aspects: e.target.checked
                                ? [...usage.aspects, k]
                                : usage.aspects.filter((a) => a !== k),
                            },
                          })
                        }
                      />{" "}
                      {aspectLabels[k]}
                    </label>
                  ))}
                </div>
                {!usage.aspects.length && (
                  <small>当前跟随此资产的全部视觉设定</small>
                )}
                {(looks.length > 0 || usage.look) && (
                  <StudioSelect
                    aria-label="镜头造型"
                    value={usage.look}
                    onValueChange={(look) =>
                      change({ usage: { ...usage, look } })
                    }
                  >
                    <option value="">通用造型</option>
                    {[...new Set([...looks, usage.look].filter(Boolean))].map(
                      (n) => (
                        <option key={n} value={n}>
                          {n}
                        </option>
                      ),
                    )}
                  </StudioSelect>
                )}
                <label className="block">
                  <input
                    type="checkbox"
                    checked={ref.ref_mode === "pinned"}
                    disabled={!asset.current_version_id || disabled}
                    onChange={(e) =>
                      change({
                        ref_mode: e.target.checked ? "pinned" : "floating",
                        pinned_version_id: e.target.checked
                          ? asset.current_version_id
                          : null,
                      })
                    }
                  />{" "}
                  锁定当前版本
                </label>
                {usage.evidence && (
                  <p className="text-muted-foreground">{usage.evidence}</p>
                )}
              </fieldset>
            );
          })}
        </details>
      )}
      {pending.map((subject, i) => (
        <label key={i} className="my-2 block rounded-md border p-2 text-sm">
          待确认 · {subject.name}
          <span className="block text-muted-foreground">
            {subject.evidence}
          </span>
          <StudioSelect
            value=""
            disabled={disabled}
            aria-label={"为" + subject.name + "选择资产"}
            onValueChange={(id) => {
              const current = refs.data || [];
              const existing = current.find((r) => r.asset_id === id);
              const ref = existing || {
                id: "",
                asset_id: id,
                role: subject.type,
                ref_mode: "floating",
                pinned_version_id: null,
                ordinal: current.length,
              };
              save.mutate([
                ...current.filter((r) => r !== existing),
                { ...ref, resolves_subject: subject.name },
              ]);
            }}
          >
            <option value="">选择对应的资产</option>
            {assets.data
              ?.filter((a) => a.type === subject.type)
              .map((a) => (
                <option value={a.id} key={a.id}>
                  {a.name} · {a.code}
                </option>
              ))}
          </StudioSelect>
        </label>
      ))}
      <div className="reference-tools">
        <SearchField
          className="flex-1"
          label="搜索参考资产"
          placeholder="搜索角色、场景或编号…"
          value={query}
          onValueChange={(value) => setQuery(value)}
        />
        <StudioSelect
          aria-label="参考资产分类"
          value={type}
          onValueChange={(selectedValue) => setType(selectedValue)}
        >
          <option value="">全部类型</option>
          {["character", "location", "prop", "costume", "vehicle", "style"].map(
            (t) => (
              <option key={t} value={t}>
                {tr(`asset.${t}`)}
              </option>
            ),
          )}
        </StudioSelect>
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
