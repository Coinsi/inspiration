import { useRef, useState } from "react";
import {
  FileArchive,
  FileText,
  Github,
  UploadCloud,
  Check,
  LoaderCircle,
  Plus,
  X,
} from "lucide-react";
import { api, apiUpload } from "@/lib/api";
import { categories, type Skill } from "@/lib/skills";
import { Button } from "@/components/ui/button";
import { SkillOverlay } from "@/components/SkillOverlay";

const modes = [
  { id: "markdown", label: "Markdown", icon: FileText },
  { id: "zip", label: "ZIP 技能包", icon: FileArchive },
  { id: "github", label: "GitHub", icon: Github },
] as const;
type Mode = (typeof modes)[number]["id"];

export function SkillInstallCard({
  disabled,
  onClick,
}: {
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className="knowledge-import-card"
      disabled={disabled}
      onClick={onClick}
      aria-label="安装技能"
    >
      <span>
        <Plus size={28} strokeWidth={1.4} />
      </span>
      <strong>安装技能</strong>
      <p>Markdown、ZIP 技能包或 GitHub</p>
      <small>把现成的创作方法加入技能库</small>
    </button>
  );
}

// Mounted per opening, so files and successful request keys never leak into a new installation.
export function SkillInstall({
  base,
  onClose,
  onInstalled,
  onManualCreate,
}: {
  base: string;
  onClose: () => void;
  onInstalled: (skill: Skill) => void;
  onManualCreate: () => void;
}) {
  const [mode, setMode] = useState<Mode>("markdown");
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState(""),
    [ref, setRef] = useState(""),
    [directory, setDirectory] = useState("");
  const [preview, setPreview] = useState<Skill | null>(null);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [installing, setInstalling] = useState(false);
  const [dragging, setDragging] = useState(false);
  const busyRef = useRef(false),
    requestKey = useRef(crypto.randomUUID());
  const pending = useRef<{ request_key: string; document: Skill } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  function resetPreview() {
    setPreview(null);
    setError("");
    requestKey.current = crypto.randomUUID();
    pending.current = null;
  }
  function changeMode(next: Mode) {
    if (busyRef.current || pending.current) return;
    setMode(next);
    setFile(null);
    resetPreview();
  }
  function chooseFile(next: File) {
    if (busyRef.current || pending.current) return;
    resetPreview();
    setFile(null);
    if (
      !(mode === "zip" ? /\.zip$/i : /\.(md|markdown|txt)$/i).test(next.name)
    ) {
      setError(
        mode === "zip" ? "请选择 ZIP 技能包" : "请选择 Markdown 或 TXT 文件",
      );
      return;
    }
    if (next.size > 32_000_000) {
      setError("技能文件不能超过 32MB");
      return;
    }
    setFile(next);
  }
  async function readPackage() {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      let doc: Skill;
      if (mode === "github")
        doc = await api.post<Skill>(`${base}/skills/github-preview`, {
          url: url.trim(),
          ref: ref.trim(),
          directory: directory.trim(),
        });
      else {
        if (!file) throw new Error("请先选择技能文件");
        const body = new FormData();
        body.append("file", file);
        doc = await apiUpload<Skill>(`${base}/skills/import-preview`, body);
      }
      setPreview(doc);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function install() {
    if (busyRef.current || !preview) return;
    busyRef.current = true;
    setBusy(true);
    setInstalling(true);
    setError("");
    pending.current ??= {
      request_key: requestKey.current,
      document: {
        ...preview,
        name: preview.name.trim(),
        description: preview.description.trim(),
      },
    };
    try {
      const result = await api.post<Skill>(
        `${base}/skills/install`,
        pending.current,
      );
      onInstalled(result);
    } catch (e) {
      setError(
        `${(e as Error).message}。可以重试安装，同一次操作不会重复添加。`,
      );
    } finally {
      busyRef.current = false;
      setBusy(false);
      setInstalling(false);
    }
  }
  const locked = busy || !!pending.current;
  return (
    <div className="knowledge-install">
      <SkillOverlay
        className="knowledge-install"
        open
        title="安装技能"
        subtitle="让好方法成为你的创作工具"
        onClose={onClose}
        dismissDisabled={busy}
      >
        <div className="knowledge-install-body">
          <p className="knowledge-install-lead">
            上传技能文件或连接
            GitHub，自动读取名称、简介和配套文件，确认后即可在创作中使用。
          </p>
          <div
            className="knowledge-install-modes"
            role="tablist"
            aria-label="技能安装方式"
          >
            {modes.map(({ id, label, icon: Icon }, i) => (
              <button
                key={id}
                role="tab"
                aria-selected={mode === id}
                tabIndex={mode === id ? 0 : -1}
                disabled={locked}
                onClick={() => changeMode(id)}
                onKeyDown={(e) => {
                  if (
                    !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)
                  )
                    return;
                  e.preventDefault();
                  const index =
                    e.key === "Home"
                      ? 0
                      : e.key === "End"
                        ? 2
                        : (i + (e.key === "ArrowRight" ? 1 : -1) + 3) % 3;
                  changeMode(modes[index].id);
                  (
                    e.currentTarget.parentElement?.children[
                      index
                    ] as HTMLElement
                  )?.focus();
                }}
              >
                <Icon size={16} />
                {label}
              </button>
            ))}
          </div>
          <fieldset disabled={locked} className="knowledge-install-source">
            {mode === "github" ? (
              <>
                <label>
                  GitHub 地址
                  <input
                    type="url"
                    aria-label="GitHub 地址"
                    value={url}
                    placeholder="https://github.com/作者/仓库"
                    maxLength={1000}
                    onChange={(e) => {
                      setUrl(e.target.value);
                      resetPreview();
                    }}
                  />
                </label>
                <div className="knowledge-install-fields">
                  <label>
                    分支或标签
                    <input
                      value={ref}
                      maxLength={200}
                      placeholder="默认分支"
                      onChange={(e) => {
                        setRef(e.target.value);
                        resetPreview();
                      }}
                    />
                  </label>
                  <label>
                    技能子目录
                    <input
                      value={directory}
                      maxLength={240}
                      placeholder="skills/ai-director"
                      onChange={(e) => {
                        setDirectory(e.target.value);
                        resetPreview();
                      }}
                    />
                  </label>
                </div>
                <p className="knowledge-install-hint">
                  支持公开仓库；仓库只有一个技能时，子目录可留空。
                </p>
              </>
            ) : (
              <>
                <input
                  ref={fileInput}
                  className="sr-only"
                  tabIndex={-1}
                  type="file"
                  aria-label="选择技能安装文件"
                  accept={mode === "zip" ? ".zip" : ".md,.markdown,.txt"}
                  onChange={(e) => {
                    const next = e.target.files?.[0];
                    e.target.value = "";
                    if (next) chooseFile(next);
                  }}
                />
                <button
                  type="button"
                  className={`knowledge-install-drop ${dragging ? "is-dragging" : ""}`}
                  onClick={() => fileInput.current?.click()}
                  onDragOver={(e) => {
                    e.preventDefault();
                    if (!locked) setDragging(true);
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setDragging(false);
                    if (e.dataTransfer.files.length !== 1) {
                      setError("每次请选择一个技能文件");
                      return;
                    }
                    chooseFile(e.dataTransfer.files[0]);
                  }}
                >
                  {file ? (
                    <Check size={28} />
                  ) : (
                    <UploadCloud size={30} strokeWidth={1.5} />
                  )}
                  <strong>
                    {file
                      ? file.name
                      : `拖入或选择 ${mode === "zip" ? "ZIP 技能包" : "Markdown 文件"}`}
                  </strong>
                  <span>
                    {file
                      ? `${(file.size / 1024).toFixed(1)} KB · 点击重新选择`
                      : mode === "zip"
                        ? "包含 SKILL.md 及 references、assets 等配套文件"
                        : "支持 SKILL.md 和普通 Markdown 文件"}
                  </span>
                </button>
                {file && (
                  <button
                    className="knowledge-install-remove"
                    onClick={() => {
                      setFile(null);
                      resetPreview();
                    }}
                  >
                    <X size={12} />
                    移除文件
                  </button>
                )}
              </>
            )}
          </fieldset>
          {preview && (
            <section
              className="knowledge-install-preview"
              aria-label="待安装技能"
            >
              <div className="knowledge-install-ready">
                <Check size={15} />
                已识别技能 · {1 + (preview.files?.length ?? 0)} 个文件
              </div>
              <fieldset disabled={locked} className="knowledge-install-fields">
                <label>
                  技能名称
                  <input
                    aria-label="安装技能名称"
                    value={preview.name}
                    maxLength={120}
                    onChange={(e) =>
                      setPreview({ ...preview, name: e.target.value })
                    }
                  />
                </label>
                <label>
                  技能分类
                  <select
                    aria-label="技能分类"
                    value={preview.category ?? "general"}
                    onChange={(e) =>
                      setPreview({ ...preview, category: e.target.value })
                    }
                  >
                    {Object.entries(categories).map(([key, name]) => (
                      <option key={key} value={key}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="is-wide">
                  技能简介
                  <textarea
                    aria-label="安装技能简介"
                    value={preview.description}
                    rows={2}
                    maxLength={1000}
                    onChange={(e) =>
                      setPreview({ ...preview, description: e.target.value })
                    }
                  />
                </label>
              </fieldset>
              <details>
                <summary>查看技能内容与文件</summary>
                <pre>{preview.instructions}</pre>
                <ul>
                  <li>SKILL.md</li>
                  {preview.files?.map((f) => (
                    <li key={f.path}>{f.path}</li>
                  ))}
                </ul>
              </details>
              {preview.source_metadata?.commit && (
                <p className="knowledge-install-hint">
                  来源版本 {preview.source_metadata.commit.slice(0, 12)} ·
                  安装后可手动检查更新
                </p>
              )}
            </section>
          )}
          {error && (
            <p role="alert" className="knowledge-notice is-error">
              {error}
            </p>
          )}
          <p className="knowledge-install-hint">
            安装到当前项目，项目成员可在创作中选用。配套文件会一并保存。
          </p>
        </div>
        <footer className="knowledge-install-footer">
          <button
            className="knowledge-install-manual"
            disabled={busy}
            onClick={onManualCreate}
          >
            从空白创建技能
          </button>
          <div>
            <Button variant="ghost" disabled={busy} onClick={onClose}>
              取消
            </Button>
            {preview ? (
              <Button
                disabled={
                  busy || !preview.name.trim() || !preview.description.trim()
                }
                onClick={() => void install()}
              >
                {installing ? (
                  <LoaderCircle size={15} className="animate-spin" />
                ) : (
                  <Plus size={15} />
                )}
                {installing
                  ? "正在安装…"
                  : pending.current
                    ? "重试安装"
                    : "确认安装"}
              </Button>
            ) : (
              <Button
                disabled={busy || (mode === "github" ? !url.trim() : !file)}
                onClick={() => void readPackage()}
              >
                {busy && <LoaderCircle size={15} className="animate-spin" />}
                {busy ? "正在识别…" : "读取技能"}
              </Button>
            )}
          </div>
        </footer>
      </SkillOverlay>
    </div>
  );
}
