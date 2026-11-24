import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Undo2 } from "lucide-react";
import { api, type SceneListItem, type Shot } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { refreshShotLists } from "@/components/ShotActions";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";

type RemovedShot = Pick<Shot, "id" | "title" | "code" | "ordinal"> & {
  deleted_at: string;
};

export function RemovedShotsDialog({
  projectId,
  scene,
  canEdit,
  onClose,
}: {
  projectId: string;
  scene: SceneListItem;
  canEdit: boolean;
  onClose: () => void;
}) {
  const zh = useI18n().lang === "zh";
  const qc = useQueryClient();
  const toast = useToast();
  const [selected, setSelected] = useState<string[]>([]);
  const path = `/projects/${projectId}/scenes/${scene.id}/shots`;
  const removed = useQuery({
    queryKey: ["removed-shots", projectId, scene.id],
    queryFn: () => api.get<RemovedShot[]>(path + "/removed"),
  });
  const restore = useMutation({
    mutationFn: (ids: string[]) =>
      api.post<{ changed: number }>(path + "/restore", { shot_ids: ids }),
    onSuccess: async ({ changed }, ids) => {
      await refreshShotLists(qc, projectId, scene.id);
      setSelected((current) => current.filter((id) => !ids.includes(id)));
      toast.push(
        zh ? `已恢复 ${changed} 个镜头` : `${changed} shots restored`,
        "success",
      );
    },
  });
  const rows = removed.data ?? [];
  const selectedIds = rows
    .filter((s) => selected.includes(s.id))
    .map((s) => s.id);
  return (
    <Modal
      open
      title={`${zh ? "已移除镜头" : "Removed shots"} · ${scene.title || scene.code}`}
      onClose={onClose}
      width={620}
    >
      {removed.isLoading && (
        <p role="status">{zh ? "正在读取镜头…" : "Loading shots…"}</p>
      )}
      {removed.isError && (
        <p role="alert" className="text-sm text-danger">
          {removed.error.message}{" "}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void removed.refetch()}
          >
            {zh ? "重试" : "Retry"}
          </Button>
        </p>
      )}
      {removed.isSuccess && !rows.length && (
        <p className="studio-empty">
          {zh ? "本场没有已移除的镜头" : "No removed shots in this scene"}
        </p>
      )}
      {!!rows.length && (
        <>
          <p className="mb-3 text-sm text-muted-foreground">
            {zh
              ? "恢复后可继续使用原有画面、引用与审阅记录。原位置已被占用的镜头会排到本场末尾。"
              : "Restored shots keep their outputs, references and reviews. A shot whose position is occupied moves to the end of the scene."}
          </p>
          {canEdit && (
            <div className="storyboard-selection-bar">
              <Button
                variant="ghost"
                size="sm"
                disabled={restore.isPending}
                onClick={() =>
                  setSelected(
                    selectedIds.length === rows.length
                      ? []
                      : rows.map((s) => s.id),
                  )
                }
              >
                {selectedIds.length === rows.length
                  ? zh
                    ? "清除选择"
                    : "Clear selection"
                  : zh
                    ? "选择本场"
                    : "Select scene"}
              </Button>
              <Button
                size="sm"
                disabled={!selectedIds.length || restore.isPending}
                onClick={() => restore.mutate(selectedIds)}
              >
                <Undo2 size={15} />
                {zh
                  ? `恢复所选（${selectedIds.length}）`
                  : `Restore selected (${selectedIds.length})`}
              </Button>
            </div>
          )}
          <ul className="divide-y divide-border">
            {rows.map((shot) => (
              <li
                className="flex min-w-0 items-center gap-2 py-2"
                key={shot.id}
              >
                <label className="flex min-w-0 flex-1 items-center gap-3 py-1 text-sm">
                  {canEdit && (
                    <input
                      type="checkbox"
                      aria-label={`${zh ? "选择" : "Select"} ${shot.title || shot.code}`}
                      checked={selectedIds.includes(shot.id)}
                      disabled={restore.isPending}
                      onChange={(e) =>
                        setSelected((ids) =>
                          e.target.checked
                            ? [...ids, shot.id]
                            : ids.filter((id) => id !== shot.id),
                        )
                      }
                    />
                  )}
                  <span className="min-w-0">
                    <strong className="block break-words font-medium">
                      {shot.title || shot.code}
                    </strong>
                    <small className="text-muted-foreground">
                      {shot.code} ·{" "}
                      {new Date(shot.deleted_at).toLocaleDateString(
                        zh ? "zh-CN" : "en-US",
                      )}
                    </small>
                  </span>
                </label>
                {canEdit && (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={restore.isPending}
                    aria-label={`${zh ? "恢复镜头" : "Restore shot"} ${shot.title || shot.code}`}
                    onClick={() => restore.mutate([shot.id])}
                  >
                    {zh ? "恢复" : "Restore"}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {restore.isError && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {restore.error.message}
        </p>
      )}
    </Modal>
  );
}
