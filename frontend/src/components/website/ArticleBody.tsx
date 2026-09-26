import { useMemo } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import { unified } from "unified";
import { BlogCover, articleDate, type ArticleContent } from "./BlogShared";

export function ArticleBody({
  article,
  date,
  preview = false,
}: {
  article: ArticleContent;
  date?: string;
  preview?: boolean;
}) {
  const headings = useMemo(() => {
    const tree = unified().use(remarkParse).use(remarkGfm).parse(article.body);
    type TextNode = { type: string; value?: string; children?: TextNode[] };
    const text = (node: TextNode): string =>
      node.value || node.children?.map(text).join("") || "";
    return tree.children
      .filter((node) => node.type === "heading" && node.depth === 2)
      .map((node) => ({
        text: text(node),
        id: `section-${node.position?.start.line}`,
      }));
  }, [article.body]);
  return (
    <article className="journal-article">
      <header className="journal-article-heading">
        <div className="journal-meta">
          <span>{article.category}</span>
          <span>{preview ? "草稿预览 · 尚未发布" : articleDate(date)}</span>
        </div>
        <h1>{article.title}</h1>
        <p>{article.excerpt}</p>
        <div className="journal-byline">
          <span className="journal-author-mark">I</span>
          <span>
            {article.author}
            <small>
              约 {Math.max(1, Math.ceil(article.body.length / 650))} 分钟阅读
            </small>
          </span>
        </div>
      </header>
      {article.cover_media_id && (
        <div className="journal-article-cover">
          <BlogCover article={article} preview={preview} />
        </div>
      )}
      <div className="journal-reading-layout">
        <aside className="journal-toc">
          <p>本文目录</p>
          {headings.length ? (
            headings.map((h) => (
              <a key={h.id} href={`#${h.id}`}>
                {h.text}
              </a>
            ))
          ) : (
            <span>从这里，开始阅读。</span>
          )}
          <span className="journal-toc-end">READ. THINK. CREATE.</span>
        </aside>
        <div className="journal-prose">
          <Markdown
            remarkPlugins={[remarkGfm]}
            skipHtml
            components={{
              h2: ({ children, node }) => (
                <h2 id={`section-${node?.position?.start.line}`}>{children}</h2>
              ),
              a: ({ href, children }) => (
                <a
                  href={href}
                  target={href?.startsWith("http") ? "_blank" : undefined}
                  rel="noopener noreferrer"
                >
                  {children}
                </a>
              ),
              img: ({ alt }) => (
                <span className="journal-image-note">
                  {alt ? `图片说明：${alt}` : "图片"}
                </span>
              ),
            }}
          >
            {article.body}
          </Markdown>
          <div className="journal-endmark">
            <span />
            END OF NOTE
            <span />
          </div>
        </div>
      </div>
    </article>
  );
}
