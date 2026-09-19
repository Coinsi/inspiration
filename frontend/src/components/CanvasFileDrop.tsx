import { useEffect, useRef, useState } from "react";
import { apiUpload, type Generation } from "@/lib/api";
import { Modal } from "./ui/modal";
import { Button } from "./ui/button";

export function CanvasFileDrop({
  path,
  files,
  onDone,
  onClose,
}: {
  path: string;
  files: File[];
  onDone: (items: { generation: Generation; label: string }[]) => void;
  onClose: () => void;
}) {
  const keys = useRef(files.map(() => crypto.randomUUID()));
  const results = useRef<(Generation | undefined)[]>([]);
  const working = useRef(false),
    started = useRef(false);
  const [states, setStates] = useState(files.map(() => "等待导入")),
    [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function process() {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    setError("");
    for (let i = 0; i < files.length; i++) {
      if (results.current[i]) continue;
      const update = (value: string) =>
        setStates((s) => s.map((v, index) => (index === i ? value : v)));
      const file = files[i];
      try {
        if (!file.size || file.size > 100 * 1024 * 1024)
          throw new Error("单文件最大 100 MB，大视频请用素材库");
        if (
          !["image/png", "image/jpeg", "image/webp", "video/mp4"].includes(
            file.type,
          )
        )
          throw new Error("支持 PNG、JPG、WebP、MP4");
        update("正在导入…");
        const form = new FormData();
        form.append("file", file);
        form.append("request_key", keys.current[i]);
        results.current[i] = await apiUpload<Generation>(
          path + "/import",
          form,
        );
        update("已保存");
      } catch (e) {
        update((e as Error).message);
      }
    }
    working.current = false;
    setBusy(false);
    if (files.every((_, i) => results.current[i])) finish();
  }
  function finish() {
    try {
      const items = files.flatMap((f, i) =>
        results.current[i]
          ? [
              {
                generation: results.current[i]!,
                label:
                  f.name.replace(/\.[^.]+$/, "").slice(0, 120) || "粘贴图片",
              },
            ]
          : [],
      );
      if (!items.length) return;
      onDone(items);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    if (!started.current) {
      started.current = true;
      void process();
    }
  }, []);
  return (
    <Modal
      open
      title="导入到画布"
      width={620}
      onClose={() => {
        if (!working.current) onClose();
      }}
    >
      <p className="text-sm text-muted-foreground mb-4">
        文件会保存为项目素材，自动放在画布落点；不会替换现有作品。
      </p>
      <div className="space-y-3">
        {files.map((f, i) => (
          <div key={i} className="flex justify-between gap-4 text-sm">
            <span className="truncate">{f.name}</span>
            <span role="status" className="text-muted-foreground shrink-0">
              {states[i]}
            </span>
          </div>
        ))}
      </div>
      {error && (
        <p role="alert" className="text-danger mt-3">
          {error}
        </p>
      )}
      {!busy && (
        <div className="flex justify-end gap-2 mt-5">
          <Button variant="outline" onClick={() => void process()}>
            重试未完成文件
          </Button>
          <Button onClick={finish} disabled={!results.current.some(Boolean)}>
            放入已完成素材
          </Button>
        </div>
      )}
    </Modal>
  );
}
