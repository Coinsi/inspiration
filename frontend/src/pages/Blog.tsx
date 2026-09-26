import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Search } from "lucide-react";
import { api } from "@/lib/api";
import {
  BlogCard,
  BlogFrame,
  categories,
  type BlogList,
} from "@/components/website/BlogShared";

export default function Blog() {
  const [params, setParams] = useSearchParams();
  const q = (params.get("q") || "").slice(0, 150);
  const category =
    categories.find((item) => item === params.get("category")) || "";
  const rawOffset = Number(params.get("offset"));
  const offset = Number.isSafeInteger(rawOffset) ? Math.max(0, rawOffset) : 0;
  const [search, setSearch] = useState(q);
  useEffect(() => setSearch(q), [q]);
  useEffect(() => {
    const old = document.title;
    document.title = "创作手记 · Inspiration";
    return () => {
      document.title = old;
    };
  }, []);
  const query = useQuery({
    queryKey: ["blog", q, category, offset],
    queryFn: () =>
      api.get<BlogList>(
        `/blog?${new URLSearchParams({ q, offset: String(offset), ...(category ? { category } : {}) })}`,
      ),
  });
  function filter(next: Record<string, string>) {
    setParams({ ...Object.fromEntries(params), offset: "0", ...next });
  }
  return (
    <BlogFrame>
      <main className="site-contained journal-main">
        <header className="journal-heading">
          <span className="site-eyebrow">THE INSPIRATION JOURNAL</span>
          <h1>
            创作不止于工具，
            <br />
            <em>也在每一次思考里。</em>
          </h1>
          <div>
            <p>
              分享工作台的新进展，也记录从灵感走向作品的方法。
              <br />
              慢慢读，让下一个想法找到起点。
            </p>
            <span>创作指南 / 产品更新 / 灵感笔记</span>
          </div>
        </header>
        <div className="journal-toolbar">
          <nav aria-label="文章分类">
            {["全部", ...categories].map((c) => (
              <button
                key={c}
                aria-pressed={(category || "全部") === c}
                onClick={() => filter({ category: c === "全部" ? "" : c })}
              >
                {c}
              </button>
            ))}
          </nav>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              filter({ q: search.trim() });
            }}
          >
            <label className="sr-only" htmlFor="blog-search">
              搜索文章
            </label>
            <input
              id="blog-search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="寻找一个灵感…"
              maxLength={150}
            />
            <button aria-label="搜索文章">
              <Search size={16} />
            </button>
          </form>
        </div>
        {query.isLoading ? (
          <div className="journal-loading" role="status">
            正在翻开创作手记…
          </div>
        ) : query.error ? (
          <div className="journal-loading" role="alert">
            <p>文章暂时无法加载。</p>
            <button onClick={() => void query.refetch()}>重新加载</button>
          </div>
        ) : query.data?.items.length ? (
          <>
            <div className="journal-results">
              <span>{q ? `“${q}” 的搜索结果` : category || "所有文章"}</span>
              <span>{query.data.total} 篇记录</span>
            </div>
            <div className="journal-grid">
              {query.data.items.map((a, i) => (
                <BlogCard
                  key={a.slug}
                  article={a}
                  featured={i === 0 && offset === 0 && !q && !category}
                />
              ))}
            </div>
            <nav className="journal-pagination" aria-label="文章分页">
              <button
                disabled={!offset}
                onClick={() =>
                  setParams({
                    ...Object.fromEntries(params),
                    offset: String(Math.max(0, offset - 9)),
                  })
                }
              >
                <ArrowLeft size={14} />
                上一页
              </button>
              <span>
                {Math.floor(offset / 9) + 1} /{" "}
                {Math.max(1, Math.ceil(query.data.total / 9))}
              </span>
              <button
                disabled={offset + 9 >= query.data.total}
                onClick={() =>
                  setParams({
                    ...Object.fromEntries(params),
                    offset: String(offset + 9),
                  })
                }
              >
                下一页
                <ArrowRight size={14} />
              </button>
            </nav>
          </>
        ) : (
          <div className="journal-loading">
            <h2>
              {q || category
                ? "暂时没有找到相关文章"
                : "第一篇创作手记，正在准备中。"}
            </h2>
            <p>
              {q || category
                ? "试试其他关键词，或看看所有文章。"
                : "关于创作与产品的思考，会陆续在这里与你见面。"}
            </p>
            {q || category ? (
              <button onClick={() => setParams({})}>查看全部文章</button>
            ) : (
              <Link to="/">先去探索工作台 →</Link>
            )}
          </div>
        )}
      </main>
    </BlogFrame>
  );
}
