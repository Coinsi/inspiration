import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";
import { assetPath, invalidateAssetLists } from "@/components/AssetActions";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { SelectField } from "@/components/ui/select-field";
import { useToast } from "@/components/ui/toast";
import {
  api,
  blobUrl,
  type Asset,
  type RefImage,
  type SceneListItem,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";

const fields = ["novel_id", "chapter_id", "script_id", "scene_id"] as const;
type Field = (typeof fields)[number];
type Option = { value: string; label: string };

export function storyScope(params: URLSearchParams) {
  const result = new URLSearchParams();
  for (const field of fields) {
    const value = params.get(field);
    if (value) result.set(field, value);
  }
  return result;
}

type ElementUse = {
  id: string;
  kind: string;
  name: string;
  asset_id: string | null;
  evidence: string;
  prompt: string;
  direction: string;
  reference_hash: string | null;
};

export function SceneMaterials({
  projectId,
  scope,
  onChange,
  canEdit,
}: {
  projectId: string;
  scope: URLSearchParams;
  onChange: (next: URLSearchParams) => void;
  canEdit: boolean;
}) {
  const { lang, t } = useI18n();
  const zh = lang === "zh";
  const [expanded, setExpanded] = useState(!!scope.size);
  const [editing, setEditing] = useState<ElementUse | null>(null);
  const base = "/projects/" + projectId;
  const scenes = useQuery({
    queryKey: ["scenes-all", projectId, "", ""],
    queryFn: () => api.get<SceneListItem[]>(base + "/scenes"),
    enabled: expanded || !!scope.size,
  });
  const sceneId = scope.get("scene_id");
  const roster = useQuery({
    queryKey: ["scene-elements", projectId, sceneId],
    queryFn: () =>
      api.get<ElementUse[]>(base + "/scenes/" + sceneId + "/elements"),
    enabled: !!sceneId,
  });
  const rows = (scenes.data || []).filter((s) => !s.removed_from_script);
  const labels = zh
    ? ["小说", "章节", "剧本", "场次"]
    : ["Novel", "Chapter", "Script", "Scene"];
  function option(s: SceneListItem, field: Field): Option | null {
    if (field === "scene_id")
      return {
        value: s.id,
        label: String(s.ordinal) + " · " + (s.title || s.code),
      };
    const id = s[field];
    if (!id) return null;
    if (field === "chapter_id")
      return {
        value: id,
        label: s.chapter_title || labels[1] + " " + s.chapter_ordinal,
      };
    return {
      value: id,
      label: (field === "novel_id" ? s.novel_title : s.script_title) || id,
    };
  }
  function choices(index: number) {
    const options = new Map<string, Option>();
    for (const s of rows) {
      if (
        fields
          .slice(0, index)
          .some((f) => scope.has(f) && scope.get(f) !== option(s, f)?.value)
      )
        continue;
      const item = option(s, fields[index]);
      if (item) options.set(item.value, item);
    }
    return Array.from(options.values());
  }
  function choose(index: number, value: string) {
    const next = new URLSearchParams(scope);
    for (const field of fields.slice(index)) next.delete(field);
    if (value) next.set(fields[index], value);
    setEditing(null);
    onChange(next);
  }
  const active = rows.find((s) => s.id === sceneId);
  return (
    <section className="space-y-3 rounded-lg border bg-card px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "▾ " : "▸ "}
          {zh ? "按故事筛选" : "Filter by story"}
          {active ? " · " + (active.title || active.code) : ""}
        </Button>
        {!!scope.size && (
          <Button size="sm" variant="ghost" onClick={() => choose(0, "")}>
            {zh ? "清除故事筛选" : "Clear story filters"}
          </Button>
        )}
      </div>
      {expanded && (
        <div className="grid gap-2 pb-1 sm:grid-cols-2 xl:grid-cols-4">
          {fields.map((field, index) => (
            <SelectField
              key={field}
              label={labels[index]}
              searchable
              value={scope.get(field) || ""}
              onChange={(v) => choose(index, v)}
              options={[
                { value: "", label: (zh ? "全部" : "All ") + labels[index] },
                ...choices(index),
              ]}
            />
          ))}
          {scenes.isError && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void scenes.refetch()}
            >
              {zh ? "重新读取场次" : "Retry scenes"}
            </Button>
          )}
        </div>
      )}
      {sceneId && (
        <div className="space-y-2 border-t py-2">
          <h2 className="text-sm font-semibold">
            {zh ? "本场出场素材" : "Materials in this scene"}
          </h2>
          {roster.isError ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void roster.refetch()}
            >
              {zh ? "重新读取本场素材" : "Retry materials"}
            </Button>
          ) : roster.isPending ? (
            <p className="text-sm text-muted-foreground">
              {zh ? "正在读取本场素材…" : "Loading scene materials…"}
            </p>
          ) : (
            !roster.data?.length && (
              <p className="text-sm text-muted-foreground">
                {zh
                  ? "这场戏还没有提取到出场素材。"
                  : "No materials have been identified for this scene yet."}
              </p>
            )
          )}
          <div className="flex flex-wrap gap-2">
            {roster.data?.map((e) => (
              <Button
                key={e.id}
                size="sm"
                variant="outline"
                onClick={() => setEditing(e)}
              >
                {e.name}
                <span className="text-xs text-muted-foreground">
                  {t("asset." + e.kind)}
                  {!e.asset_id && (zh ? " · 待关联" : " · Unlinked")}
                </span>
              </Button>
            ))}
          </div>
        </div>
      )}
      {editing && sceneId && (
        <SceneLook
          key={editing.id}
          projectId={projectId}
          sceneId={sceneId}
          member={editing}
          canEdit={canEdit}
          onClose={() => setEditing(null)}
        />
      )}
    </section>
  );
}

function SceneLook({
  projectId,
  sceneId,
  member,
  canEdit,
  onClose,
}: {
  projectId: string;
  sceneId: string;
  member: ElementUse;
  canEdit: boolean;
  onClose: () => void;
}) {
  const zh = useI18n().lang === "zh";
  const cache = useQueryClient();
  const location = useLocation();
  const toast = useToast();
  const base = "/projects/" + projectId;
  const path = base + "/scenes/" + sceneId + "/elements/" + member.id;
  const [assetId, setAssetId] = useState(member.asset_id || "");
  const [reference, setReference] = useState(member.reference_hash || "");
  const [direction, setDirection] = useState(member.direction || member.prompt);
  const localDirection =
    direction.trim() === member.prompt.trim() ? "" : direction.trim();
  const assets = useQuery({
    queryKey: ["assets", projectId, member.kind, "", ""],
    queryFn: () => api.get<Asset[]>(base + "/assets?type=" + member.kind),
    enabled: canEdit,
  });
  const images = useQuery({
    queryKey: ["scene-asset-images", projectId, assetId],
    queryFn: () =>
      api.get<RefImage[]>(base + "/assets/" + assetId + "/reference-images"),
    enabled: !!assetId,
  });
  const cover = assets.data?.find(
    (a) => a.id === assetId,
  )?.representative_blob_hash;
  const hashes = [
    ...new Set(
      [cover, reference, ...(images.data || []).map((i) => i.blob_hash)].filter(
        (v): v is string => !!v,
      ),
    ),
  ];
  const save = useMutation({
    mutationFn: (adopt: boolean) =>
      adopt
        ? api.post<ElementUse>(path + "/asset", { direction: localDirection })
        : api.patch<ElementUse>(path, {
            ...(assetId !== (member.asset_id || "")
              ? { asset_id: assetId || null }
              : {}),
            reference_hash: reference || null,
            direction: localDirection,
          }),
    onSuccess: () => {
      invalidateAssetLists(cache, projectId);
      for (const key of [
        "scene-elements",
        "all-shots",
        "scene-shots",
        "compose",
        "shot-refs",
        "shot-generations",
      ]) {
        void cache.invalidateQueries({ queryKey: [key] });
      }
      onClose();
    },
    onError: (error) => toast.push(error.message, "error"),
  });
  return (
    <Modal open onClose={onClose} title={member.name} width={540}>
      <div className="space-y-4">
        <blockquote className="border-l-2 pl-3 text-sm text-muted-foreground">
          {member.evidence}
        </blockquote>
        {canEdit && (
          <label className="block space-y-1 text-sm">
            <span>{zh ? "关联的项目素材" : "Linked project asset"}</span>
            <SelectField
              label={zh ? "关联的项目素材" : "Linked project asset"}
              searchable
              value={assetId}
              options={[
                { value: "", label: zh ? "尚未关联" : "Unlinked" },
                ...(assets.data || []).map((a) => ({
                  value: a.id,
                  label: a.name,
                })),
              ]}
              onChange={(v) => {
                setAssetId(v);
                setReference("");
              }}
            />
          </label>
        )}
        {assetId && (
          <Link
            className="studio-link text-sm"
            to={assetPath(
              projectId,
              assetId,
              location.pathname + location.search,
              "ref",
            )}
          >
            {zh ? "查看素材与参考图" : "Open asset and reference images"}
          </Link>
        )}
        {!member.asset_id && canEdit && !assetId && (
          <Button
            variant="outline"
            size="sm"
            disabled={save.isPending}
            onClick={() => save.mutate(true)}
          >
            {zh ? "用本场描述建立或关联素材" : "Create or link from this scene"}
          </Button>
        )}
        <label className="block space-y-1 text-sm">
          <span>{zh ? "本场画面描述" : "Appearance in this scene"}</span>
          <textarea
            className="w-full rounded-md border bg-background p-2"
            rows={3}
            readOnly={!canEdit}
            maxLength={6000}
            placeholder={member.prompt}
            value={direction}
            onChange={(e) => setDirection(e.target.value)}
          />
          {!direction && member.prompt && (
            <span className="sr-only">{member.prompt}</span>
          )}
        </label>
        {assetId && (
          <div className="space-y-1 text-sm">
            <p>{zh ? "本场参考图" : "Scene reference image"}</p>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant={reference ? "outline" : "default"}
                size="sm"
                aria-pressed={!reference}
                disabled={!canEdit}
                onClick={() => setReference("")}
              >
                {zh ? "沿用素材默认" : "Use asset default"}
              </Button>
              {hashes.map((hash, i) => (
                <button
                  key={hash}
                  type="button"
                  disabled={!canEdit}
                  aria-pressed={reference === hash}
                  aria-label={(zh ? "参考图 " : "Reference ") + (i + 1)}
                  onClick={() => setReference(hash)}
                  className="overflow-hidden rounded border p-0.5 aria-pressed:ring-2 aria-pressed:ring-primary"
                >
                  <img
                    loading="lazy"
                    className="size-14 object-contain"
                    alt=""
                    src={blobUrl(projectId, hash, "thumbnail")}
                  />
                </button>
              ))}
            </div>
          </div>
        )}
        {canEdit && (
          <div className="flex justify-end">
            <Button
              disabled={save.isPending}
              onClick={() => save.mutate(false)}
            >
              {zh ? "保存本场设置" : "Save scene settings"}
            </Button>
          </div>
        )}
      </div>
    </Modal>
  );
}
