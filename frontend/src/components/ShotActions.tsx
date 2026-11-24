import {
  useMutation,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import {
  Clapperboard,
  ClipboardCheck,
  Images,
  Pencil,
  Trash2,
} from "lucide-react";
import { Link } from "react-router-dom";
import { api, type Shot } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { useConfirm } from "@/components/ui/confirm";
import {
  ActionMenu,
  ActionMenuItem,
  ActionMenuSeparator,
} from "@/components/ui/header-menu";
import { useToast } from "@/components/ui/toast";

export function useCanEditShots(projectId: string) {
  const { me } = useAuth();
  return !!me?.memberships.some(
    (m) =>
      m.project_id === projectId &&
      ["admin", "director", "artist"].includes(m.role),
  );
}

export function shotLink(
  projectId: string,
  shotId: string,
  tab: string,
  source: "storyboard" | "status" = "storyboard",
) {
  const search = new URLSearchParams({ shot: shotId, tab });
  search.set(source === "status" ? "view" : "from", source);
  return `/projects/${projectId}/shots?${search}`;
}

export function ShotDestinations({
  projectId,
  shotId,
  showProduction = true,
}: {
  projectId: string;
  shotId: string;
  showProduction?: boolean;
}) {
  const zh = useI18n().lang === "zh";
  return (
    <div className="storyboard-shot-links">
      {showProduction && <Link to={shotLink(projectId, shotId, "gen")}>
        {zh ? "制作画面" : "Produce"}
      </Link>}
      <Link to={shotLink(projectId, shotId, "refs")}>
        {zh ? "参考素材" : "References"}
      </Link>
      <Link to={shotLink(projectId, shotId, "review")}>
        {zh ? "审阅镜头" : "Review shot"}
      </Link>
    </div>
  );
}

export function ShotActions({
  projectId,
  shot,
  source,
  canEdit,
  onRename,
  onRemove,
}: {
  projectId: string;
  shot: Shot;
  source?: "storyboard" | "status";
  canEdit: boolean;
  onRename: () => void;
  onRemove: () => void;
}) {
  const zh = useI18n().lang === "zh";
  return (
    <ActionMenu
      label={`${zh ? "更多镜头操作" : "More shot actions"} · ${shot.title || shot.code}`}
    >
      <ActionMenuItem asChild>
        <Link to={shotLink(projectId, shot.id, "overview", source)}>
          <Clapperboard />
          {zh ? "查看镜头" : "View shot"}
        </Link>
      </ActionMenuItem>
      <ActionMenuItem asChild>
        <Link to={shotLink(projectId, shot.id, "refs", source)}>
          <Images />
          {zh ? "参考素材" : "References"}
        </Link>
      </ActionMenuItem>
      <ActionMenuItem asChild>
        <Link to={shotLink(projectId, shot.id, "review", source)}>
          <ClipboardCheck />
          {zh ? "审阅镜头" : "Review shot"}
        </Link>
      </ActionMenuItem>
      {canEdit && (
        <>
          <ActionMenuSeparator />
          <ActionMenuItem onSelect={onRename}>
            <Pencil />
            {zh ? "重命名" : "Rename"}
          </ActionMenuItem>
          <ActionMenuItem danger onSelect={onRemove}>
            <Trash2 />
            {zh ? "移除镜头" : "Remove shot"}
          </ActionMenuItem>
        </>
      )}
    </ActionMenu>
  );
}

export function refreshShotLists(
  qc: QueryClient,
  projectId: string,
  sceneId: string,
) {
  const keys = [
    ["scene-shots", projectId, sceneId],
    ["removed-shots", projectId, sceneId],
    ["scene-visuals", `/projects/${projectId}`, sceneId],
    ["scenes-all", projectId],
    ["all-shots", projectId],
    ["board", projectId],
    ["assets", projectId],
    ["asset-usages", projectId],
    ["materials", projectId],
    ["library-item", projectId],
    ["material-generation", projectId],
    ["media-usages", projectId],
  ];
  return Promise.all(
    keys.map((queryKey) => qc.invalidateQueries({ queryKey })),
  );
}

export function useRemoveShots(
  projectId: string,
  onRemoved?: (sceneId: string) => void,
) {
  const zh = useI18n().lang === "zh";
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const removal = useMutation({
    mutationFn: ({ sceneId, ids }: { sceneId: string; ids: string[] }) =>
      api.post<{ changed: number }>(
        `/projects/${projectId}/scenes/${sceneId}/shots/remove`,
        { shot_ids: ids },
      ),
    onSuccess: async ({ changed }, { sceneId }) => {
      await refreshShotLists(qc, projectId, sceneId);
      onRemoved?.(sceneId);
      toast.push(
        zh
          ? `已移除 ${changed} 个镜头，可在本场「已移除镜头」中恢复`
          : `${changed} shots removed. Restore them from Removed shots in this scene.`,
        "success",
      );
    },
    onError: (error) => toast.push(error.message, "error"),
  });
  async function remove(shots: Shot[], sceneName: string) {
    if (!shots.length || removal.isPending) return;
    const names = shots
      .slice(0, 3)
      .map((s) => s.title || s.code)
      .join(zh ? "、" : ", ");
    const count = shots.length;
    const ok = await confirm({
      title: zh ? `移除 ${count} 个镜头？` : `Remove ${count} shot(s)?`,
      message: (
        <>
          <p>
            {zh
              ? `将从「${sceneName}」移除「${names}」${count > 3 ? "等镜头" : ""}。`
              : `Remove ${names}${count > 3 ? " and others" : ""} from ${sceneName}.`}
          </p>
          <p className="mt-2">
            {zh
              ? "已生成画面、素材引用和审阅记录都会保留。可在本场「已移除镜头」中恢复。"
              : "Outputs, references and reviews are kept. Restore these shots from Removed shots in this scene."}
          </p>
        </>
      ),
      danger: true,
      confirmText: zh ? `移除 ${count} 个镜头` : `Remove ${count} shot(s)`,
    });
    if (ok)
      removal.mutate({
        sceneId: shots[0].scene_id,
        ids: shots.map((s) => s.id),
      });
  }
  return { remove, busy: removal.isPending };
}
