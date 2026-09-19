import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Trash2 } from "lucide-react";
import { api, type Project } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Modal } from "./ui/modal";
import { Button } from "./ui/button";

export function ProjectTrashDialog({
  onClose,
  onRestored,
}: {
  onClose: () => void;
  onRestored: () => void;
}) {
  const zh = useI18n().lang === "zh",
    qc = useQueryClient();
  const items = useQuery({
    queryKey: ["project-trash"],
    queryFn: () => api.get<Project[]>("/projects?deleted=true"),
  });
  const [busy, setBusy] = useState(""),
    [error, setError] = useState("");
  const pending = useRef(false);
  async function restore(p: Project) {
    if (pending.current) return;
    pending.current = true;
    setBusy(p.id);
    setError("");
    try {
      await api.post(`/projects/${p.id}/restore`);
      qc.setQueryData<Project[]>(["project-trash"], (old) =>
        old?.filter((x) => x.id !== p.id),
      );
      void items.refetch();
      onRestored();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      pending.current = false;
      setBusy("");
    }
  }
  return (
    <Modal
      open
      title={zh ? "项目回收站" : "Project trash"}
      width={640}
      onClose={() => {
        if (!pending.current) onClose();
      }}
    >
      <p className="text-sm text-muted-foreground mb-5">
        {zh
          ? "删除的项目会保留内容和素材。恢复后，原来的项目地址和成员权限继续有效。"
          : "Deleted projects keep their content and media. Restoring also restores their original links and member access."}
      </p>
      {items.isPending ? (
        <p role="status">{zh ? "正在读取…" : "Loading…"}</p>
      ) : items.isError ? (
        <div role="alert">
          {zh ? "回收站读取失败" : "Unable to load trash"}
          <Button onClick={() => void items.refetch()}>
            {zh ? "重试" : "Retry"}
          </Button>
        </div>
      ) : !items.data?.length ? (
        <div className="studio-empty min-h-40">
          <Trash2 size={28} />
          <p>{zh ? "回收站是空的" : "Trash is empty"}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.data.map((p) => (
            <div
              key={p.id}
              data-trash-project-id={p.id}
              className="flex items-center gap-3 rounded-xl border p-4"
            >
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium break-words">{p.name}</p>
                <p className="text-xs text-muted-foreground mt-1">
                  {zh ? "删除于 " : "Deleted "}
                  {p.deleted_at
                    ? new Date(p.deleted_at).toLocaleString(
                        zh ? "zh-CN" : "en-US",
                      )
                    : "—"}
                </p>
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={!!busy}
                onClick={() => void restore(p)}
              >
                <RotateCcw size={14} />
                {busy === p.id
                  ? zh
                    ? "恢复中…"
                    : "Restoring…"
                  : zh
                    ? "恢复"
                    : "Restore"}
              </Button>
            </div>
          ))}
        </div>
      )}
      {error && (
        <p role="alert" className="text-danger text-sm mt-4">
          {error}
        </p>
      )}
    </Modal>
  );
}
