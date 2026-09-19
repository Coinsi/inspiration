import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookMarked,
  ImagePlus,
  Loader2,
  Pencil,
  Plus,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { useConfirm } from "@/components/ui/confirm";
import { useToast } from "@/components/ui/toast";
import { useI18n } from "@/lib/i18n";
import { useAuth } from "@/lib/auth";
import { usePersistentState } from "@/lib/usePersistentState";
import {
  api,
  SETTING_CATEGORIES,
  type Novel,
  type Setting,
  type SettingExtraction,
} from "@/lib/api";
import "./story.css";

const TO_ASSET_CATEGORIES = new Set(["character", "location", "item"]);
export default function StoryBible() {
  const { projectId } = useParams();
  return <StoryBibleWorkspace key={projectId} />;
}
function StoryBibleWorkspace() {
  const { projectId } = useParams();
  const base = `/projects/${projectId}`,
    qc = useQueryClient();
  const toast = useToast(),
    confirm = useConfirm(),
    { t: tr } = useI18n();
  const { me } = useAuth();
  const role =
    me?.memberships.find((m) => m.project_id === projectId)?.role ?? "";
  const canEdit = ["admin", "director", "writer"].includes(role);
  const canAsset = ["admin", "director", "artist"].includes(role);
  const [novelId, setNovelId] = usePersistentState<string | null>(
    `bible.novel.${projectId}`,
    null,
  );
  const [category, setCategory] = useState("");
  const [q, setQ] = useState(""),
    [chFilter, setChFilter] = useState("");
  const [editor, setEditor] = useState<Setting | "new" | null>(null);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [extractOpen, setExtractOpen] = useState(false);
  const [fromCh, setFromCh] = useState(""),
    [toCh, setToCh] = useState("");
  const [concurrency, setConcurrency] = usePersistentState<number>(
    "bible.concurrency",
    4,
  );
  const novels = useQuery({
    queryKey: ["novels", projectId],
    queryFn: () => api.get<Novel[]>(`${base}/novels`),
  });
  const catalog = useQuery({
    queryKey: ["settings", projectId, novelId],
    queryFn: () =>
      api.get<Setting[]>(
        `${base}/settings${novelId ? `?novel_id=${novelId}` : ""}`,
      ),
  });
  const settings = catalog.data;
  const latest = useQuery({
    queryKey: ["bible-job", projectId, novelId],
    queryFn: () =>
      api.get<SettingExtraction | null>(
        `${base}/novels/${novelId}/extract-settings/latest`,
      ),
    enabled: !!novelId,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 1500 : false,
  });
  const job = latest.data,
    running = job?.status === "running";
  useEffect(() => {
    void qc.invalidateQueries({ queryKey: ["settings", projectId] });
  }, [job?.done_chapters, job?.status, qc, projectId]);
  const startExtract = useMutation({
    mutationFn: () =>
      api.post<SettingExtraction>(
        `${base}/novels/${novelId}/extract-settings`,
        {
          from_chapter: fromCh ? Number(fromCh) : null,
          to_chapter: toCh ? Number(toCh) : null,
          concurrency,
        },
      ),
    onSuccess: () => {
      toast.push(tr("bible.started"), "success");
      void qc.invalidateQueries({ queryKey: ["bible-job", projectId] });
    },
    onError: (e) => toast.push(e.message, "error"),
  });
  const cancelExtract = useMutation({
    mutationFn: () => api.post(`${base}/setting-extractions/${job!.id}/cancel`),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["bible-job", projectId] }),
    onError: (e) => toast.push(e.message, "error"),
  });
  const invalidate = () =>
    void qc.invalidateQueries({ queryKey: ["settings", projectId] });
  const del = useMutation({
    mutationFn: (id: string) => api.del(`${base}/settings/${id}`),
    onSuccess: invalidate,
    onError: (e) => toast.push(e.message, "error"),
  });
  const toAsset = useMutation({
    mutationFn: (id: string) => api.post(`${base}/settings/${id}/to-asset`),
    onSuccess: () => {
      toast.push(tr("bible.assetCreated"), "success");
      void qc.invalidateQueries({ queryKey: ["assets", projectId] });
    },
    onError: (e) => toast.push(e.message, "error"),
  });
  const categories = useMemo(
    () => [
      ...new Set<string>([
        ...SETTING_CATEGORIES,
        ...(settings ?? []).map((s) => s.category),
      ]),
    ],
    [settings],
  );
  const catLabel = (c: string) => {
    const value = tr(`bible.cat.${c}`);
    return value === `bible.cat.${c}` ? c : value;
  };
  const range = chFilter.trim().match(/^(\d+)(?:\s*[-~]\s*(\d+))?$/);
  const filterInvalid =
    !!chFilter.trim() &&
    (!range ||
      Number(range[1]) < 1 ||
      Number(range[2] ?? range[1]) < Number(range[1]));
  const shown = (settings ?? []).filter(
    (s) =>
      (!category || s.category === category) &&
      `${s.name} ${s.content}`
        .toLocaleLowerCase()
        .includes(q.trim().toLocaleLowerCase()) &&
      !filterInvalid &&
      (!range ||
        s.source_chapters.some(
          (n) => n >= Number(range[1]) && n <= Number(range[2] ?? range[1]),
        )),
  );
  const invalidRange =
    [fromCh, toCh].some(
      (v) => v !== "" && (!Number.isInteger(Number(v)) || Number(v) < 1),
    ) ||
    (!!fromCh && !!toCh && Number(fromCh) > Number(toCh));
  const clearFilters = () => {
    setCategory("");
    setQ("");
    setChFilter("");
  };
  return (
    <div className="story-bible">
      <header className="story-header">
        <div>
          <span className="story-eyebrow">STORY BIBLE / 创作设定</span>
          <h1>{tr("bible.title")}</h1>
          <p>让人物、世界与故事细节，在创作中保持连贯。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link className="story-text-link" to={`${base}/narrative`}>
            回到章节
          </Link>
          {canEdit && (
            <>
              <Button
                variant="outline"
                aria-expanded={extractOpen}
                onClick={() => setExtractOpen(!extractOpen)}
              >
                <Sparkles size={15} />
                从小说提取
              </Button>
              <Button onClick={() => setEditor("new")}>
                <Plus size={16} />
                {tr("bible.add")}
              </Button>
            </>
          )}
        </div>
      </header>
      <div className="story-bible-tools">
        <label className="story-search">
          <Search size={16} />
          <input
            aria-label="搜索设定"
            placeholder="搜索设定名称或内容"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        <select
          aria-label="设定来源小说"
          value={novelId ?? ""}
          disabled={startExtract.isPending || cancelExtract.isPending}
          onChange={(e) => {
            setNovelId(e.target.value || null);
            clearFilters();
          }}
        >
          <option value="">全部小说与项目设定</option>
          {novels.data?.map((n) => (
            <option key={n.id} value={n.id}>
              {n.title}
            </option>
          ))}
        </select>
        <input
          aria-label="按来源章节筛选"
          aria-invalid={filterInvalid}
          placeholder="来源章节，如 5 或 5-8"
          value={chFilter}
          onChange={(e) => setChFilter(e.target.value)}
        />
        {(q || chFilter || category) && (
          <Button size="sm" variant="ghost" onClick={clearFilters}>
            清除筛选
          </Button>
        )}
      </div>
      {novels.error && (
        <ReadError error={novels.error} retry={() => void novels.refetch()} />
      )}
      {filterInvalid && (
        <p role="alert" className="story-validation">
          请输入正整数章节号，或从小到大的范围，例如 5-8。
        </p>
      )}
      {canEdit && extractOpen && (
        <section aria-label="小说设定提取" className="story-extract-panel">
          <div>
            <h2>从章节整理设定</h2>
            <p>
              先选择一本小说，再设置提取范围；留空表示全书。结果会更新该小说已有的同名设定。
            </p>
          </div>
          <div className="story-extract-controls">
            <label>
              起始章
              <input
                type="number"
                min={1}
                value={fromCh}
                disabled={running || startExtract.isPending}
                onChange={(e) => setFromCh(e.target.value)}
                placeholder="首章"
              />
            </label>
            <label>
              结束章
              <input
                type="number"
                min={1}
                value={toCh}
                disabled={running || startExtract.isPending}
                onChange={(e) => setToCh(e.target.value)}
                placeholder="末章"
              />
            </label>
            <label>
              并行章节
              <select
                value={concurrency}
                disabled={running || startExtract.isPending}
                onChange={(e) => setConcurrency(Number(e.target.value))}
              >
                {[1, 2, 4, 8].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <Button
              disabled={
                !novelId ||
                !!latest.error ||
                latest.isFetching ||
                running ||
                startExtract.isPending ||
                invalidRange
              }
              onClick={() => startExtract.mutate()}
            >
              {startExtract.isPending && (
                <Loader2 size={14} className="animate-spin" />
              )}
              开始提取
            </Button>
          </div>
          {invalidRange && (
            <p role="alert" className="story-validation">
              章节范围需为正整数，结束章不能早于起始章。
            </p>
          )}
        </section>
      )}
      {novelId && latest.error && (
        <ReadError error={latest.error} retry={() => void latest.refetch()} />
      )}
      {job && (
        <div className="story-extraction-status" role="status">
          {running && <Loader2 size={16} className="animate-spin" />}
          <span>
            {running
              ? "正在提取"
              : job.status === "done" || job.status === "succeeded"
                ? "提取完成"
                : job.status === "failed"
                  ? "提取失败"
                  : job.status === "cancelled"
                    ? "已取消提取"
                    : job.status}{" "}
            · {job.done_chapters} / {job.total_chapters} 章
          </span>
          {job.error && <span className="text-danger">{job.error}</span>}
          {running && canEdit && (
            <Button
              size="sm"
              variant="outline"
              disabled={cancelExtract.isPending}
              onClick={() => cancelExtract.mutate()}
            >
              停止提取
            </Button>
          )}
        </div>
      )}
      <div className="story-bible-layout">
        <nav className="story-categories" aria-label="设定分类">
          <p className="story-eyebrow">分类目录</p>
          {["", ...categories].map((c) => (
            <button
              key={c}
              aria-current={category === c ? "true" : undefined}
              onClick={() => setCategory(c)}
            >
              <span>{c ? catLabel(c) : tr("bible.all")}</span>
              <span>
                {catalog.isError || !settings
                  ? "—"
                  : c
                    ? settings.filter((s) => s.category === c).length
                    : settings.length}
              </span>
            </button>
          ))}
        </nav>
        <section className="story-setting-list" aria-label="设定列表">
          <div className="story-results-heading">
            <h2>{category ? catLabel(category) : "故事中的一切"}</h2>
            <span>
              {settings && !catalog.isError ? `${shown.length} 条设定` : ""}
            </span>
          </div>
          {catalog.isPending ? (
            <p role="status" className="story-state">
              正在读取设定…
            </p>
          ) : catalog.error ? (
            <ReadError
              error={catalog.error}
              retry={() => void catalog.refetch()}
            />
          ) : !shown.length ? (
            <div className="story-empty">
              <BookMarked size={34} strokeWidth={1} />
              <h3>
                {q || category || chFilter
                  ? "没有匹配的设定"
                  : "为故事建立第一条设定"}
              </h3>
              <p>
                {q || category || chFilter
                  ? "调整分类或搜索条件，查看其他内容。"
                  : "可以手动写下世界观、角色与地点，也可以从小说章节提取。"}
              </p>
              {q || category || chFilter ? (
                <Button size="sm" variant="outline" onClick={clearFilters}>
                  清除筛选
                </Button>
              ) : (
                canEdit && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setEditor("new")}
                  >
                    新建设定
                  </Button>
                )
              )}
            </div>
          ) : (
            <div className="story-setting-grid">
              {shown.map((s) => (
                <article className="story-setting-card" key={s.id}>
                  <div className="story-setting-heading">
                    <Badge variant="primary">{catLabel(s.category)}</Badge>
                    <span>
                      {s.novel_id
                        ? (novels.data?.find((n) => n.id === s.novel_id)
                            ?.title ?? "小说设定")
                        : "项目设定"}
                    </span>
                  </div>
                  <h3>{s.name}</h3>
                  <p
                    className={
                      expanded.includes(s.id)
                        ? "story-setting-content"
                        : "story-setting-content is-collapsed"
                    }
                  >
                    {s.content || tr("bible.noContent")}
                  </p>
                  {!!s.content && (
                    <button
                      className="story-read-toggle"
                      aria-expanded={expanded.includes(s.id)}
                      onClick={() =>
                        setExpanded((ids) =>
                          ids.includes(s.id)
                            ? ids.filter((id) => id !== s.id)
                            : [...ids, s.id],
                        )
                      }
                    >
                      {expanded.includes(s.id) ? "收起正文" : "阅读全文"}
                    </button>
                  )}
                  {s.source_chapters?.length > 0 && (
                    <p className="story-setting-source">
                      {tr("bible.sourceCh")}{" "}
                      {formatChapterRanges(s.source_chapters)}
                    </p>
                  )}
                  <footer>
                    <span>
                      {new Date(s.updated_at).toLocaleDateString()} 更新
                    </span>
                    <div>
                      {canAsset && TO_ASSET_CATEGORIES.has(s.category) && (
                        <button
                          disabled={toAsset.isPending}
                          title={tr("bible.toAsset")}
                          aria-label={`转为资产：${s.name}`}
                          onClick={() => toAsset.mutate(s.id)}
                        >
                          <ImagePlus size={15} />
                        </button>
                      )}
                      {canEdit && (
                        <>
                          <button
                            aria-label={`编辑设定：${s.name}`}
                            onClick={() => setEditor(s)}
                          >
                            <Pencil size={15} />
                          </button>
                          <button
                            disabled={del.isPending}
                            aria-label={`删除设定：${s.name}`}
                            onClick={async () => {
                              if (
                                await confirm({
                                  title: tr("common.delete"),
                                  message: tr("bible.deleteConfirm").replace(
                                    "{name}",
                                    s.name,
                                  ),
                                  confirmText: tr("common.delete"),
                                  danger: true,
                                })
                              )
                                del.mutate(s.id);
                            }}
                          >
                            <Trash2 size={15} />
                          </button>
                        </>
                      )}
                    </div>
                  </footer>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
      {editor && (
        <SettingEditor
          key={editor === "new" ? "new" : editor.id}
          projectId={projectId!}
          novelId={novelId}
          setting={editor === "new" ? undefined : editor}
          categories={categories}
          catLabel={catLabel}
          onClose={() => setEditor(null)}
          onSaved={() => {
            invalidate();
            clearFilters();
            toast.push("设定已保存", "success");
          }}
        />
      )}
    </div>
  );
}

function ReadError({ error, retry }: { error: Error; retry: () => void }) {
  return (
    <div className="story-state" role="alert">
      <p>{error.message}</p>
      <Button size="sm" variant="outline" onClick={retry}>
        重新读取
      </Button>
    </div>
  );
}
function SettingEditor({
  projectId,
  novelId,
  setting,
  categories,
  catLabel,
  onClose,
  onSaved,
}: {
  projectId: string;
  novelId: string | null;
  setting?: Setting;
  categories: string[];
  catLabel: (c: string) => string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(setting?.name ?? ""),
    [content, setContent] = useState(setting?.content ?? ""),
    [category, setCategory] = useState(setting?.category ?? "world");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const pending = useRef(false),
    confirm = useConfirm();
  const dirty =
    name !== (setting?.name ?? "") ||
    content !== (setting?.content ?? "") ||
    category !== (setting?.category ?? "world");
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (dirty || pending.current) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  async function close() {
    if (pending.current) return;
    if (
      !dirty ||
      (await confirm({
        title: "放弃未保存的设定？",
        message: "当前修改尚未保存。返回编辑可以继续保留这些文字。",
        confirmText: "放弃修改",
        cancelText: "返回编辑",
      }))
    )
      onClose();
  }
  async function save() {
    if (pending.current || !name.trim()) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const payload = { name: name.trim(), content, category };
      if (setting)
        await api.patch(
          `/projects/${projectId}/settings/${setting.id}`,
          payload,
        );
      else
        await api.post(`/projects/${projectId}/settings`, {
          ...payload,
          novel_id: novelId,
        });
      onSaved();
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
      title={setting ? "编辑设定" : "新建设定"}
      onClose={() => void close()}
      width={800}
    >
      <form
        className="story-setting-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <p className="text-xs text-muted-foreground">
          {setting
            ? "整理细节，让后续剧本创作有据可依。"
            : novelId
              ? "保存到当前选择的小说。"
              : "保存为项目设定，不绑定某本小说。"}
        </p>
        <label>
          设定名称
          <Input
            autoFocus
            required
            maxLength={255}
            value={name}
            disabled={busy}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          分类
          <select
            aria-label="分类"
            value={category}
            disabled={busy}
            onChange={(e) => setCategory(e.target.value)}
          >
            {categories.map((c) => (
              <option key={c} value={c}>
                {catLabel(c)}
              </option>
            ))}
          </select>
        </label>
        <label>
          设定正文
          <textarea
            aria-label="设定正文"
            rows={12}
            value={content}
            disabled={busy}
            placeholder="描述背景、性格、关系或创作中需要遵守的细节…"
            onChange={(e) => setContent(e.target.value)}
          />
        </label>
        <div className="story-form-status">
          <span>{content.replace(/\s/g, "").length.toLocaleString()} 字</span>
          <span>{dirty ? "未保存修改" : ""}</span>
        </div>
        {error && (
          <p role="alert" className="story-validation">
            {error}。输入已保留，可以重试。
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="ghost"
            disabled={busy}
            onClick={() => void close()}
          >
            取消
          </Button>
          <Button type="submit" disabled={busy || !name.trim()}>
            {busy ? "保存中…" : "保存设定"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

// 章节列表 → 连续区间压缩:[1,2,3,4,8,11,12,14] → "1-4、8、11-12、14"
function formatChapterRanges(nums: number[]): string {
  const a = [...new Set(nums)].sort((x, y) => x - y);
  if (a.length === 0) return "";
  const parts: string[] = [];
  let start = a[0];
  let prev = a[0];
  for (let i = 1; i <= a.length; i++) {
    const v = a[i];
    if (v === prev + 1) {
      prev = v;
      continue;
    }
    parts.push(start === prev ? `${start}` : `${start}-${prev}`);
    if (v !== undefined) {
      start = v;
      prev = v;
    }
  }
  return parts.join("、");
}
