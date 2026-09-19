import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Upload, Loader2, X } from "lucide-react";
import { apiUpload, type Generation } from "@/lib/api";
import { Button } from "./ui/button";

export function CanvasMediaImport({
  projectId,
  targetType,
  targetId,
  label,
  onBusy,
  onPick,
}: {
  projectId: string;
  targetType: "asset" | "shot";
  targetId: string;
  label: string;
  onBusy: (busy: boolean) => void;
  onPick: (generation: Generation, label: string) => boolean;
}) {
  const cache = useQueryClient(),
    input = useRef<HTMLInputElement>(null),
    pending = useRef(false);
  const [file, setFile] = useState<File | null>(null),
    [url, setUrl] = useState("");
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [over, setOver] = useState(false);
  const [uploaded, setUploaded] = useState<Generation | null>(null);
  useEffect(() => {
    if (!file) {
      setUrl("");
      return;
    }
    const value = URL.createObjectURL(file);
    setUrl(value);
    return () => URL.revokeObjectURL(value);
  }, [file]);
  function choose(files: FileList | null) {
    if (pending.current || !files?.length) return;
    setError("");
    if (files.length !== 1) {
      setError("请一次导入一个文件");
      return;
    }
    const next = files[0];
    if (
      !["image/png", "image/jpeg", "image/webp", "video/mp4"].includes(
        next.type,
      )
    ) {
      setError("请选择 PNG、JPG、WebP 图片或 MP4 视频");
      return;
    }
    if (!next.size || next.size > 100 * 1024 * 1024) {
      setError("文件不能为空，单个文件最大 100 MB；大视频请从素材库导入");
      return;
    }
    setFile(next);
    setUploaded(null);
  }
  async function upload() {
    if (pending.current || !file) return;
    pending.current = true;
    setBusy(true);
    onBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      const result =
        uploaded ||
        (await apiUpload<Generation>(
          `/projects/${projectId}/media/${targetType}/${targetId}/upload`,
          form,
        ));
      setUploaded(result);
      void cache.invalidateQueries({
        queryKey: ["gens", targetType, targetId],
      });
      if (!onPick(result, file.name.replace(/\.[^.]+$/, "").slice(0, 120)))
        setError("素材已保存，画布空间不足。整理节点后可从版本列表放入。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      pending.current = false;
      setBusy(false);
      onBusy(false);
    }
  }
  return (
    <section
      className={`canvas-import ${over ? "is-over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        choose(e.dataTransfer.files);
      }}
    >
      <input
        ref={input}
        type="file"
        className="sr-only"
        aria-label="本地素材文件"
        accept="image/png,image/jpeg,image/webp,video/mp4"
        disabled={busy}
        onChange={(e) => {
          choose(e.target.files);
          e.target.value = "";
        }}
      />
      {file ? (
        <>
          <div className="canvas-import-file">
            {url &&
              (file.type === "video/mp4" ? (
                <video src={url} muted controls preload="metadata" />
              ) : (
                <img src={url} alt="待导入素材" />
              ))}
            <div>
              <strong>{file.name}</strong>
              <small>
                {(file.size / 1024 / 1024).toFixed(1)} MB · 保存到 {label}
              </small>
            </div>
            <Button
              size="icon"
              variant="ghost"
              disabled={busy}
              aria-label="移除待导入文件"
              onClick={() => {
                setFile(null);
                setUploaded(null);
                setError("");
              }}
            >
              <X size={16} />
            </Button>
          </div>
          <Button size="sm" disabled={busy} onClick={() => void upload()}>
            {busy ? (
              <Loader2 size={15} className="animate-spin" />
            ) : (
              <Upload size={15} />
            )}
            {busy ? "正在导入…" : uploaded ? "放入画布" : "导入并放入画布"}
          </Button>
        </>
      ) : (
        <>
          <Button
            size="sm"
            variant="outline"
            onClick={() => input.current?.click()}
          >
            <Upload size={15} />
            导入本地素材
          </Button>
          <p>
            或拖放文件到这里 · 图片 / MP4，最大 100 MB
            <br />
            保存到「{label}」的版本中，不替换已采用的作品。
          </p>
        </>
      )}
      {error && (
        <p role="alert" className="text-danger">
          {error}
        </p>
      )}
    </section>
  );
}
