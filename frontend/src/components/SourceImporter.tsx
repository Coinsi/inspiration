import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { FileUp, Upload } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
const field = "w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm";
type SourceItem = {
  name: string;
  type: string;
  summary?: string;
  source_id?: string;
  source_url?: string;
  tags?: string[];
};
export function SourceImporter({ base }: { base: string }) {
  const [source, setSource] = useState(""),
    [items, setItems] = useState<SourceItem[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [result, setResult] = useState<string[]>([]);
  const qc = useQueryClient();
  const key = useRef(crypto.randomUUID());
  const busyRef = useRef(false);
  function sample() {
    const url = URL.createObjectURL(
      new Blob(
        [
          JSON.stringify(
            {
              source: "示例外部目录",
              items: [
                {
                  name: "海边小屋",
                  type: "location",
                  summary: "面朝海岸的木屋，适合清晨场景。",
                  source_id: "location-001",
                  source_url: "https://example.com/locations/001",
                  tags: ["海岸", "木屋"],
                },
              ],
            },
            null,
            2,
          ),
        ],
        { type: "application/json" },
      ),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "source-catalogue.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section className="max-w-4xl space-y-5 rounded-2xl border border-border bg-card p-6">
      <div>
        <h2 className="text-lg font-medium">导入角色与资产目录</h2>
        <p className="mt-2 text-sm leading-7 text-muted-foreground">
          读取其他工具导出的 JSON
          目录，导入名称、描述、标签和来源编号。这里只导入资产信息；视频、图片原文件仍通过素材上传入口导入。
        </p>
      </div>
      <div className="flex flex-wrap gap-3">
        <label className="studio-link cursor-pointer">
          <Upload size={16} />
          选择目录文件
          <input
            className="sr-only"
            type="file"
            accept=".json,application/json"
            aria-label="选择外部目录文件"
            disabled={busy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file || busyRef.current) return;
              busyRef.current = true;
              setBusy(true);
              void (async () => {
                setError("");
                setResult([]);
                try {
                  if (file.size > 2_000_000) throw new Error("目录文件最大2MB");
                  const data = JSON.parse(await file.text());
                  const rows = Array.isArray(data) ? data : data?.items;
                  if (
                    !Array.isArray(rows) ||
                    !rows.length ||
                    rows.length > 100 ||
                    rows.some(
                      (r) =>
                        !r ||
                        typeof r.name !== "string" ||
                        (r.type !== undefined && typeof r.type !== "string") ||
                        ["summary", "source_id", "source_url"].some(
                          (k) => r[k] !== undefined && typeof r[k] !== "string",
                        ) ||
                        (r.tags !== undefined &&
                          (!Array.isArray(r.tags) ||
                            r.tags.some(
                              (t: unknown) => typeof t !== "string",
                            ))),
                    ) ||
                    (data?.source !== undefined &&
                      typeof data.source !== "string")
                  )
                    throw new Error("请选择包含1至100项资产的有效目录");
                  setItems(rows);
                  setSource(data.source || file.name);
                  key.current = crypto.randomUUID();
                } catch (e) {
                  setItems([]);
                  setError((e as Error).message);
                } finally {
                  busyRef.current = false;
                  setBusy(false);
                }
              })();
            }}
          />
        </label>
        <Button variant="outline" onClick={sample}>
          下载格式示例
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      {items.length > 0 && (
        <>
          <label className="block space-y-2 text-xs">
            <span>目录来源</span>
            <input
              className={field}
              value={source}
              disabled={busy || !!result.length}
              maxLength={1000}
              onChange={(e) => {
                setSource(e.target.value);
                key.current = crypto.randomUUID();
              }}
            />
          </label>
          <div className="max-h-72 overflow-auto rounded-lg border border-border">
            {items.map((i, n) => (
              <div key={n} className="border-b border-border p-3">
                <h3 className="text-sm font-medium">{i.name}</h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  {i.summary}
                </p>
              </div>
            ))}
          </div>
          <Button
            disabled={busy || !!result.length || !source.trim()}
            onClick={() => {
              if (busyRef.current) return;
              busyRef.current = true;
              setBusy(true);
              setError("");
              void api
                .post<{ asset_ids: string[] }>(
                  `${base}/source-catalogues/import`,
                  { request_key: key.current, source, items },
                )
                .then(async (r) => {
                  setResult(r.asset_ids);
                  await qc.invalidateQueries({
                    queryKey: ["assets", base.split("/").pop()],
                  });
                })
                .catch((e) => setError(e.message))
                .finally(() => {
                  busyRef.current = false;
                  setBusy(false);
                });
            }}
          >
            <FileUp size={16} />
            导入 {items.length} 项资产信息
          </Button>
        </>
      )}
      {result.length > 0 && (
        <div role="status" className="rounded-lg bg-success/10 p-4 text-sm">
          已导入 {result.length} 项资产并保留来源信息。
          <Link className="ml-2 text-primary underline" to={`${base}/assets`}>
            进入资产库
          </Link>
        </div>
      )}
    </section>
  );
}
