import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useState, type ReactNode } from "react";
import { SkillOverlay } from "@/components/SkillOverlay";
import {
  FileText,
  FolderOpen,
  Search,
  Download,
  Files,
  ChevronRight,
  BookOpen,
} from "lucide-react";
import { getToken } from "@/lib/api";
import type { Skill, SkillFile } from "@/lib/skills";
import { categories } from "@/lib/skills";
import { Button } from "@/components/ui/button";

const field =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm";
function downloadFile(file: SkillFile) {
  const bytes =
    file.encoding === "base64"
      ? Uint8Array.from(atob(file.content), (c) => c.charCodeAt(0))
      : file.content;
  const url = URL.createObjectURL(
    new Blob([bytes], { type: "application/octet-stream" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = file.path.split("/").pop()!;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function SkillReader({
  skill,
  onClose,
  actions,
  feedback,
}: {
  skill: Skill | null;
  onClose: () => void;
  actions?: ReactNode;
  feedback?: ReactNode;
}) {
  const [path, setPath] = useState("SKILL.md"),
    [source, setSource] = useState(false),
    [filter, setFilter] = useState(""),
    [filesOpen, setFilesOpen] = useState(false);
  const files = skill
    ? [
        {
          path: "SKILL.md",
          content: skill.instructions,
          encoding: "utf-8" as const,
        },
        ...(skill.files ?? []),
      ]
    : [];
  const file = files.find((f) => f.path === path) ?? files[0];
  const visible = files.filter((f) =>
    f.path.toLowerCase().includes(filter.toLowerCase()),
  );
  const directories = Array.from(
    new Set(
      visible.map((f) =>
        f.path.includes("/")
          ? f.path.substring(0, f.path.lastIndexOf("/"))
          : "",
      ),
    ),
  );
  function chooseFile(path: string) {
    setPath(path);
    setSource(false);
    setFilesOpen(false);
  }
  return (
    <SkillOverlay
      open={!!skill}
      title={skill?.name ?? "技能详情"}
      subtitle={
        skill ? (
          <>
            {categories[skill.category ?? "general"]}
            <ChevronRight size={11} />
            {skill.id ? `版本 ${skill.revision}` : "方法模板"}
          </>
        ) : undefined
      }
      onClose={onClose}
      actions={actions}
    >
      {skill && (
        <>
          <div className="knowledge-reader-intro">
            <p>{skill.description}</p>
            <span>{skill.source}</span>
          </div>
          <div className="knowledge-reader-notices">{feedback}</div>
          <div
            className={`knowledge-reader-workspace ${filesOpen ? "show-files" : ""}`}
          >
            <aside className="knowledge-file-sidebar" aria-label="技能文件">
              <div className="knowledge-files-heading">
                <Files size={15} />
                <strong>技能文件</strong>
                <span>{files.length}</span>
              </div>
              <label className="knowledge-file-search">
                <Search size={14} />
                <input
                  aria-label="筛选技能文件"
                  placeholder="筛选文件…"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                />
              </label>
              <nav className="knowledge-file-tree">
                {directories.map((dir) => (
                  <div key={dir}>
                    {dir && (
                      <div className="knowledge-file-folder">
                        <FolderOpen size={14} />
                        {dir}
                      </div>
                    )}
                    {visible
                      .filter(
                        (f) =>
                          (f.path.includes("/")
                            ? f.path.substring(0, f.path.lastIndexOf("/"))
                            : "") === dir,
                      )
                      .map((f) => (
                        <button
                          key={f.path}
                          className={`${file.path === f.path ? "is-active" : ""} ${dir ? "is-child" : ""}`}
                          onClick={() => chooseFile(f.path)}
                          aria-label={f.path}
                          aria-current={
                            file.path === f.path ? "page" : undefined
                          }
                        >
                          <FileText size={15} />
                          <span>{f.path.split("/").pop()}</span>
                          {f.path === "SKILL.md" && <small>入口</small>}
                        </button>
                      ))}
                  </div>
                ))}
                {!visible.length && (
                  <p className="knowledge-empty-note">没有匹配的文件</p>
                )}
              </nav>
              <div className="knowledge-files-note">
                <BookOpen size={14} />
                <span>从 SKILL.md 开始阅读，参考文件补充具体方法。</span>
              </div>
            </aside>
            <section className="knowledge-reader-pane">
              <div className="knowledge-file-toolbar">
                <div className="knowledge-file-breadcrumb">
                  <button
                    className="knowledge-mobile-files"
                    aria-label="切换文件目录"
                    aria-expanded={filesOpen}
                    onClick={() => setFilesOpen(!filesOpen)}
                  >
                    <Files size={17} />
                  </button>
                  <FileText size={15} />
                  <span>{file.path}</span>
                </div>
                <div className="knowledge-file-controls">
                  <div role="group" aria-label="文件阅读方式">
                    <button
                      aria-pressed={!source}
                      onClick={() => setSource(false)}
                    >
                      阅读
                    </button>
                    <button
                      aria-pressed={source}
                      onClick={() => setSource(true)}
                    >
                      源码
                    </button>
                  </div>
                  <button
                    aria-label="下载文件"
                    title="下载文件"
                    className="knowledge-icon-button"
                    onClick={() => downloadFile(file)}
                  >
                    <Download size={16} />
                  </button>
                </div>
              </div>
              <div className="knowledge-file-body">
                {file.encoding === "base64" ? (
                  <div className="knowledge-binary">
                    <FileText size={35} />
                    <h3>{file.path.split("/").pop()}</h3>
                    <p>这个附件可下载后查看，原文件保留在技能包中。</p>
                    <Button
                      variant="outline"
                      onClick={() => downloadFile(file)}
                    >
                      <Download size={15} />
                      下载附件
                    </Button>
                  </div>
                ) : source ? (
                  <pre className="knowledge-source">{file.content}</pre>
                ) : (
                  <article className="knowledge-markdown">
                    {" "}
                    <Markdown
                      skipHtml
                      remarkPlugins={[remarkGfm]}
                      components={{
                        h1: ({ children }) => (
                          <h1 className="text-2xl font-semibold my-4">
                            {children}
                          </h1>
                        ),
                        h2: ({ children }) => (
                          <h2 className="text-xl font-semibold mt-5 mb-3">
                            {children}
                          </h2>
                        ),
                        h3: ({ children }) => (
                          <h3 className="text-lg font-semibold mt-4 mb-2">
                            {children}
                          </h3>
                        ),
                        p: ({ children }) => <p className="my-3">{children}</p>,
                        ul: ({ children }) => (
                          <ul className="list-disc pl-5 my-3">{children}</ul>
                        ),
                        ol: ({ children }) => (
                          <ol className="list-decimal pl-5 my-3">{children}</ol>
                        ),
                        blockquote: ({ children }) => (
                          <blockquote className="border-l-2 border-primary/40 pl-4 text-muted-foreground">
                            {children}
                          </blockquote>
                        ),
                        pre: ({ children }) => (
                          <pre className="overflow-auto rounded-lg bg-background p-3 text-xs leading-6 my-3">
                            {children}
                          </pre>
                        ),
                        table: ({ children }) => (
                          <div className="overflow-auto">
                            <table className="w-full border-collapse text-xs">
                              {children}
                            </table>
                          </div>
                        ),
                        th: ({ children }) => (
                          <th className="border border-border p-2 text-left">
                            {children}
                          </th>
                        ),
                        td: ({ children }) => (
                          <td className="border border-border p-2">
                            {children}
                          </td>
                        ),
                        img: ({ alt }) => (
                          <span className="text-muted-foreground">
                            [图片：{alt ?? "附件"}，请在文件列表查看]
                          </span>
                        ),
                        a: ({ href, children }) => {
                          let linked: string | undefined;
                          try {
                            const url = new URL(
                              href ?? "",
                              `https://skill.local/${file.path}`,
                            );
                            if (url.origin === "https://skill.local")
                              linked = decodeURIComponent(
                                url.pathname.slice(1),
                              );
                          } catch {
                            /* display plain text */
                          }
                          if (linked && files.some((f) => f.path === linked))
                            return (
                              <button
                                className="text-primary underline"
                                onClick={() => chooseFile(linked!)}
                              >
                                {children}
                              </button>
                            );
                          return href && /^https?:\/\//i.test(href) ? (
                            <a
                              className="text-primary underline"
                              href={href}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              {children}
                            </a>
                          ) : (
                            <span>{children}</span>
                          );
                        },
                      }}
                    >
                      {file.path.toLowerCase().endsWith(".md")
                        ? file.content.replace(
                            /^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/,
                            "",
                          )
                        : "```\n" + file.content + "\n```"}
                    </Markdown>
                  </article>
                )}
              </div>
              <footer className="knowledge-reader-footnote">
                <span>
                  {file.encoding === "utf-8"
                    ? `${file.content.length.toLocaleString()} 字符 · UTF-8`
                    : "二进制附件"}
                </span>
                <span>
                  {skill.source_metadata?.commit
                    ? `来源提交 ${skill.source_metadata.commit.slice(0, 12)}`
                    : skill.required_tools.length
                      ? `${skill.required_tools.length} 项工具依赖`
                      : "创作方法参考"}
                </span>
              </footer>
            </section>
          </div>
        </>
      )}
    </SkillOverlay>
  );
}

export function SkillFilesEditor({
  skill,
  onChange,
  disabled,
  run,
}: {
  skill: Skill;
  onChange: (change: Partial<Skill>) => void;
  disabled: boolean;
  run: (fn: () => Promise<void>) => Promise<void>;
}) {
  const [active, setActive] = useState(""),
    [error, setError] = useState("");
  const files = skill.files ?? [],
    file = files.find((f) => f.path === active);
  return (
    <div className="space-y-3">
      <label className="block text-xs">
        技能分类
        <select
          className={`${field} mt-2`}
          value={skill.category ?? "general"}
          onChange={(e) => onChange({ category: e.target.value })}
        >
          {Object.entries(categories).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      <div className="flex justify-between gap-2 text-sm">
        <span>配套文件 · {files.length}/40</span>
        <label className="cursor-pointer text-primary">
          添加文件
          <input
            className="sr-only"
            aria-label="添加技能配套文件"
            type="file"
            multiple
            disabled={disabled}
            onChange={async (e) => {
              const selected = Array.from(e.target.files ?? []);
              e.target.value = "";
              await run(async () => {
                setError("");
                try {
                  if (files.length + selected.length > 40)
                    throw new Error("最多40个配套文件");
                  const added: SkillFile[] = [];
                  for (const f of selected) {
                    if (f.size > 1000000)
                      throw new Error("单个文件不能超过1MB");
                    if (
                      f.name.toLowerCase() === "skill.md" ||
                      [...files, ...added].some(
                        (v) => v.path.toLowerCase() === f.name.toLowerCase(),
                      )
                    )
                      throw new Error("文件名重复，请先重命名文件");
                    const bytes = new Uint8Array(await f.arrayBuffer());
                    let content: string,
                      encoding: SkillFile["encoding"] = "utf-8";
                    try {
                      content = new TextDecoder("utf-8", {
                        fatal: true,
                      }).decode(bytes);
                      if (content.includes("\0")) throw new Error();
                    } catch {
                      encoding = "base64";
                      content = btoa(
                        Array.from(bytes, (b) => String.fromCharCode(b)).join(
                          "",
                        ),
                      );
                    }
                    added.push({ path: f.name, content, encoding });
                  }
                  onChange({ files: [...files, ...added] });
                } catch (err) {
                  setError((err as Error).message);
                }
              });
            }}
          />
        </label>
      </div>
      <p className="text-xs text-muted-foreground">
        在正文中写明参考文件路径，创作时会带入相关文本。scripts/ 与 assets/
        文件仅保留供下载。
      </p>
      <div className="flex flex-wrap gap-2">
        {files.map((f) => (
          <button
            type="button"
            key={f.path}
            className={`rounded border px-2 py-1 text-xs ${active === f.path ? "border-primary" : "border-border"}`}
            onClick={() => setActive(f.path)}
          >
            {f.path}
          </button>
        ))}
      </div>
      {file && (
        <div className="space-y-2">
          <label className="block text-xs">
            文件路径
            <input
              aria-label="配套文件路径"
              className={field}
              value={file.path}
              onChange={(e) => {
                const path = e.target.value;
                onChange({
                  files: files.map((f) => (f === file ? { ...f, path } : f)),
                });
                setActive(path);
              }}
            />
          </label>
          {file.encoding === "utf-8" ? (
            <textarea
              aria-label="配套文件内容"
              className={`${field} min-h-40 font-mono`}
              value={file.content}
              onChange={(e) =>
                onChange({
                  files: files.map((f) =>
                    f === file ? { ...f, content: e.target.value } : f,
                  ),
                })
              }
            />
          ) : (
            <p className="text-xs">二进制附件 · 保留原文件内容</p>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              onChange({ files: files.filter((f) => f !== file) });
              setActive("");
            }}
          >
            移除此文件
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

export async function exportSkill(base: string, id: string) {
  const r = await fetch(`/api/v1${base}/skills/${id}/bundle`, {
    headers: { Authorization: `Bearer ${getToken() ?? ""}` },
  });
  if (!r.ok) throw new Error("技能包下载失败，请重试");
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = "skill-package.zip";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
