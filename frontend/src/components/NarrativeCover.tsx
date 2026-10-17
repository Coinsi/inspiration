import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  FileText,
  ImagePlus,
  MoreHorizontal,
  Pencil,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { api, apiUpload, blobUrl } from "@/lib/api";
import { Button } from "./ui/button";
import { Modal } from "./ui/modal";
import { useToast } from "./ui/toast";
import "./narrative-cover.css";

type CoverProps = {
  projectId: string;
  kind: "novel" | "script";
  title: string;
  hash?: string | null;
};

export function NarrativeCover({ projectId, kind, title, hash }: CoverProps) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [hash]);
  return (
    <div className={`narrative-cover-art ${kind}`}>
      {hash && !failed ? (
        <img
          src={blobUrl(projectId, hash)}
          alt={`${title} · 封面`}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      ) : (
        <div className="narrative-cover-type">
          <span>{kind === "novel" ? "故事" : "剧本"}</span>
          {kind === "novel" ? (
            <BookOpen size={40} strokeWidth={1} />
          ) : (
            <FileText size={40} strokeWidth={1} />
          )}
          {failed && <small>封面暂时无法加载</small>}
        </div>
      )}
    </div>
  );
}

export function NarrativeCoverEditor({
  projectId,
  kind,
  id,
  title,
  hash,
  actions,
}: CoverProps & {
  id: string;
  actions?: { rename: () => void; remove: () => void; removing: boolean };
}) {
  const [open, setOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const dismiss = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [menuOpen]);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [remove, setRemove] = useState(false);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const toast = useToast();
  useEffect(() => {
    if (!file) {
      setPreview("");
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  const save = useMutation({
    mutationFn: () => {
      const url = `/projects/${projectId}/${kind === "novel" ? "novels" : "scripts"}/${id}/cover`;
      if (remove) return api.del(url);
      const data = new FormData();
      data.append("file", file!);
      return apiUpload(url, data);
    },
    onSuccess: async () => {
      await Promise.all(
        [
          "novels",
          "novels-trash",
          "novel",
          "scripts",
          "script",
          "material-shot-context",
          "conversation-object-catalog",
        ].map((key) => qc.invalidateQueries({ queryKey: [key, projectId] })),
      );
      await qc.invalidateQueries({ queryKey: ["script", id] });
      toast.push(remove ? "已移除封面" : "封面已保存", "success");
      setOpen(false);
      setFile(null);
    },
  });
  const pick = (next?: File) => {
    if (!next || save.isPending) return;
    setError("");
    save.reset();
    if (!["image/jpeg", "image/png", "image/webp"].includes(next.type)) {
      setError("请选择 JPG、PNG 或 WebP 静态图片");
      return;
    }
    if (next.size > 10 * 1024 * 1024 || !next.size) {
      setError("请选择不超过 10 MB 的图片");
      return;
    }
    setReady(false);
    setRemove(false);
    setFile(next);
  };
  return (
    <>
      <div
        className="narrative-actions"
        ref={menuRef}
        onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node))
            setMenuOpen(false);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.stopPropagation();
            setMenuOpen(false);
            menuRef.current
              ?.querySelector<HTMLButtonElement>(".narrative-more")
              ?.focus();
          }
          if (
            ["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key) &&
            menuOpen
          ) {
            e.preventDefault();
            const items = Array.from(
              menuRef.current?.querySelectorAll<HTMLButtonElement>(
                '[role="menuitem"]:not(:disabled)',
              ) ?? [],
            );
            const index = items.indexOf(
              document.activeElement as HTMLButtonElement,
            );
            items[
              e.key === "Home"
                ? 0
                : e.key === "End"
                  ? items.length - 1
                  : (index + (e.key === "ArrowUp" ? -1 : 1) + items.length) %
                    items.length
            ]?.focus();
          }
        }}
      >
        {actions && (
          <button
            type="button"
            className="narrative-more"
            aria-label={`更多操作：${title}`}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(!menuOpen)}
          >
            <MoreHorizontal size={19} />
          </button>
        )}
        {(!actions || menuOpen) && (
          <div
            className={actions ? "narrative-action-menu" : undefined}
            role={actions ? "menu" : undefined}
            aria-label={actions ? `${title}的操作` : undefined}
          >
            <button
              type="button"
              className="narrative-cover-edit"
              aria-label={`设置封面：${title}`}
              role={actions ? "menuitem" : undefined}
              onClick={() => {
                setMenuOpen(false);
                setFile(null);
                setRemove(false);
                setError("");
                save.reset();
                setOpen(true);
              }}
            >
              <ImagePlus size={14} />
              {hash ? "更换封面" : "添加封面"}
            </button>
            {actions && (
              <>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    actions.rename();
                  }}
                >
                  <Pencil size={14} />
                  重命名
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="narrative-action-danger"
                  disabled={actions.removing}
                  onClick={() => {
                    setMenuOpen(false);
                    actions.remove();
                  }}
                >
                  <Trash2 size={14} />
                  {kind === "novel" ? "移入回收站" : "删除剧本"}
                </button>
              </>
            )}
          </div>
        )}
      </div>
      <Modal
        open={open}
        onClose={() => {
          if (!save.isPending) {
            setOpen(false);
            setFile(null);
          }
        }}
        title={`${hash ? "更换" : "添加"}${kind === "novel" ? "故事" : "剧本"}封面`}
        width={650}
      >
        <div className="narrative-cover-dialog">
          <p>{title}</p>
          <div
            className="narrative-cover-drop"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              pick(e.dataTransfer.files[0]);
            }}
          >
            {preview && !remove ? (
              <img
                className="narrative-cover-preview"
                src={preview}
                alt="待保存封面预览"
                onLoad={() => setReady(true)}
                onError={() => {
                  setReady(false);
                  setError("图片无法读取，请重新选择");
                }}
              />
            ) : hash && !remove ? (
              <NarrativeCover
                projectId={projectId}
                kind={kind}
                title={title}
                hash={hash}
              />
            ) : (
              <div className="narrative-cover-upload-empty">
                <ImagePlus size={30} strokeWidth={1.2} />
                <strong>
                  {remove
                    ? "保存后恢复文字封面"
                    : `为这个${kind === "novel" ? "故事" : "剧本"}选一张封面`}
                </strong>
                <span>拖入图片，或从电脑选择</span>
              </div>
            )}
          </div>
          <input
            ref={input}
            className="sr-only"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            aria-label="选择封面图片"
            disabled={save.isPending}
            onChange={(e) => {
              pick(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
          <div className="narrative-cover-file-tools">
            <Button
              variant="outline"
              disabled={save.isPending}
              onClick={() => input.current?.click()}
            >
              <Upload size={15} />
              {file || hash ? "选择其他图片" : "选择图片"}
            </Button>
            {(file || hash) && !remove && (
              <Button
                variant="ghost"
                disabled={save.isPending}
                onClick={() => {
                  setFile(null);
                  setRemove(!!hash);
                  setError("");
                  save.reset();
                }}
              >
                <X size={14} />
                {hash ? "移除封面" : "取消所选"}
              </Button>
            )}
          </div>
          <small>
            建议使用 2:3 竖版图片，列表会居中裁切，原图完整保留。支持
            JPG、PNG、WebP，最大 10 MB。
          </small>
          {(error || save.error) && (
            <p role="alert" className="narrative-cover-error">
              {error || save.error?.message}
            </p>
          )}
          <footer>
            <Button
              variant="ghost"
              disabled={save.isPending}
              onClick={() => {
                setOpen(false);
                setFile(null);
              }}
            >
              取消
            </Button>
            <Button
              disabled={
                save.isPending || (!remove && (!file || !ready || !!error))
              }
              onClick={() => save.mutate()}
            >
              {save.isPending ? "保存中…" : "保存封面"}
            </Button>
          </footer>
        </div>
      </Modal>
    </>
  );
}
