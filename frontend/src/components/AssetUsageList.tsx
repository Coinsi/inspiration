import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { SceneLook } from "@/components/SceneMaterials";
import { Button } from "@/components/ui/button";
import { api, blobUrl, type Asset } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { sceneLabel } from "@/lib/scene-label";
import { useWorkspaceScroll } from "@/lib/useWorkspaceScroll";

type Member = { id: string; name: string; direction_excerpt: string; reference_hash: string | null };
type Shot = { id: string; title: string | null; ordinal: number; pinned_versions: number[] };
type Appearance = {
  scene_id: string;
  title: string | null;
  ordinal: number;
  locked: boolean;
  removed_from_script: boolean;
  novel_title: string | null;
  chapter_id: string | null;
  chapter_ordinal: number | null;
  chapter_title: string | null;
  script_id: string;
  script_title: string;
  members: Member[];
  shots: Shot[];
};

function storyLabel(scene: Appearance, zh: boolean) {
  const chapter =
    scene.chapter_title ||
    (scene.chapter_ordinal != null
      ? zh
        ? `第 ${scene.chapter_ordinal} 章`
        : `Chapter ${scene.chapter_ordinal}`
      : "");
  return [scene.novel_title, chapter].filter(Boolean).join(" · ");
}

export function AssetUsageList({
  projectId,
  asset,
  canEdit,
  returnTo,
  initialScene,
}: {
  projectId: string;
  asset: Asset;
  canEdit: boolean;
  returnTo: string;
  initialScene?: string | null;
}) {
  const { lang, t } = useI18n();
  const zh = lang === "zh";
  const [params] = useSearchParams();
  const [editing, setEditing] = useState<{ scene: Appearance; member: Member } | null>(null);
  const base = "/projects/" + projectId;
  const query = useQuery({
    queryKey: ["asset-usages", projectId, asset.id, "appearances"],
    queryFn: () => api.get<Appearance[]>(base + "/assets/" + asset.id + "/appearances"),
  });
  useWorkspaceScroll(`asset.usages.${projectId}.${asset.id}`, query.isSuccess);
  if (query.isPending)
    return (
      <p role="status" className="py-4 text-sm text-muted-foreground">
        {t("common.loading")}
      </p>
    );
  if (query.isError)
    return (
      <div role="alert" className="flex items-center gap-2 text-sm">
        {zh ? "场次读取失败。" : "Could not load scenes."}
        <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
          {zh ? "重试" : "Retry"}
        </Button>
      </div>
    );
  if (!query.data.length)
    return (
      <p className="py-4 text-sm text-muted-foreground">
        {asset.type === "character"
          ? zh
            ? "这个角色还没有关联场次或镜头。"
            : "This character has no linked scenes or shots yet."
          : zh
            ? "这项素材还没有关联场次或镜头。"
            : "This asset has no linked scenes or shots yet."}
      </p>
    );

  const scripts = new Map<string, Appearance[]>();
  for (const scene of query.data) {
    const key = scene.script_id + "/" + (scene.chapter_id || "");
    const group = scripts.get(key);
    if (group) group.push(scene);
    else scripts.set(key, [scene]);
  }
  const selectedScene = params.get("scene") || initialScene;
  function shotPath(scene: Appearance, shot: Shot) {
    const back = new URL(returnTo, window.location.origin);
    back.searchParams.set("scene", scene.scene_id);
    const search = new URLSearchParams({
      shot: shot.id,
      tab: "refs",
      return: back.pathname + back.search,
    });
    return base + "/shots?" + search;
  }
  return (
    <div className="space-y-7">
      {Array.from(scripts, ([id, scenes]) => {
        const first = scenes[0];
        return (
          <section key={id} aria-label={first.script_title}>
            <header className="mb-2">
              {storyLabel(first, zh) && (
                <p className="mb-1 text-xs text-muted-foreground">{storyLabel(first, zh)}</p>
              )}
              <h2 className="break-words text-base font-semibold">
                {first.script_title || (zh ? "未命名剧本" : "Untitled script")}
              </h2>
            </header>
            <ul className="divide-y divide-border border-y">
              {scenes.map((scene) => (
                <li key={scene.scene_id} className="py-3">
                  <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
                    <h3 className="min-w-0 break-words text-sm font-medium">{sceneLabel(scene, zh)}</h3>
                    {scene.members.map((m) => (
                      <Button
                        key={m.id}
                        variant="outline"
                        size="sm"
                        onClick={() => setEditing({ scene, member: m })}
                      >
                        {scene.members.length > 1 && m.name + " · "}
                        {zh ? "本场形象" : "Scene appearance"}
                      </Button>
                    ))}
                  </div>
                  <div className="mt-1 text-xs leading-5 text-muted-foreground">
                    {scene.removed_from_script
                      ? zh
                        ? "剧本已移除此场，保留已有分镜"
                        : "Removed from the script. Existing shots are retained."
                      : !scene.members.length
                        ? zh
                          ? "仅在镜头中引用"
                          : "Referenced directly by shots"
                        : scene.members
                            .filter((m) => m.direction_excerpt || m.reference_hash)
                            .map((m) => (
                              <p key={m.id} className="flex items-center gap-2">
                                {m.reference_hash && (
                                  <img
                                    className="size-8 shrink-0 rounded object-contain"
                                    loading="lazy"
                                    alt={zh ? "本场参考图" : "Scene reference"}
                                    src={blobUrl(projectId, m.reference_hash, "thumbnail")}
                                  />
                                )}
                                <span className="line-clamp-2 break-words">
                                  {scene.members.length > 1 && m.name + " · "}
                                  {m.direction_excerpt ||
                                    (zh ? "使用本场参考图" : "Scene-specific reference image")}
                                </span>
                              </p>
                            ))}
                  </div>
                  {scene.shots.length ? (
                    <details open={scene.scene_id === selectedScene || undefined} className="mt-1">
                      <summary className="w-fit cursor-pointer rounded py-1.5 text-sm text-muted-foreground hover:text-foreground">
                        {zh
                          ? `${scene.shots.length} 个引用镜头`
                          : `Referenced by ${scene.shots.length} shots`}
                      </summary>
                      <ul className="mt-1 border-l pl-3">
                        {scene.shots.map((shot) => {
                          const ordinal = zh ? "镜头 " + shot.ordinal : "Shot " + shot.ordinal;
                          return (
                            <li key={shot.id}>
                              <Link
                                to={shotPath(scene, shot)}
                                className="flex min-h-11 items-center gap-2 rounded-md px-2 text-sm hover:bg-elevated focus-visible:ring-2 focus-visible:ring-primary"
                              >
                                <span className="shrink-0 text-muted-foreground">{ordinal}</span>
                                {shot.title && shot.title !== ordinal && (
                                  <span className="min-w-0 break-words">{shot.title}</span>
                                )}
                                {!!shot.pinned_versions.length && (
                                  <span className="text-xs text-muted-foreground">
                                    {zh
                                      ? `使用第 ${shot.pinned_versions.join("、")} 版`
                                      : `Pinned to v${shot.pinned_versions.join(", ")}`}
                                  </span>
                                )}
                                <ChevronRight
                                  size={15}
                                  className="ml-auto shrink-0 text-muted-foreground"
                                  aria-hidden
                                />
                              </Link>
                            </li>
                          );
                        })}
                      </ul>
                    </details>
                  ) : (
                    <p className="mt-1 text-xs text-muted-foreground">
                      {zh ? "尚无引用镜头" : "No shots use this asset yet"}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </section>
        );
      })}
      {editing && (
        <SceneLook
          projectId={projectId}
          sceneId={editing.scene.scene_id}
          memberId={editing.member.id}
          memberName={editing.member.name}
          appearance={{
            sceneLabel: [editing.scene.script_title, sceneLabel(editing.scene, zh)]
              .filter(Boolean)
              .join(" · "),
            assetId: asset.id,
            cover: asset.representative_blob_hash,
          }}
          canEdit={canEdit && !editing.scene.locked}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}
