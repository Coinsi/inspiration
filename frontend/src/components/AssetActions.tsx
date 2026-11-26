import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { FileText, Images, Trash2 } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ActionMenu, ActionMenuItem, ActionMenuSeparator } from "@/components/ui/header-menu";
import { useConfirm } from "@/components/ui/confirm";
import { useToast } from "@/components/ui/toast";
import { api, type Asset } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";

export function assetPath(projectId: string, assetId: string, returnTo: string, tab = "usages") {
  const params = new URLSearchParams();
  if (tab !== "usages") params.set("tab", tab);
  params.set("return", returnTo);
  return `/projects/${projectId}/assets/${assetId}?${params}`;
}

export function useAssetPermissions(projectId: string) {
  const role = useAuth().me?.memberships.find((m) => m.project_id === projectId)?.role;
  return {
    canEdit: role === "admin" || role === "director" || role === "artist",
    canLock: role === "admin" || role === "director",
  };
}

export function invalidateAssetLists(qc: QueryClient, projectId: string) {
  for (const key of ["assets", "asset-usages", "assets-pick", "materials", "trash", "workbench-references"]) {
    void qc.invalidateQueries({ queryKey: [key, projectId] });
  }
}

type TrashImpact = {
  eligible_ids: string[];
  shot_count: number;
  floating_shots: number;
  pinned_shots: number;
};

export function useAssetTrash(projectId: string, onTrashed: () => void) {
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const zh = useI18n().lang === "zh";
  const base = `/projects/${projectId}/assets`;
  const moveLabel = zh ? "移入回收站" : "Move to trash";
  const refreshDetails = (ids: string[], refetchType: "active" | "none") => {
    const affected = new Set(ids);
    void qc.invalidateQueries({
      predicate: query => query.queryKey[0] === "asset" && affected.has(String(query.queryKey[1])),
      refetchType,
    });
  };
  return useMutation({
    mutationFn: async (items: Asset[]) => {
      const ids = items.map((a) => a.id);
      const impact = await api.post<TrashImpact>(base + "/batch-delete/preview", { ids });
      const eligible = new Set(impact.eligible_ids);
      const names = items
        .filter((a) => eligible.has(a.id))
        .slice(0, 3)
        .map((a) => a.name);
      if (!eligible.size) {
        toast.push(
          zh ? "所选项已锁定或已移出列表。" : "These items are locked or no longer listed.",
          "info",
        );
        invalidateAssetLists(qc, projectId);
        refreshDetails(ids, "active");
        return;
      }
      const count = eligible.size;
      const skipped = ids.length - count;
      const ok = await confirm({
        title: zh ? `将 ${count} 项移入回收站？` : `Move ${count} items to trash?`,
        message: (
          <div className="space-y-2">
            <p className="break-words font-medium text-foreground">
              {names.join(zh ? "、" : ", ")}
              {count > names.length && "…"}
            </p>
            {impact.shot_count ? (
              <p>
                {zh
                  ? `涉及 ${impact.shot_count} 个引用镜头。`
                  : `Used in ${impact.shot_count} shots. `}
                {impact.floating_shots > 0 &&
                  (zh
                    ? `${impact.floating_shots} 个镜头使用最新设定，恢复素材或更换参考后才能再次生成。`
                    : `${impact.floating_shots} shots use floating references. Restore the item or replace the reference before generating again. `)}
                {impact.pinned_shots > 0 &&
                  (zh
                    ? `${impact.pinned_shots} 个镜头使用指定版本，仍可读取该版本。`
                    : `Pinned versions remain available in ${impact.pinned_shots} shots.`)}
              </p>
            ) : (
              <p>{zh ? "当前没有镜头引用所选项。" : "No shots currently reference these items."}</p>
            )}
            {skipped > 0 && (
              <p>
                {zh
                  ? `另有 ${skipped} 项已锁定或已移出列表，将跳过。`
                  : `${skipped} locked or unavailable items will be skipped.`}
              </p>
            )}
            <p>
              {zh
                ? "可在「素材 → 回收站」恢复。"
                : "You can restore these items from Materials → Trash."}
            </p>
          </div>
        ),
        confirmText: moveLabel,
        danger: true,
      });
      if (!ok) return;
      let deleted = 1,
        skippedAtSave = 0;
      if (ids.length === 1) {
        await api.del(`${base}/${ids[0]}`);
      } else {
        const result = await api.post<{ deleted: number; skipped: number }>(
          base + "/batch-delete",
          { ids: impact.eligible_ids },
        );
        deleted = result.deleted;
        skippedAtSave = result.skipped;
      }
      if (deleted)
        toast.push(
          zh ? `已将 ${deleted} 项移入回收站` : `Moved ${deleted} items to trash`,
          "success",
        );
      if (skippedAtSave)
        toast.push(
          zh
            ? `${skippedAtSave} 项状态已变更，已跳过。`
            : `Skipped ${skippedAtSave} items whose status changed.`,
          "info",
        );
      return impact.eligible_ids;
    },
    onSuccess: (ids) => {
      if (!ids) return;
      onTrashed();
      invalidateAssetLists(qc, projectId);
      refreshDetails(ids, "none");
      void qc.invalidateQueries({ queryKey: ["asset-usages", projectId], refetchType: "none" });
    },
    onError: (err, items) => {
      toast.push(err.message, "error");
      invalidateAssetLists(qc, projectId);
      refreshDetails(items.map(a => a.id), "active");
    },
  });
}

export function AssetActions({
  projectId,
  asset,
  returnTo,
  canTrash,
  onTrash,
  children,
}: {
  projectId: string;
  asset: Asset;
  returnTo: string;
  canTrash: boolean;
  onTrash: () => void;
  children?: ReactNode;
}) {
  const zh = useI18n().lang === "zh";
  return (
    <ActionMenu label={zh ? `${asset.name}的更多操作` : `More actions for ${asset.name}`}>
      {children ?? <>
      <ActionMenuItem asChild>
        <Link to={assetPath(projectId, asset.id, returnTo, "info")}>
          <FileText />
          {canTrash && asset.status !== "locked" ? (zh ? "编辑资料" : "Edit details") : (zh ? "查看资料" : "View details")}
        </Link>
      </ActionMenuItem>
      <ActionMenuItem asChild>
        <Link to={assetPath(projectId, asset.id, returnTo, "ref")}>
          <Images />
          {zh ? "查看参考图" : "View reference images"}
        </Link>
      </ActionMenuItem>
      </>}
      <ActionMenuSeparator />
      <ActionMenuItem disabled={!canTrash || asset.status === "locked"} danger onSelect={onTrash}>
        <Trash2 />
        {zh ? "移入回收站" : "Move to trash"}
      </ActionMenuItem>
    </ActionMenu>
  );
}
