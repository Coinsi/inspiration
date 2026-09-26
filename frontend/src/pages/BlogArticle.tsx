import { useEffect } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowUpRight } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { BlogFrame, type PublicArticle } from "@/components/website/BlogShared";
import { ArticleBody } from "@/components/website/ArticleBody";

export default function BlogArticle() {
  const { slug } = useParams();
  const query = useQuery({
    queryKey: ["blog-article", slug],
    queryFn: () =>
      api.get<PublicArticle>(`/blog/${encodeURIComponent(slug || "")}`),
    retry: false,
  });
  useEffect(() => {
    const old = document.title;
    if (query.data) document.title = `${query.data.title} · Inspiration`;
    return () => {
      document.title = old;
    };
  }, [query.data]);
  return (
    <BlogFrame>
      <main className="site-contained">
        <div className="journal-breadcrumb">
          <Link to="/blog">
            <ArrowLeft size={14} />
            所有文章
          </Link>
          <span>INSPIRATION / JOURNAL</span>
        </div>
        {query.isLoading ? (
          <div className="journal-loading" role="status">
            正在载入文章…
          </div>
        ) : query.error ? (
          <div className="journal-loading" role="alert">
            <h1>
              {query.error instanceof ApiError &&
              query.error.code === "NOT_FOUND"
                ? "这篇文章暂未公开"
                : "暂时无法打开这篇文章"}
            </h1>
            <p>文章可能尚未发布、已经下线，或网络暂时不可用。</p>
            <button onClick={() => void query.refetch()}>重新加载</button>
            <Link to="/blog">返回博客</Link>
          </div>
        ) : query.data ? (
          <>
            <ArticleBody article={query.data} date={query.data.published_at} />
            <div className="journal-reader-next">
              <div>
                <span className="site-eyebrow">TAKE THE NEXT STEP</span>
                <h2>让读到的灵感，成为下一次创作。</h2>
              </div>
              <Link className="site-text-link" to="/projects">
                回到工作台
                <ArrowUpRight size={17} />
              </Link>
              <Link to="/blog">继续阅读 →</Link>
            </div>
          </>
        ) : null}
      </main>
    </BlogFrame>
  );
}
