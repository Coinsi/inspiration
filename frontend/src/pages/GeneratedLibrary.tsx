import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Download, Search, Sparkles } from "lucide-react";
import { api, blobUrl } from "@/lib/api";
import { mediaSize } from "@/lib/library";
import { MaterialTabs } from "@/components/MaterialTabs";
import { MediaPreview } from "@/components/MediaPreview";
import { MediaVideo } from "@/components/MediaVideo";
import { Button } from "@/components/ui/button";
import "./generated-library.css";

export type GeneratedMaterial = {
  id: string;
  job_id: string;
  title: string;
  prompt: string;
  output_type: "image" | "video";
  reference_available: boolean;
  blob_hash: string;
  model_name: string;
  provider: string;
  created_at: string;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  size_bytes: number;
  target_type: string;
  target_id: string;
};

export default function GeneratedLibrary() {
  const { projectId } = useParams();
  return <Collection key={projectId} projectId={projectId!} />;
}

function Collection({ projectId }: { projectId: string }) {
  const [kind, setKind] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const base = `/projects/${projectId}`;
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(search);
      setPage(0);
    }, 250);
    return () => clearTimeout(timer);
  }, [search]);
  const results = useQuery({
    queryKey: ["generated-library", projectId, kind, query, page],
    queryFn: () =>
      api.get<{ items: GeneratedMaterial[]; total: number }>(
        `${base}/library/generated?${new URLSearchParams({ query, offset: String(page * 24), ...(kind ? { kind } : {}) })}`,
      ),
    refetchInterval: 5000,
  });
  return (
    <div className="studio-page material-workspace generated-library">
      <MaterialTabs projectId={projectId} />
      <header className="generated-heading">
        <div>
          <span className="generated-eyebrow">
            <Sparkles size={14} /> 创作素材
          </span>
          <h1>AI 生成</h1>
          <p>
            每一次生成，都是下一次创作的起点。图片和视频完成后自动保存在这里。
          </p>
        </div>
        <Link className="generated-create" to={`${base}/workbench`}>
          开始创作 ↗
        </Link>
      </header>
      <div className="generated-toolbar">
        <div
          className="generated-filters"
          role="group"
          aria-label="生成素材类型"
        >
          {[
            ["", "全部"],
            ["image", "图片"],
            ["video", "视频"],
          ].map(([value, label]) => (
            <button
              key={value}
              aria-pressed={kind === value}
              onClick={() => {
                setKind(value);
                setPage(0);
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="generated-search">
          <Search size={16} />
          <input
            aria-label="搜索生成素材"
            placeholder="搜索提示词或模型"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <span className="generated-count">
          {results.data ? `${results.data.total} 项素材` : ""}
        </span>
      </div>
      {results.isError ? (
        <div role="alert" className="generated-empty">
          <p>{results.error.message}</p>
          <Button onClick={() => void results.refetch()}>重新加载</Button>
        </div>
      ) : results.isPending ? (
        <p role="status" className="generated-empty">
          正在整理创作素材…
        </p>
      ) : !results.data.items.length ? (
        <div className="generated-empty">
          <Sparkles size={28} />
          <h2>{query || kind ? "没有匹配的素材" : "你的创作，从这里积累"}</h2>
          <p>
            {query || kind
              ? "试试其他关键词或素材类型。"
              : "完成一次图片或视频生成后，会自动出现在这里，无需手动上传。"}
          </p>
          <Link to={`${base}/workbench`}>前往工作台 →</Link>
        </div>
      ) : (
        <div className="generated-grid">
          {results.data.items.map((item) => (
            <article key={item.id}>
              {item.output_type === "video" ? (
                <MediaVideo
                  presentation="studio"
                  src={blobUrl(projectId, item.blob_hash)}
                  label={item.title}
                />
              ) : (
                <MediaPreview
                  src={blobUrl(projectId, item.blob_hash)}
                  label={item.title}
                  filename={`inspiration-${item.id}`}
                />
              )}
              <div className="generated-card-body">
                <div className="generated-meta">
                  <span>
                    {item.output_type === "video" ? "视频" : "图片"} ·{" "}
                    {item.model_name}
                  </span>
                  <time dateTime={item.created_at}>
                    {new Date(item.created_at).toLocaleDateString()}
                  </time>
                </div>
                <p className="generated-prompt" title={item.prompt}>
                  {item.title}
                </p>
                <div className="generated-specs">
                  {item.width && item.height ? (
                    <span>
                      {item.width} × {item.height}
                    </span>
                  ) : null}
                  <span>{mediaSize(item.size_bytes)}</span>
                </div>
                <details>
                  <summary>提示词与来源</summary>
                  <p className="generated-full-prompt">
                    {item.prompt || "没有提示词记录"}
                  </p>
                  <Link to={`${base}/tasks?job=${item.job_id}`}>
                    查看来源任务 ↗
                  </Link>
                </details>
                <footer>
                  {item.reference_available && (
                    <Link
                      className="generated-reference"
                      to={`${base}/workbench?reference=${item.id}`}
                    >
                      用作参考
                    </Link>
                  )}
                  <Link to={`${base}/workbench?reuse=${item.id}`}>
                    复用提示词
                  </Link>
                  <a
                    aria-label={`下载${item.output_type === "video" ? "视频" : "图片"}`}
                    href={`${blobUrl(projectId, item.blob_hash)}&download=true`}
                    download
                  >
                    <Download size={15} />
                  </a>
                </footer>
              </div>
            </article>
          ))}
        </div>
      )}
      {results.data && results.data.total > 24 && (
        <div className="generated-pagination">
          <Button
            variant="outline"
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
          >
            上一页
          </Button>
          <span>
            {page + 1} / {Math.ceil(results.data.total / 24)}
          </span>
          <Button
            variant="outline"
            disabled={(page + 1) * 24 >= results.data.total}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </Button>
        </div>
      )}
    </div>
  );
}
