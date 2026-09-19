import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Grid2X2, Loader2, Check } from "lucide-react";
import { api, blobUrl, type Generation } from "@/lib/api";
import { Modal } from "./ui/modal";
import { Button } from "./ui/button";

export function CanvasGridSplit({
  projectId,
  source,
  label,
  available,
  onClose,
  onInsert,
}: {
  projectId: string;
  source: Generation;
  label: string;
  available: number;
  onClose: () => void;
  onInsert: (items: Generation[], columns: number) => void;
}) {
  const cache = useQueryClient();
  const [rows, setRows] = useState(2),
    [columns, setColumns] = useState(2);
  const [cells, setCells] = useState([0, 1, 2, 3]);
  const [size, setSize] = useState<{ width: number; height: number } | null>(
    null,
  );
  const [imageError, setImageError] = useState(false),
    [busy, setBusy] = useState(false);
  const [error, setError] = useState(""),
    [result, setResult] = useState<Generation[] | null>(null);
  const request = useRef({ signature: "", key: "" });
  const pending = useRef(false);
  function configure(r: number, c: number) {
    setRows(r);
    setColumns(c);
    setCells(Array.from({ length: r * c }, (_, i) => i));
    setError("");
  }
  const invalid =
    !size ||
    imageError ||
    rows * columns < 2 ||
    !cells.length ||
    cells.length > available ||
    size.width < columns ||
    size.height < rows ||
    size.width * size.height > 40_000_000 ||
    Math.max(size.width, size.height) > 8192;
  async function split() {
    if (pending.current || invalid) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const options = {
        rows,
        columns,
        cells: [...cells].sort((a, b) => a - b),
      };
      const signature = JSON.stringify(options);
      if (request.current.signature !== signature)
        request.current = { signature, key: crypto.randomUUID() };
      const items =
        result ||
        (await api.post<Generation[]>(
          `/projects/${projectId}/generations/${source.id}/grid-split`,
          { ...options, request_key: request.current.key },
        ));
      setResult(items);
      void cache.invalidateQueries({
        queryKey: ["gens", source.target_type, source.target_id],
      });
      onInsert(items, columns);
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
      title="拆分图片"
      width={1060}
      onClose={() => {
        if (!pending.current) onClose();
      }}
    >
      <div className="canvas-grid-tool">
        <section className="canvas-grid-preview">
          <div
            className="canvas-grid-stage"
            style={{
              aspectRatio: size ? `${size.width}/${size.height}` : "16/9",
              maxWidth: size
                ? `calc(56vh * ${size.width / size.height})`
                : undefined,
            }}
          >
            <img
              src={blobUrl(projectId, source.output_blob_hash!)}
              alt="拆分原图"
              draggable={false}
              onLoad={(e) => {
                setSize({
                  width: e.currentTarget.naturalWidth,
                  height: e.currentTarget.naturalHeight,
                });
                setImageError(false);
              }}
              onError={() => setImageError(true)}
            />
            {size && !imageError && (
              <div
                className="canvas-grid-overlay"
                style={{
                  gridTemplateColumns: `repeat(${columns},1fr)`,
                  gridTemplateRows: `repeat(${rows},1fr)`,
                }}
              >
                {Array.from({ length: rows * columns }, (_, i) => (
                  <button
                    key={i}
                    type="button"
                    aria-label={`第 ${Math.floor(i / columns) + 1} 行第 ${(i % columns) + 1} 列`}
                    aria-pressed={cells.includes(i)}
                    disabled={busy || !!result}
                    onClick={() =>
                      setCells((previous) =>
                        previous.includes(i)
                          ? previous.filter((v) => v !== i)
                          : [...previous, i],
                      )
                    }
                  >
                    <span>
                      {String(i + 1).padStart(2, "0")}
                      {cells.includes(i) && <Check size={12} />}
                    </span>
                  </button>
                ))}
              </div>
            )}
            {!size && !imageError && (
              <div className="canvas-grid-loading" role="status">
                <Loader2 className="animate-spin" />
                正在读取原图…
              </div>
            )}
          </div>
          {imageError && <p role="alert">原图读取失败，请关闭后重新打开。</p>}
          <p className="canvas-grid-caption">
            点击画面选择要保留的格子 · 从左到右、从上到下排列
          </p>
        </section>
        <section className="canvas-grid-options">
          <div className="canvas-tool-eyebrow">
            <Grid2X2 size={16} /> 宫格拆分
          </div>
          <h3>一张图，多个创作起点</h3>
          <p>
            适合分镜拼图、角色设定和多角度参考。按等分线裁切，保留每格原始像素，不进行缩放。
          </p>
          <div className="canvas-grid-presets">
            {[
              [1, 2],
              [2, 2],
              [3, 3],
              [4, 4],
            ].map(([r, c]) => (
              <button
                key={`${r}-${c}`}
                disabled={busy || !!result}
                aria-pressed={rows === r && columns === c}
                onClick={() => configure(r, c)}
              >
                {r} × {c}
              </button>
            ))}
          </div>
          <div className="canvas-grid-dimensions">
            <label>
              行数
              <select
                aria-label="拆分行数"
                value={rows}
                disabled={busy || !!result}
                onChange={(e) => configure(Number(e.target.value), columns)}
              >
                {[1, 2, 3, 4].map((n) => (
                  <option key={n}>{n}</option>
                ))}
              </select>
            </label>
            <label>
              列数
              <select
                aria-label="拆分列数"
                value={columns}
                disabled={busy || !!result}
                onChange={(e) => configure(rows, Number(e.target.value))}
              >
                {[1, 2, 3, 4].map((n) => (
                  <option key={n}>{n}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="canvas-grid-summary">
            <strong>
              已选 {cells.length} / {rows * columns} 格
            </strong>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy || !!result}
              onClick={() =>
                setCells(
                  cells.length === rows * columns
                    ? []
                    : Array.from({ length: rows * columns }, (_, i) => i),
                )
              }
            >
              {cells.length === rows * columns ? "取消全选" : "选择全部"}
            </Button>
          </div>
          <p className="canvas-grid-source">
            来源：{label}
            {size && (
              <>
                <br />
                原图 {size.width} × {size.height} · 每格约{" "}
                {Math.floor(size.width / columns)} ×{" "}
                {Math.floor(size.height / rows)}
              </>
            )}
          </p>
          <p>
            结果会保存到原对象的版本中，并作为独立图片放入画布。撤销画布操作不会删除已保存的素材。
          </p>
          {rows * columns < 2 && <p role="alert">请至少拆分成两格。</p>}
          {cells.length > available && (
            <p role="alert">画布剩余 {available} 个节点位置，请减少选择。</p>
          )}
          {size &&
            (size.width * size.height > 40_000_000 ||
              Math.max(size.width, size.height) > 8192) && (
              <p role="alert">支持最长边 8192 像素、4000 万像素以内的图片。</p>
            )}
          {error && (
            <p role="alert" className="text-danger">
              {error}
            </p>
          )}
          <Button
            className="w-full"
            disabled={busy || invalid}
            onClick={() => void split()}
          >
            {busy ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                正在拆分并保存…
              </>
            ) : result ? (
              "重新放入画布"
            ) : (
              `拆分并放入画布 · ${cells.length} 张`
            )}
          </Button>
        </section>
      </div>
    </Modal>
  );
}
