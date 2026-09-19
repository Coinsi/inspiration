import { useEffect, useRef, useState } from "react";
import { ImagePlus, Upload, X } from "lucide-react";
import { api, apiUpload, blobUrl, type Project } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Modal } from "./ui/modal";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

export function ProjectDialog({
  project,
  onClose,
  onSaved,
}: {
  project?: Project;
  onClose: () => void;
  onSaved: (p: Project) => void;
}) {
  const zh = useI18n().lang === "zh";
  const [name, setName] = useState(""),
    [description, setDescription] = useState("");
  const [file, setFile] = useState<File | null>(null),
    [removed, setRemoved] = useState(false);
  const [preview, setPreview] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const pending = useRef(false),
    key = useRef(crypto.randomUUID()),
    picker = useRef<HTMLInputElement>(null);
  const change = () => {
    key.current = crypto.randomUUID();
    setError("");
  };
  useEffect(() => {
    if (!file) {
      setPreview("");
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  const src =
    preview ||
    (!removed && project?.cover_blob_hash
      ? blobUrl(project.id, project.cover_blob_hash)
      : "");
  function choose(next?: File) {
    if (!next || pending.current) return;
    if (
      !["image/jpeg", "image/png", "image/webp"].includes(next.type) ||
      !next.size ||
      next.size > 10 * 1024 * 1024
    ) {
      setError(
        zh
          ? "请选择 10 MB 以内的 JPG、PNG 或 WebP 图片"
          : "Choose a JPG, PNG or WebP image up to 10 MB",
      );
      return;
    }
    change();
    setFile(next);
    setRemoved(false);
  }
  async function save() {
    if (pending.current || (!project && !name.trim())) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      if (file) form.append("file", file);
      let saved: Project;
      if (project)
        saved = file
          ? await apiUpload<Project>(`/projects/${project.id}/cover`, form)
          : await api.del<Project>(`/projects/${project.id}/cover`);
      else {
        form.append("name", name.trim());
        form.append("description", description.trim());
        form.append("request_key", key.current);
        saved = await apiUpload<Project>("/projects/with-cover", form);
      }
      onSaved(saved);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <Modal
      open
      title={
        project
          ? zh
            ? "项目封面"
            : "Project cover"
          : zh
            ? "新建项目"
            : "New project"
      }
      width={560}
      onClose={() => {
        if (!pending.current) onClose();
      }}
    >
      <form
        className="project-create-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <p className="text-sm text-muted-foreground">
          {project
            ? project.name
            : zh
              ? "给故事起个名字，从这里开始创作。"
              : "Give your story a name and start creating."}
        </p>
        <div
          className="project-cover-upload"
          onDragOver={(e) => {
            e.preventDefault();
          }}
          onDrop={(e) => {
            e.preventDefault();
            choose(e.dataTransfer.files[0]);
          }}
        >
          <button
            type="button"
            className="project-cover-select"
            disabled={busy}
            onClick={() => picker.current?.click()}
            aria-label={zh ? "选择项目封面" : "Choose project cover"}
          >
            {src ? (
              <img
                src={src}
                alt={zh ? "项目封面预览" : "Project cover preview"}
              />
            ) : (
              <span>
                <ImagePlus size={30} strokeWidth={1.5} />
                <strong>
                  {zh ? "添加一张项目封面" : "Add a project cover"}
                </strong>
                <small>
                  {zh
                    ? "点击上传或拖入图片 · 可稍后添加"
                    : "Choose or drop an image · Optional"}
                </small>
              </span>
            )}
            {src && (
              <span className="project-cover-change">
                <Upload size={15} />
                {zh ? "更换封面" : "Change cover"}
              </span>
            )}
          </button>
          {src && (
            <button
              type="button"
              className="project-cover-remove"
              disabled={busy}
              aria-label={zh ? "移除封面" : "Remove cover"}
              onClick={() => {
                change();
                setFile(null);
                setRemoved(true);
              }}
            >
              <X size={16} />
            </button>
          )}
          <input
            ref={picker}
            className="sr-only"
            type="file"
            aria-label={zh ? "上传项目封面" : "Upload project cover"}
            accept="image/jpeg,image/png,image/webp"
            disabled={busy}
            onChange={(e) => {
              choose(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          {zh
            ? "推荐横向 16:9 图片，支持 JPG / PNG / WebP，最大 10 MB。"
            : "Landscape 16:9 recommended. JPG / PNG / WebP, up to 10 MB."}
        </p>
        {!project && (
          <>
            <label className="block space-y-2 text-sm">
              <span>{zh ? "项目名称" : "Project name"}</span>
              <Input
                autoFocus
                required
                maxLength={255}
                disabled={busy}
                placeholder={
                  zh ? "例如：雨夜重逢" : "For example: A rainy night reunion"
                }
                value={name}
                onChange={(e) => {
                  change();
                  setName(e.target.value);
                }}
              />
            </label>
            <label className="block space-y-2 text-sm">
              <span>
                {zh ? "项目简介" : "Description"}
                <small className="ml-2 text-muted-foreground">
                  {zh ? "选填" : "Optional"}
                </small>
              </span>
              <textarea
                maxLength={10000}
                disabled={busy}
                rows={3}
                value={description}
                onChange={(e) => {
                  change();
                  setDescription(e.target.value);
                }}
                placeholder={
                  zh
                    ? "简单写下这个故事，或你想实现的画面…"
                    : "A little about your story or vision…"
                }
              />
            </label>
          </>
        )}
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <Button
            type="button"
            variant="ghost"
            disabled={busy}
            onClick={onClose}
          >
            {zh ? "取消" : "Cancel"}
          </Button>
          <Button
            type="submit"
            disabled={busy || (project ? !file && !removed : !name.trim())}
          >
            {busy
              ? zh
                ? "保存中…"
                : "Saving…"
              : project
                ? zh
                  ? "保存封面"
                  : "Save cover"
                : zh
                  ? "创建项目"
                  : "Create project"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
