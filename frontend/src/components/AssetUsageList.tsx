import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { api, ASSET_TYPES } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useWorkspaceScroll } from "@/lib/useWorkspaceScroll";

type AssetUsage = {
  shot_id: string;
  shot_title?: string | null;
  shot_code: string;
  shot_ordinal?: number;
  scene_id?: string;
  scene_title?: string | null;
  scene_code?: string;
  scene_ordinal?: number;
  role: string;
  ref_mode: string;
  pinned_version_no?: number | null;
};

export function AssetUsageList({
  projectId,
  assetId,
  returnTo,
  limit,
}: {
  projectId: string;
  assetId: string;
  returnTo: string;
  limit?: number;
}) {
  const { t, lang } = useI18n();
  const zh = lang === "zh";
  const query = useQuery({
    queryKey: ["asset-usages", projectId, assetId],
    queryFn: () => api.get<AssetUsage[]>(`/projects/${projectId}/assets/${assetId}/usages`),
  });
  useWorkspaceScroll(`asset.usages.${projectId}.${assetId}`, !limit && query.isSuccess);
  if (query.isPending)
    return <p className="py-3 text-sm text-muted-foreground">{t("common.loading")}</p>;
  if (query.isError)
    return (
      <div role="alert" className="flex items-center gap-3 text-sm text-danger">
        {t("common.loadFailed")}
        <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
          {zh ? "重试" : "Retry"}
        </Button>
      </div>
    );
  if (!query.data.length)
    return (
      <p className="py-3 text-sm text-muted-foreground">
        {zh ? "还没有镜头引用此项。" : "No shots reference this item yet."}
      </p>
    );

  const byShot = new Map<string, AssetUsage[]>();
  for (const usage of query.data) {
    const refs = byShot.get(usage.shot_id);
    if (refs) refs.push(usage);
    else byShot.set(usage.shot_id, [usage]);
  }
  const shots = [...byShot.values()];
  return (
    <div>
      {!limit && (
        <h2 className="mb-3 text-sm font-medium">
          {zh ? `引用镜头（${shots.length}）` : `Referencing shots (${shots.length})`}
        </h2>
      )}
      <ul className="divide-y divide-border">
        {shots.slice(0, limit ?? shots.length).map((refs) => {
          const shot = refs[0];
          const sceneName =
            shot.scene_title?.trim() ||
            (shot.scene_ordinal != null
              ? zh
                ? `第 ${shot.scene_ordinal} 场`
                : `Scene ${shot.scene_ordinal}`
              : shot.scene_code || (zh ? "未命名场景" : "Untitled scene"));
          const shotName =
            shot.shot_title?.trim() ||
            (shot.shot_ordinal != null
              ? zh
                ? `镜头 ${shot.shot_ordinal}`
                : `Shot ${shot.shot_ordinal}`
              : shot.shot_code);
          const params = new URLSearchParams({ shot: shot.shot_id, tab: "refs", return: returnTo });
          return (
            <li key={shot.shot_id}>
              <Link
                to={`/projects/${projectId}/shots?${params}`}
                className="flex min-h-11 items-center justify-between gap-3 rounded-md px-2 py-3 hover:bg-elevated focus-visible:ring-2 focus-visible:ring-primary"
              >
                <span className="min-w-0 space-y-1">
                  <span className="block break-words text-xs text-muted-foreground">
                    {sceneName}
                  </span>
                  <span className="block break-words text-sm font-medium">{shotName}</span>
                  <span className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    {shot.shot_code !== shotName && <span>{shot.shot_code}</span>}
                    {refs.map((ref) => {
                      const role = ASSET_TYPES.some((type) => type === ref.role)
                        ? t(`asset.${ref.role}`)
                        : ref.role;
                      const mode =
                        ref.ref_mode === "pinned"
                          ? (zh ? "固定引用" : "Pinned reference") +
                            (ref.pinned_version_no ? ` v${ref.pinned_version_no}` : "")
                          : zh
                            ? "浮动引用"
                            : "Floating reference";
                      return (
                        <span key={ref.role}>
                          {role} · {mode}
                        </span>
                      );
                    })}
                  </span>
                </span>
                <ChevronRight size={16} aria-hidden className="shrink-0 text-muted-foreground" />
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
