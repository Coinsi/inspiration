import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ArrowUpRight, BookOpen } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { SiteMedia } from "./SiteMedia";
import { GitHubLink } from "./GitHubLink";
import "./landing.css";
import "./blog.css";

export const categories = ["创作指南", "产品更新", "灵感笔记"] as const;
export interface ArticleContent {
  title: string;
  excerpt: string;
  body: string;
  category: string;
  author: string;
  cover_media_id: string | null;
}
export interface PublicArticle extends ArticleContent {
  slug: string;
  published_at: string;
  reading_minutes: number;
}
export interface BlogList {
  items: PublicArticle[];
  total: number;
}
export const articleDate = (value?: string | null) =>
  value
    ? new Date(value).toLocaleDateString("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      })
    : "草稿";

export function BlogCover({
  article,
  preview = false,
}: {
  article: ArticleContent;
  preview?: boolean;
}) {
  if (article.cover_media_id)
    return (
      <SiteMedia
        id={article.cover_media_id}
        mime="image/jpeg"
        alt={article.title}
        preview={preview}
      />
    );
  return (
    <div
      className={`journal-art journal-art-${categories.indexOf(article.category as (typeof categories)[number])}`}
      aria-hidden="true"
    >
      <span>INSPIRATION / JOURNAL</span>
      <div className="journal-art-sheets">
        <i />
        <i />
        <i>
          <BookOpen strokeWidth={1} size={42} />
          <b>{article.category}</b>
          <small>STORIES IN THE MAKING</small>
        </i>
      </div>
      <em>每一个想法，都值得被认真记录。</em>
    </div>
  );
}

export function BlogCard({
  article,
  featured = false,
}: {
  article: PublicArticle;
  featured?: boolean;
}) {
  return (
    <Link
      className={`journal-card ${featured ? "journal-card-featured" : ""}`}
      to={`/blog/${article.slug}`}
    >
      <div className="journal-card-cover">
        <BlogCover article={article} />
        <span className="journal-card-arrow">
          <ArrowUpRight size={20} />
        </span>
      </div>
      <div className="journal-card-copy">
        <div className="journal-meta">
          <span>{article.category}</span>
          <time dateTime={article.published_at}>
            {articleDate(article.published_at)}
          </time>
        </div>
        <h2>{article.title}</h2>
        <p>{article.excerpt}</p>
        <div className="journal-card-bottom">
          <span>
            {article.author} · 约 {article.reading_minutes} 分钟
          </span>
          <span>
            阅读全文 <ArrowRight size={14} />
          </span>
        </div>
      </div>
    </Link>
  );
}

export function BlogFrame({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  const { data } = useQuery({
    queryKey: ["public-website"],
    queryFn: () =>
      api.get<{ content: { brand: string; footer: string } }>("/website"),
  });
  const brand = data?.content.brand || "Inspiration";
  return (
    <div className="site-root journal-root">
      <header className="site-nav">
        <div className="site-nav-inner">
          <Link to="/" className="site-brand">
            <span className="site-mark">
              <i />
              <i />
              <i />
            </span>
            {brand}
          </Link>
          <div className="journal-nav">
            <Link to="/">产品官网</Link>
            <Link to="/blog" aria-current="page">
              博客
            </Link>
          </div>
          <Link
            className="site-button small"
            to={me ? "/projects" : "/register"}
          >
            {me ? "进入工作台" : "开始创作"}
            <ArrowUpRight size={14} />
          </Link>
        </div>
      </header>
      {children}
      <footer className="journal-footer site-contained">
        <Link to="/" className="site-brand">
          {brand}
        </Link>
        <p>
          {data?.content.footer || "为认真创作的人，留一个让灵感生长的地方。"}
        </p>
        <div>
          <span>
            © {new Date().getFullYear()} {brand}
          </span>
          <Link to="/blog">创作手记</Link>
          <GitHubLink />
          <Link to="/">
            返回官网 <ArrowUpRight size={13} />
          </Link>
        </div>
      </footer>
    </div>
  );
}

export function JournalTeaser() {
  const query = useQuery({
    queryKey: ["blog-home"],
    queryFn: () => api.get<BlogList>("/blog?limit=3"),
  });
  return (
    <section
      className="site-section site-contained journal-teaser"
      id="journal"
    >
      <div className="site-section-heading">
        <div>
          <p className="site-eyebrow">JOURNAL / NOTES FROM THE STUDIO</p>
          <h2>
            关于创作，
            <br />
            还有一些想与你分享。
          </h2>
        </div>
        <Link className="site-text-link" to="/blog">
          阅读全部文章 <ArrowRight size={16} />
        </Link>
      </div>
      {query.data?.items.length ? (
        <div className="journal-home-grid">
          {query.data.items.map((a) => (
            <BlogCard article={a} key={a.slug} />
          ))}
        </div>
      ) : (
        <div className="journal-empty-inline">
          <BookOpen size={26} strokeWidth={1} />
          <p>
            {query.error
              ? "文章暂时未能加载，可以前往博客重试。"
              : "创作指南、产品进展与灵感笔记，将在这里陆续分享。"}
          </p>
          <Link to="/blog">
            进入创作手记 <ArrowRight size={14} />
          </Link>
        </div>
      )}
    </section>
  );
}
