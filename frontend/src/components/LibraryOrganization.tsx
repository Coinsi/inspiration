import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Folder, FolderPlus, Move, Pencil, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { useConfirm } from "@/components/ui/confirm";

type FolderItem = {
  id: string;
  name: string;
  parent_id: string | null;
  revision: number;
  media_count: number;
};
export function LibraryOrganization({
  projectId,
  value,
  onSelect,
  selection,
  onClear,
  onSelectPage,
  onChange,
}: {
  projectId: string;
  value: string;
  onSelect: (id: string) => void;
  selection: Record<string, string | null>;
  onClear: () => void;
  onSelectPage: () => void;
  onChange: () => void;
}) {
  const zh = useI18n().lang === "zh",
    base = `/projects/${projectId}/library`,
    qc = useQueryClient(),
    confirm = useConfirm();
  const folders = useQuery({
    queryKey: ["library-folders", projectId],
    queryFn: () => api.get<FolderItem[]>(base + "/folders"),
  });
  const [form, setForm] = useState<{
    mode: "create" | "edit" | "move";
    id?: string;
    revision?: number;
    name: string;
    parent: string;
  } | null>(null);
  const byId = new Map((folders.data ?? []).map((f) => [f.id, f]));
  const current = byId.get(value),
    count = Object.keys(selection).length;
  const path = (id: string) => {
    const names: string[] = [];
    let f = byId.get(id);
    for (let i = 0; f && i < 8; i++) {
      names.unshift(f.name);
      f = f.parent_id ? byId.get(f.parent_id) : undefined;
    }
    return names.join(" / ");
  };
  const parent = current?.id ?? null;
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["library-folders", projectId] });
    onChange();
  };
  const change = useMutation({
    mutationFn: async () => {
      if (!form) return;
      if (form.mode === "move") {
        await api.post(base + "/move", {
          items: Object.entries(selection).map(
            ([media_id, expected_folder_id]) => ({
              media_id,
              expected_folder_id,
            }),
          ),
          folder_id: form.parent || null,
        });
        onClear();
      } else {
        const body = { name: form.name, parent_id: form.parent || null };
        const result = form.id
          ? await api.put<FolderItem>(base + "/folders/" + form.id, {
              ...body,
              revision: form.revision,
            })
          : await api.post<FolderItem>(base + "/folders", body);
        onSelect(result.id);
      }
    },
    onSuccess: () => {
      setForm(null);
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (f: FolderItem) =>
      api.del(base + `/folders/${f.id}?revision=${f.revision}`),
    onSuccess: () => {
      onSelect(current?.parent_id ?? "root");
      refresh();
    },
  });
  const busy = change.isPending || remove.isPending;
  function open(next: NonNullable<typeof form>) {
    change.reset();
    remove.reset();
    setForm(next);
  }
  const options = (excluded?: string) =>
    folders.data
      ?.filter((f) => {
        let next: FolderItem | undefined = f;
        for (let i = 0; next && i < 8; i++) {
          if (next.id === excluded) return false;
          next = next.parent_id ? byId.get(next.parent_id) : undefined;
        }
        return true;
      })
      .map((f) => (
        <option key={f.id} value={f.id}>
          {path(f.id)}
        </option>
      ));
  return (
    <section
      aria-label={zh ? "素材目录" : "Video folders"}
      className="mb-4 space-y-3 rounded-xl border bg-card p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Folder className="h-4 w-4 shrink-0 text-primary" />
        <select
          aria-label={zh ? "当前素材目录" : "Current video folder"}
          className="min-w-0 max-w-full flex-1 rounded-lg border bg-background p-2 text-sm"
          value={value}
          disabled={folders.isPending || folders.isError || busy}
          onChange={(e) => onSelect(e.target.value)}
        >
          <option value="all">
            {zh ? "全部目录中的视频" : "Videos in all folders"}
          </option>
          <option value="root">{zh ? "未归档视频" : "Unfiled videos"}</option>
          {options()}
        </select>
        <Button
          size="sm"
          variant="outline"
          disabled={busy || folders.isPending || folders.isError}
          onClick={() =>
            open({ mode: "create", name: "", parent: parent ?? "" })
          }
        >
          <FolderPlus size={14} />
          {zh ? "新建目录" : "New folder"}
        </Button>
        {current && (
          <>
            <Button
              size="sm"
              variant="ghost"
              aria-label={zh ? "编辑当前目录" : "Edit current folder"}
              disabled={busy}
              onClick={() =>
                open({
                  mode: "edit",
                  id: current.id,
                  revision: current.revision,
                  name: current.name,
                  parent: current.parent_id ?? "",
                })
              }
            >
              <Pencil size={14} />
            </Button>
            <Button
              size="sm"
              variant="ghost"
              aria-label={zh ? "删除当前目录" : "Delete current folder"}
              disabled={busy}
              onClick={async () => {
                if (
                  await confirm({
                    message: zh
                      ? "只删除空目录，不会删除视频。确定继续？"
                      : "Delete this empty folder? Videos are never deleted by this action.",
                  })
                )
                  remove.mutate(current);
              }}
            >
              <Trash2 size={14} />
            </Button>
          </>
        )}
      </div>
      {folders.isError && (
        <div role="alert" className="text-sm text-danger">
          {zh ? "目录读取失败" : "Could not load folders"}{" "}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void folders.refetch()}
          >
            {zh ? "重试" : "Retry"}
          </Button>
        </div>
      )}
      {folders.data && !["all", "root"].includes(value) && !current && (
        <p role="alert" className="text-sm text-danger">
          {zh ? "此目录已不存在或无法访问。" : "This folder is unavailable."}
          <Button variant="ghost" size="sm" onClick={() => onSelect("all")}>
            {zh ? "返回全部目录" : "Show all folders"}
          </Button>
        </p>
      )}
      {folders.data && (
        <div className="flex max-h-32 flex-wrap gap-2 overflow-auto">
          {current && (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => onSelect(current.parent_id ?? "root")}
            >
              ↑ {zh ? "上一级" : "Parent"}
            </Button>
          )}
          {folders.data
            .filter((f) => f.parent_id === parent)
            .map((f) => (
              <Button
                key={f.id}
                size="sm"
                variant="outline"
                disabled={busy}
                className="max-w-full"
                onClick={() => onSelect(f.id)}
              >
                <Folder size={14} />
                <span className="max-w-48 truncate">{f.name}</span>
                <span className="text-muted-foreground">{f.media_count}</span>
              </Button>
            ))}
        </div>
      )}
      {current && folders.data?.some((f) => f.parent_id === current.id) && (
        <p className="text-xs text-muted-foreground">
          {zh
            ? "当前目录仅显示直接归档的视频，子目录请点击进入。"
            : "Showing videos directly in this folder. Open a child folder to browse its videos."}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2 border-t pt-2 text-xs">
        {!!count && (
          <span className="mr-auto text-muted-foreground" role="status">
            {zh
              ? `已选 ${count} 项 · 每次最多移动100项`
              : `${count} selected · Move up to 100 items`}
          </span>
        )}
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={onSelectPage}
        >
          {zh ? "选择本页" : "Select page"}
        </Button>
        {!!count && (
          <>
            <Button variant="ghost" size="sm" disabled={busy} onClick={onClear}>
              {zh ? "取消选择" : "Clear selection"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || folders.isError}
              onClick={() =>
                open({ mode: "move", name: "", parent: parent ?? "" })
              }
            >
              <Move size={14} />
              {zh ? "移动所选素材" : "Move selection"}
            </Button>
          </>
        )}
      </div>
      {remove.error && (
        <p role="alert" className="text-sm text-danger">
          {remove.error.message}
        </p>
      )}
      {form && (
        <Modal
          open
          title={
            form.mode === "move"
              ? zh
                ? "移动所选素材"
                : "Move selection"
              : form.id
                ? zh
                  ? "编辑素材目录"
                  : "Edit folder"
                : zh
                  ? "新建素材目录"
                  : "New folder"
          }
          onClose={() => {
            if (!busy) setForm(null);
          }}
          width={500}
        >
          <div className="space-y-4">
            {form.mode !== "move" && (
              <label className="block space-y-2 text-xs">
                <span>{zh ? "目录名称" : "Folder name"}</span>
                <input
                  aria-label={zh ? "目录名称" : "Folder name"}
                  className="w-full rounded-lg border bg-background p-2 text-sm"
                  maxLength={120}
                  value={form.name}
                  disabled={busy}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </label>
            )}
            <label className="block space-y-2 text-xs">
              <span>
                {form.mode === "move"
                  ? zh
                    ? "目标目录"
                    : "Destination folder"
                  : zh
                    ? "父目录"
                    : "Parent folder"}
              </span>
              <select
                aria-label={zh ? "目标目录" : "Destination folder"}
                className="w-full rounded-lg border bg-background p-2 text-sm"
                value={form.parent}
                disabled={busy}
                onChange={(e) => setForm({ ...form, parent: e.target.value })}
              >
                <option value="">
                  {form.mode === "move"
                    ? zh
                      ? "未归档视频"
                      : "Unfiled videos"
                    : zh
                      ? "顶层目录"
                      : "Top-level folder"}
                </option>
                {options(form.id)}
              </select>
            </label>
            {form.mode === "move" && (
              <p className="text-sm text-muted-foreground">
                {zh
                  ? `将 ${count} 项素材移动到目标目录，原片版本与已有引用保持不变。`
                  : `Move ${count} items while preserving versions and existing references.`}
              </p>
            )}
            {change.error && (
              <div role="alert" className="text-sm text-danger">
                {change.error.message}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    if (form.mode === "move") onClear();
                    setForm(null);
                    refresh();
                  }}
                >
                  {zh ? "关闭并刷新" : "Close and refresh"}
                </Button>
              </div>
            )}
            <div className="flex justify-end gap-2">
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => setForm(null)}
              >
                {zh ? "取消" : "Cancel"}
              </Button>
              <Button
                disabled={busy || (form.mode !== "move" && !form.name.trim())}
                onClick={() => change.mutate()}
              >
                {busy
                  ? zh
                    ? "保存中…"
                    : "Saving…"
                  : form.mode === "move"
                    ? zh
                      ? "确认移动"
                      : "Move"
                    : zh
                      ? "保存目录"
                      : "Save folder"}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
}
