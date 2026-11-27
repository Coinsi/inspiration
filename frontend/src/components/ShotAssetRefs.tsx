import { StudioSelect } from "@/components/ui/studio-select";
import { SelectField } from "@/components/ui/select-field";
import { SearchField } from "@/components/ui/search-field";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { X, Check, Plus, MapPin, UserRound, Shapes } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useCanEditShots } from "@/components/ShotActions";
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
import { assetChoices } from "@/lib/assetChoices";

type NamedReference = ShotAssetRef & {
  asset_name?: string | null;
  asset_removed?: boolean;
};
type Subject = { name: string; type: string; evidence?: string };

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
  const canEdit = useCanEditShots(projectId);
  const shot = useQuery({
    queryKey: ["shot-reference-subjects", projectId, shotId],
    queryFn: () => api.get<Shot>(base + "/shots/" + shotId),
  });
  const pending = Array.isArray(shot.data?.storyboard?.unresolved_subjects)
    ? (shot.data.storyboard.unresolved_subjects as Subject[])
    : [];
  const refs = useQuery({
    queryKey: ["shot-refs", shotId],
    queryFn: () =>
      api.get<NamedReference[]>(`${base}/shots/${shotId}/asset-refs`),
  });
  const assets = useQuery({
    queryKey: ["assets-pick", projectId],
    queryFn: () => api.get<Asset[]>(`${base}/assets`),
  });
  const choices = useMemo(() => assetChoices(assets.data ?? []), [assets.data]);
  const byId = useMemo(
    () => new Map(choices.map((choice) => [choice.value, choice])),
    [choices],
  );
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
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["shot-refs", shotId] }),
        qc.invalidateQueries({ queryKey: ["shot-prompt", projectId, shotId] }),
        qc.invalidateQueries({ queryKey: ["assets", projectId] }),
        qc.invalidateQueries({ queryKey: ["asset-usages", projectId] }),
        qc.invalidateQueries({ queryKey: ["materials", projectId] }),
        shot.refetch(),
      ]);
      onChange?.();
    },
  });
  const disabled =
    !canEdit ||
    save.isPending ||
    refs.isFetching ||
    refs.isError ||
    assets.isPending ||
    assets.isError;
  function confirmSubject(subject: Subject, id: string) {
    if (disabled || shot.isFetching || shot.isError) return;
    const current = refs.data ?? [];
    const existing = current.find(
      (r) => r.asset_id === id && r.role === subject.type,
    );
    if (existing) {
      save.mutate(
        current.map((r) =>
          r === existing ? { ...r, resolves_subject: subject.name } : r,
        ),
      );
    } else {
      save.mutate([
        ...current,
        {
          id: "",
          asset_id: id,
          role: subject.type,
          ref_mode: "floating",
          pinned_version_id: null,
          ordinal: current.length,
          resolves_subject: subject.name,
        },
      ]);
    }
  }
  function addAsset(a: Asset) {
    const current = refs.data ?? [];
    if (disabled || current.some((r) => r.asset_id === a.id)) return;
    save.mutate([
      ...current,
      {
        id: "",
        asset_id: a.id,
        role: a.type,
        ref_mode: "floating",
        pinned_version_id: null,
        ordinal: current.length,
      },
    ]);
  }
  const filtered = choices.filter(
    ({ asset, label, description, keywords }) =>
      (!type || asset.type === type) &&
      `${label} ${description} ${keywords}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  return (
    <div>
      {!!refs.data?.length && (
        <h3 className="mb-2 text-sm font-semibold">
          已引用 · {refs.data.length}
        </h3>
      )}
      <div
        className="reference-selected empty:hidden"
        aria-label="已选角色与场景"
      >
        {refs.data?.map((ref) => {
          const name =
            byId.get(ref.asset_id)?.selectedLabel ||
            ref.asset_name ||
            (assets.isLoading ? "读取参考…" : "不可用的参考素材");
          const shared = refs.data?.some(
            (r) => r.asset_id === ref.asset_id && r.id !== ref.id,
          );
          const roleKey = `asset.${ref.role}`;
          const roleLabel = tr(roleKey);
          const label = shared
            ? `${name} · ${roleLabel === roleKey ? `用途 ${ref.ordinal + 1}` : roleLabel}`
            : name;
          return (
            <span key={ref.id} className="reference-chip">
              <span>
                {label}
                {ref.asset_removed && (
                  <small className="ml-1 text-muted-foreground">
                    已移入回收站
                  </small>
                )}
              </span>
              {canEdit && (
                <button
                  type="button"
                  disabled={disabled}
                  aria-label={`移除引用 ${label}`}
                  title={`移除引用 ${label}`}
                  onClick={() =>
                    save.mutate(
                      (refs.data ?? []).filter((r) => r.id !== ref.id),
                    )
                  }
                >
                  <X size={14} aria-hidden />
                </button>
              )}
            </span>
          );
        })}
      </div>
      {pending.length > 0 && (
        <section aria-label="待确认的引用" className="mb-4">
          <h3 className="mb-1 text-sm font-semibold">
            待确认的引用 · {pending.length}
          </h3>
          <ul className="divide-y">
            {pending.map((subject, index) => {
              const kind = tr(`asset.${subject.type}`);
              const options = choices.filter(
                ({ asset }) => asset.type === subject.type,
              );
              return (
                <li
                  key={`${subject.type}-${subject.name}-${index}`}
                  className="grid gap-2 py-3 sm:grid-cols-[minmax(0,26rem)_max-content] sm:items-center sm:gap-5"
                >
                  <div className="min-w-0">
                    <span className="font-medium">{subject.name}</span>
                    {subject.evidence && (
                      <p className="mt-1 break-words text-sm text-muted-foreground">
                        {subject.evidence}
                      </p>
                    )}
                  </div>
                  {canEdit &&
                    (assets.isSuccess && options.length === 0 ? (
                      <span className="shrink-0 text-sm text-muted-foreground">
                        项目中暂无可选的{kind}
                      </span>
                    ) : (
                      <SelectField
                        className="justify-self-start"
                        label={`为${subject.name}选择${kind}`}
                        placeholder={`选择${kind}`}
                        searchPlaceholder={`搜索${kind}名称或描述`}
                        value=""
                        searchable
                        menuWidth={320}
                        options={options}
                        disabled={disabled || shot.isFetching || shot.isError}
                        onChange={(id) => confirmSubject(subject, id)}
                      />
                    ))}
                </li>
              );
            })}
          </ul>
        </section>
      )}
      {!!refs.data?.length && (
        <details className="mb-3 rounded-lg border p-3 text-sm">
          <summary>镜头里用到了哪些设定</summary>
          {refs.data.map((ref) => {
            const choice = byId.get(ref.asset_id);
            if (!choice) return null;
            const { asset, selectedLabel } = choice;
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
                <legend className="font-medium">{selectedLabel}</legend>
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
      <h3 className="mt-4 text-sm font-semibold">项目素材</h3>
      <div className="reference-tools">
        <SearchField
          className="flex-1"
          label="搜索参考资产"
          placeholder="搜索名称、描述或标签…"
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
      {(refs.error || assets.error || shot.error || save.error) && (
        <p role="alert" className="text-sm text-danger mb-3">
          {(refs.error || assets.error || shot.error || save.error)?.message}
          <Button
            variant="ghost"
            onClick={() => {
              void refs.refetch();
              void assets.refetch();
              void shot.refetch();
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
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {filtered.map(({ asset: a, label, selectedLabel, description }) => {
            const selected = refs.data?.some((r) => r.asset_id === a.id);
            const Icon =
              a.type === "character"
                ? UserRound
                : a.type === "location"
                  ? MapPin
                  : Shapes;
            return (
              <Button
                type="button"
                variant="outline"
                key={a.id}
                className="h-auto min-w-0 justify-start gap-3 rounded-lg px-3 py-2.5 text-left aria-pressed:bg-elevated aria-pressed:disabled:opacity-100"
                aria-label={`参考素材 ${selectedLabel}`}
                aria-pressed={!!selected}
                disabled={disabled || !!selected}
                onClick={() => addAsset(a)}
              >
                <div
                  aria-hidden="true"
                  className="grid h-12 w-12 shrink-0 place-items-center overflow-hidden rounded-md bg-elevated text-muted-foreground"
                >
                  {a.representative_blob_hash ? (
                    <MediaImage
                      src={blobUrl(
                        projectId,
                        a.representative_blob_hash,
                        "thumbnail",
                      )}
                      alt=""
                      deferUntilVisible
                    />
                  ) : (
                    <Icon size={20} strokeWidth={1.5} />
                  )}
                </div>
                <span className="min-w-0 flex-1">
                  <strong className="block break-words text-sm font-medium">
                    {label}
                  </strong>
                  <span className="mt-0.5 line-clamp-2 break-words text-xs font-normal text-muted-foreground">
                    {[tr(`asset.${a.type}`), description]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </span>
                {selected ? (
                  <span className="flex shrink-0 items-center gap-1 text-xs">
                    <Check size={14} />
                    已引用
                  </span>
                ) : (
                  canEdit && (
                    <Plus
                      aria-hidden
                      size={16}
                      className="shrink-0 text-muted-foreground"
                    />
                  )
                )}
              </Button>
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
      <p
        role="status"
        className="mt-3 text-xs text-muted-foreground empty:hidden"
      >
        {save.isPending ? "正在保存参考…" : save.isSuccess ? "已保存引用" : ""}
      </p>
    </div>
  );
}
