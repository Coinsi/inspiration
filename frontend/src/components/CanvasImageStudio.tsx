import { useEffect, useRef, useState } from "react";
import { apiUpload, blobUrl, type Generation } from "@/lib/api";
import { Modal } from "./ui/modal";
import { Button } from "./ui/button";

type Point = { x: number; y: number };
type Mark = { tool: string; color: string; size: number; points: Point[] };
export function CanvasImageStudio({
  path,
  projectId,
  source,
  onClose,
  onDone,
}: {
  path: string;
  projectId: string;
  source?: Generation;
  onClose: () => void;
  onDone: (g: Generation, label: string) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null),
    background = useRef<HTMLImageElement | null>(null),
    active = useRef<Mark | null>(null);
  const [size, setSize] = useState({ width: 1600, height: 900 }),
    [ready, setReady] = useState(!source),
    [error, setError] = useState("");
  const [tool, setTool] = useState("brush"),
    [color, setColor] = useState("#f2ca85"),
    [weight, setWeight] = useState(8),
    [marks, setMarks] = useState<Mark[]>([]),
    [redo, setRedo] = useState<Mark[]>([]);
  const [crop, setCrop] = useState<{
      x: number;
      y: number;
      width: number;
      height: number;
    } | null>(null),
    [busy, setBusy] = useState(false);
  const pending = useRef(false),
    request = useRef({ digest: "", key: "" }),
    [name, setName] = useState(source ? "画面编辑" : "构图草图");
  useEffect(() => {
    if (!source?.output_blob_hash) return;
    let canceled = false;
    const image = new Image();
    image.onload = () => {
      if (canceled) return;
      if (
        image.width * image.height > 40_000_000 ||
        Math.max(image.width, image.height) > 8192
      ) {
        setError("编辑支持最长边8192像素、4000万像素以内的图片");
        return;
      }
      background.current = image;
      setSize({ width: image.width, height: image.height });
      setReady(true);
    };
    image.onerror = () => {
      if (!canceled) setError("原图读取失败，请关闭后重试");
    };
    image.src = blobUrl(projectId, source.output_blob_hash);
    return () => {
      canceled = true;
    };
  }, [source?.id, projectId]);
  function paint(extra?: Mark) {
    const el = canvas.current;
    if (!el) return;
    const ctx = el.getContext("2d")!;
    ctx.clearRect(0, 0, el.width, el.height);
    if (background.current) ctx.drawImage(background.current, 0, 0);
    else {
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, el.width, el.height);
    }
    for (const m of [...marks, ...(extra ? [extra] : [])]) {
      const a = m.points[0],
        b = m.points[m.points.length - 1];
      ctx.strokeStyle = m.color;
      ctx.fillStyle = m.color;
      ctx.lineWidth = m.size;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.beginPath();
      if (m.tool === "rectangle")
        ctx.rect(
          Math.min(a.x, b.x),
          Math.min(a.y, b.y),
          Math.abs(b.x - a.x),
          Math.abs(b.y - a.y),
        );
      else if (m.tool === "ellipse")
        ctx.ellipse(
          (a.x + b.x) / 2,
          (a.y + b.y) / 2,
          Math.abs(b.x - a.x) / 2,
          Math.abs(b.y - a.y) / 2,
          0,
          0,
          Math.PI * 2,
        );
      else if (m.tool === "arrow" || m.tool === "line") {
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        if (m.tool === "arrow") {
          const angle = Math.atan2(b.y - a.y, b.x - a.x),
            length = Math.max(18, m.size * 3);
          ctx.moveTo(
            b.x - length * Math.cos(angle - 0.5),
            b.y - length * Math.sin(angle - 0.5),
          );
          ctx.lineTo(b.x, b.y);
          ctx.lineTo(
            b.x - length * Math.cos(angle + 0.5),
            b.y - length * Math.sin(angle + 0.5),
          );
        }
      } else {
        ctx.moveTo(a.x, a.y);
        for (const p of m.points) ctx.lineTo(p.x, p.y);
        if (m.points.length === 1) {
          ctx.lineTo(a.x + 0.01, a.y + 0.01);
        }
      }
      ctx.stroke();
    }
  }
  useEffect(() => {
    paint();
  }, [marks, size, ready]);
  function point(e: React.PointerEvent<HTMLCanvasElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(
        0,
        Math.min(size.width, ((e.clientX - r.left) * size.width) / r.width),
      ),
      y: Math.max(
        0,
        Math.min(size.height, ((e.clientY - r.top) * size.height) / r.height),
      ),
    };
  }
  function end(e: React.PointerEvent<HTMLCanvasElement>) {
    const current = active.current;
    if (!current) return;
    active.current = null;
    if (tool === "crop") {
      const a = current.points[0],
        b = point(e);
      const width = Math.round(Math.abs(a.x - b.x)),
        height = Math.round(Math.abs(a.y - b.y));
      setCrop(
        width > 1 && height > 1
          ? {
              x: Math.floor(Math.min(a.x, b.x)),
              y: Math.floor(Math.min(a.y, b.y)),
              width,
              height,
            }
          : null,
      );
      paint();
    } else {
      setMarks((previous) => [...previous, current]);
      setRedo([]);
    }
  }
  async function save() {
    if (!ready || pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      paint();
      const output = document.createElement("canvas");
      output.width = crop?.width ?? size.width;
      output.height = crop?.height ?? size.height;
      output
        .getContext("2d")!
        .drawImage(
          canvas.current!,
          crop?.x ?? 0,
          crop?.y ?? 0,
          output.width,
          output.height,
          0,
          0,
          output.width,
          output.height,
        );
      const blob = await new Promise<Blob>((resolve, reject) =>
        output.toBlob(
          (b) => (b ? resolve(b) : reject(new Error("图片保存失败"))),
          "image/png",
        ),
      );
      const digest = Array.from(
        new Uint8Array(
          await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()),
        ),
      )
        .map((v) => v.toString(16).padStart(2, "0"))
        .join("");
      if (request.current.digest !== digest)
        request.current = { digest, key: crypto.randomUUID() };
      const form = new FormData();
      form.append("file", blob, (name.trim() || "画布作品") + ".png");
      form.append("request_key", request.current.key);
      if (source) form.append("source_generation_id", source.id);
      const g = await apiUpload<Generation>(path + "/import", form);
      onDone(g, name.trim() || "画布作品");
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
      title={source ? "画面编辑与标注" : "自由绘图"}
      width={1180}
      onClose={() => {
        if (!pending.current) onClose();
      }}
    >
      <div className="canvas-paint-tools">
        {Object.entries({
          brush: "画笔",
          line: "直线",
          arrow: "箭头",
          rectangle: "矩形",
          ellipse: "椭圆",
          crop: "裁切",
        }).map(([value, label]) => (
          <Button
            key={value}
            size="sm"
            variant={tool === value ? "default" : "ghost"}
            disabled={busy}
            onClick={() => setTool(value)}
          >
            {label}
          </Button>
        ))}
        <input
          aria-label="画笔颜色"
          type="color"
          value={color}
          disabled={busy}
          onChange={(e) => setColor(e.target.value)}
        />
        <label>
          粗细{" "}
          <input
            aria-label="画笔粗细"
            type="range"
            min="1"
            max="40"
            value={weight}
            disabled={busy}
            onChange={(e) => setWeight(+e.target.value)}
          />
        </label>
        <Button
          size="sm"
          disabled={busy || !marks.length}
          onClick={() => {
            setRedo((r) => [marks[marks.length - 1], ...r]);
            setMarks((m) => m.slice(0, -1));
          }}
        >
          撤销笔画
        </Button>
        <Button
          size="sm"
          disabled={busy || !redo.length}
          onClick={() => {
            setMarks((m) => [...m, redo[0]]);
            setRedo((r) => r.slice(1));
          }}
        >
          重做笔画
        </Button>
        {crop && (
          <Button size="sm" disabled={busy} onClick={() => setCrop(null)}>
            取消裁切
          </Button>
        )}
      </div>
      <div className="canvas-paint-stage">
        <div
          style={{
            width: "100%",
            maxWidth: `calc(57vh * ${size.width / size.height})`,
            position: "relative",
          }}
        >
          <canvas
            ref={canvas}
            width={size.width}
            height={size.height}
            aria-label="绘图画面"
            style={{
              width: "100%",
              display: "block",
              touchAction: "none",
              cursor: "crosshair",
            }}
            onPointerDown={(e) => {
              if (!ready || busy) return;
              if (marks.length >= 200) {
                setError("最多200笔，请先撤销部分笔画或保存");
                return;
              }
              e.preventDefault();
              e.currentTarget.setPointerCapture(e.pointerId);
              active.current = {
                tool,
                color,
                size: weight,
                points: [point(e)],
              };
            }}
            onPointerMove={(e) => {
              if (!active.current) return;
              const p = point(e);
              if (active.current.points.length < 5000)
                active.current.points.push(p);
              if (tool === "crop") {
                const a = active.current.points[0];
                setCrop({
                  x: Math.min(a.x, p.x),
                  y: Math.min(a.y, p.y),
                  width: Math.abs(a.x - p.x),
                  height: Math.abs(a.y - p.y),
                });
              } else paint(active.current);
            }}
            onPointerUp={end}
            onPointerCancel={() => {
              active.current = null;
              paint();
            }}
          />
          {crop && (
            <div
              className="canvas-crop-guide"
              style={{
                left: (crop.x / size.width) * 100 + "%",
                top: (crop.y / size.height) * 100 + "%",
                width: (crop.width / size.width) * 100 + "%",
                height: (crop.height / size.height) * 100 + "%",
              }}
            />
          )}
        </div>
      </div>
      {!ready && !error && <p role="status">正在读取原图…</p>}
      <div className="canvas-paint-footer">
        <input
          aria-label="绘图作品名称"
          value={name}
          maxLength={110}
          disabled={busy}
          onChange={(e) => setName(e.target.value)}
        />
        <span>
          {crop
            ? `${Math.round(crop.width)} × ${Math.round(crop.height)}`
            : `${size.width} × ${size.height}`}{" "}
          · 保存独立PNG，原图不变
        </span>
        <Button disabled={!ready || busy} onClick={() => void save()}>
          {busy ? "正在保存…" : "保存并放入画布"}
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-danger">
          {error}
        </p>
      )}
    </Modal>
  );
}
