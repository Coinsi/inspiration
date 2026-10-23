import { WorkDocuments } from "@/components/WorkDocuments";
import {
  WorkSavedOutputs,
  type SavedArtifact,
  RestoreWorkVersion,
} from "@/components/WorkSavedOutputs";
import { DocumentChangeReview } from "@/components/WorkPackage";
import { WorkEditing } from "@/components/WorkEditing";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowUpRight, Film, Layers, Loader2 } from "lucide-react";
import { api, blobUrl, type Timeline } from "@/lib/api";
import { WorkPicker, type Work } from "@/components/WorkPicker";
import { MediaVideo } from "@/components/MediaVideo";
import { Button } from "@/components/ui/button";
import "./cuts.css";
import { TimelinePlayback } from "@/components/TimelinePlayback";
import { editableContent } from "@/lib/timeline-edit";
import type { TimelineDocument } from "@/lib/timeline-edit";

type Snapshot = {
  native_artifacts?: Record<string, SavedArtifact>;
  native_roots?: Record<string, { id: string; revision: number }>;
  resources?: { usage_id: string; name: string }[];
  timeline_captures?: Record<
    string,
    { document: TimelineDocument; source_revision: number }
  >;
};
type Change = {
  id: string;
  status: string;
  committed_revision: number | null;
  created_at: string;
  snapshot?: { sync_reviews?: unknown[] };
  timeline_update: {
    timeline_id?: string;
    summary?: string[];
    additional_updates?: unknown[];
  };
  preview?: {
    status: string;
    error: string | null;
    blob_hash: string | null;
  } | null;
};
type Commit = { revision: number; snapshot: Snapshot; created_at: string };
const date = (value: string) => new Date(value).toLocaleString();
const linkStyle =
  "inline-flex items-center gap-2 rounded-xl border px-4 py-2 text-sm hover:bg-muted";

export default function Works() {
  const { projectId = "", workId } = useParams();
  const navigate = useNavigate();
  const base = `/projects/${projectId}`;
  const works = useQuery({
    queryKey: ["works", projectId],
    queryFn: () => api.get<Work[]>(`${base}/works`),
  });
  if (workId)
    return (
      <WorkDetail
        key={`${projectId}/${workId}`}
        projectId={projectId}
        workId={workId}
        works={works.data}
        error={works.error}
      />
    );
  return (
    <div className="mx-auto w-full max-w-7xl space-y-8 p-4 md:p-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">作品</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            从创作到定稿，在这里查看每份作品的内容与版本。
          </p>
        </div>
        <WorkPicker
          projectId={projectId}
          value=""
          createOnly
          onChange={(id) => navigate(`${base}/works/${id}`)}
        />
      </header>
      {works.isPending && <p role="status">正在读取作品…</p>}
      {works.error && (
        <p role="alert">
          {works.error.message}{" "}
          <Button variant="ghost" onClick={() => works.refetch()}>
            重试
          </Button>
        </p>
      )}
      {works.data?.length === 0 && (
        <div className="rounded-2xl border bg-surface px-6 py-20 text-center">
          <Film className="mx-auto mb-5 h-10 w-10 text-muted-foreground" />
          <h2 className="text-xl font-medium">创建你的第一份作品</h2>
          <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-muted-foreground">
            先为作品起个名字，再到剪辑成片中保存候选。预览确认后，已采用的内容会保留在这里。
          </p>
        </div>
      )}
      <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
        {works.data?.map((work) => (
          <Link
            key={work.id}
            to={`${base}/works/${work.id}`}
            className="group rounded-2xl border bg-surface p-6 transition-colors hover:border-primary/50 hover:bg-muted/20"
          >
            <div className="mb-7 flex items-center justify-between">
              <span className="rounded-xl bg-primary/10 p-3 text-primary">
                <Film size={23} />
              </span>
              <span className="text-xs text-muted-foreground">
                {work.engineering_revision
                  ? `已采用 · 版本 ${work.engineering_revision}`
                  : "尚未采用版本"}
              </span>
            </div>
            <h2 className="truncate text-xl font-semibold">{work.title}</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              {work.script_id ? "已关联剧本" : "独立作品"}
            </p>
            <div className="mt-6 flex items-center justify-between border-t pt-4 text-sm text-muted-foreground">
              <span>查看内容与候选</span>
              <ArrowUpRight size={18} />
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function WorkDetail({
  projectId,
  workId,
  works,
  error,
}: {
  projectId: string;
  workId: string;
  works?: Work[];
  error: Error | null;
}) {
  const base = `/projects/${projectId}`;
  const path = `${base}/works/${workId}/engineering`;
  const work = works?.find((w) => w.id === workId);
  const [query, setQuery] = useSearchParams();
  type WorkTab = "current" | "candidates" | "history" | "documents";
  const requestedTab = query.get("view");
  const tab: WorkTab = requestedTab === "candidates" || requestedTab === "history" || requestedTab === "documents" ? requestedTab : "current";
  const setTab = (value: WorkTab) => setQuery(previous => {
    const next = new URLSearchParams(previous);
    next.set("view", value);
    return next;
  }, { replace: true });
  const [offset, setOffset] = useState(0);
  const selected = query.get("change") || "";
  const setSelected = (value: string) => setQuery(previous => {
    const next = new URLSearchParams(previous);
    if (value) next.set("change", value); else next.delete("change");
    return next;
  }, { replace: true });
  const [revision, setRevision] = useState<number | null>(null);
  const current = useQuery({
    queryKey: ["engineering", projectId, workId],
    queryFn: () => api.get<{ revision: number; snapshot: Snapshot }>(path),
  });
  const changes = useQuery({
    queryKey: ["engineering-changes", projectId, workId, offset],
    queryFn: () => api.get<Change[]>(`${path}/changes?offset=${offset}`),
    enabled: tab === "candidates",
  });
  const history = useQuery({
    queryKey: ["engineering-history", projectId, workId, offset],
    queryFn: () => api.get<Commit[]>(`${path}/history?offset=${offset}`),
    enabled: tab === "history",
  });
  const timelines = useQuery({
    queryKey: ["timelines", projectId],
    queryFn: () => api.get<Timeline[]>(`${base}/timelines`),
  });
  const detail = useQuery({
    queryKey: ["engineering-change", projectId, workId, selected],
    queryFn: () => api.get<Change>(`${path}/changes/${selected}`),
    enabled: tab === "candidates" && !!selected,
    refetchInterval: (q) =>
      ["pending", "submitted", "running"].includes(
        q.state.data?.preview?.status || "",
      )
        ? 2000
        : false,
  });
  const snapshot =
    tab === "history"
      ? history.data?.find((h) => h.revision === revision)?.snapshot
      : current.data?.snapshot;
  const captures = Object.entries(snapshot?.timeline_captures || {});
  const problem =
    error ||
    current.error ||
    timelines.error ||
    (tab === "candidates"
      ? changes.error || detail.error
      : tab === "history"
        ? history.error
        : null);
  const editLink = (id: string, review = false) =>
    `${base}/cuts?timeline=${encodeURIComponent(id)}&work=${workId}${review ? "&review=1" : ""}`;
  const retry = () => {
    void current.refetch();
    void timelines.refetch();
    if (tab === "candidates") {
      void changes.refetch();
      if (selected) void detail.refetch();
    }
    if (tab === "history") void history.refetch();
  };
  return (
    <div className="mx-auto w-full max-w-7xl space-y-7 p-4 md:p-8">
      <Link
        to={`${base}/works`}
        className="inline-flex items-center gap-2 text-sm text-muted-foreground"
      >
        <ArrowLeft size={16} />
        全部作品
      </Link>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="break-words text-3xl font-semibold">
            {work?.title || (works ? "作品不可用" : "读取作品…")}
          </h1>
          <p className="mt-3 text-sm text-muted-foreground">
            {current.data?.revision
              ? `当前采用版本 ${current.data.revision}`
              : "尚未采用内容"}{" "}
            · 候选与已采用内容分别保留
          </p>
        </div>
        {work?.script_id && (
          <Link className={linkStyle} to={`${base}/scripts/${work.script_id}`}>
            查看剧本
            <ArrowUpRight size={16} />
          </Link>
        )}
      </header>
      {problem && (
        <div role="alert" className="rounded-xl border p-4 text-danger">
          {problem.message}
          <Button variant="ghost" onClick={retry}>
            重新读取
          </Button>
        </div>
      )}
      {work && <WorkEditing projectId={projectId} workId={workId} />}
      <nav aria-label="作品内容" className="flex flex-wrap gap-1 border-b pb-2">
        {(
          [
            ["current", "已采用内容"],
            ["documents", "工程内容"],
            ["candidates", "候选预览"],
            ["history", "历史版本"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => {
              setTab(key);
              setOffset(0);
              setRevision(null);
              setSelected("");
            }}
            aria-current={tab === key ? "page" : undefined}
            className={`rounded-lg px-4 py-2 text-sm ${tab === key ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-muted"}`}
          >
            {label}
          </button>
        ))}
      </nav>
      {current.isPending && <p role="status">正在读取作品内容…</p>}
      {tab === "documents" && (
        <WorkDocuments projectId={projectId} workId={workId} />
      )}
      {tab === "history" && (
        <div className="flex flex-wrap gap-2">
          {history.isPending ? (
            <p>读取历史版本…</p>
          ) : (
            history.data?.map((h) => (
              <button
                key={h.revision}
                className={`${linkStyle} ${revision === h.revision ? "border-primary text-primary" : ""}`}
                onClick={() => setRevision(h.revision)}
              >
                版本 {h.revision} · {date(h.created_at)}
              </button>
            ))
          )}
          {history.data?.length === 0 && (
            <p className="text-muted-foreground">还没有已采用的历史版本。</p>
          )}
          {history.data?.length !== 0 && revision === null && (
            <p className="w-full text-sm text-muted-foreground">
              选择一个版本查看当时保存的内容。
            </p>
          )}
        </div>
      )}
      {(tab === "current" || tab === "history") && snapshot && (
        <>
          {!!Object.keys(snapshot.native_roots || {}).length && (
            <WorkSavedOutputs
              projectId={projectId}
              workId={workId}
              roots={snapshot.native_roots!}
              artifacts={snapshot.native_artifacts}
            />
          )}
          {tab === "history" && revision !== null && (
            <RestoreWorkVersion
              key={revision}
              projectId={projectId}
              workId={workId}
              revision={revision}
            />
          )}
          {captures.map(([id, capture], index) => (
            <section key={id} className="rounded-2xl border bg-surface p-6">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div>
                  <h2 className="text-lg font-semibold">
                    {timelines.data?.find((t) => t.id === id)?.name ||
                      `剪辑 ${index + 1}`}
                  </h2>
                  <p className="mt-2 text-sm text-muted-foreground">
                    {capture.document.items.length} 个片段 ·{" "}
                    {capture.document.audio.length} 条音轨 ·{" "}
                    {capture.document.subtitles.length} 条字幕
                  </p>
                </div>
                <Link
                  className={linkStyle}
                  to={editLink(id, tab === "history")}
                >
                  {tab === "history" ? "前往审阅与恢复" : "继续剪辑"}
                  <ArrowUpRight size={16} />
                </Link>
              </div>
              <SavedPlayback
                key={`${id}/${revision ?? current.data?.revision}`}
                projectId={projectId}
                document={capture.document}
              />
              <p className="mt-5 text-xs text-muted-foreground">
                这里展示保存时的内容快照。工作时间线可能包含后续修改。
              </p>
            </section>
          ))}
          {!!snapshot.resources?.length && (
            <section>
              <h2 className="mb-4 flex items-center gap-2 font-medium">
                <Layers size={18} />
                引用素材 · {snapshot.resources.length}
              </h2>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {snapshot.resources.map((r) => (
                  <div
                    key={r.usage_id}
                    className="truncate rounded-xl border bg-surface px-4 py-3 text-sm"
                  >
                    {r.name}
                  </div>
                ))}
              </div>
            </section>
          )}
          {!captures.length &&
            !snapshot.resources?.length &&
            !Object.keys(snapshot.native_roots || {}).length && (
              <section className="rounded-2xl border bg-surface px-6 py-16 text-center">
                <Film
                  size={32}
                  className="mx-auto mb-4 text-muted-foreground"
                />
                <h2 className="text-xl font-medium">尚未采用作品版本</h2>
                <p className="mx-auto my-4 max-w-md text-sm leading-6 text-muted-foreground">
                  先在上方工作剪辑中开始或继续编辑。保存候选、预览并采用后，这里会展示固定版本。
                </p>
              </section>
            )}
        </>
      )}
      {tab === "candidates" && (
        <div className="grid gap-6 lg:grid-cols-[280px_minmax(0,1fr)]">
          <div className="space-y-2">
            {changes.isPending && <p>读取候选…</p>}
            {changes.data?.length === 0 && (
              <p className="text-sm text-muted-foreground">
                还没有保存候选。可以从剪辑成片中的作品审阅开始。
              </p>
            )}
            {changes.data?.map((c) => (
              <button
                key={c.id}
                onClick={() => setSelected(c.id)}
                className={`w-full rounded-xl border p-4 text-left ${selected === c.id ? "border-primary bg-primary/5" : "bg-surface hover:bg-muted"}`}
              >
                <span className="block text-sm font-medium">
                  {c.status === "accepted"
                    ? `已采用 · 版本 ${c.committed_revision}`
                    : "待审阅候选"}
                </span>
                <span className="mt-2 block text-xs text-muted-foreground">
                  {date(c.created_at)}
                </span>
              </button>
            ))}
          </div>
          <section className="min-w-0 space-y-4">
            {detail.data &&
              (!detail.data.timeline_update.timeline_id ||
                !!detail.data.timeline_update.additional_updates?.length ||
                !!detail.data.snapshot?.sync_reviews?.length) && (
                <DocumentChangeReview
                  key={selected}
                  projectId={projectId}
                  workId={workId}
                  changeId={selected}
                />
              )}
            {(!detail.data || detail.data.timeline_update.timeline_id) && (
              <div className="flex min-h-64 items-center justify-center overflow-hidden rounded-2xl bg-black/90 text-white/70">
                {detail.data?.preview?.blob_hash ? (
                  <MediaVideo
                    src={blobUrl(projectId, detail.data.preview.blob_hash)}
                    label="作品候选预览"
                  />
                ) : (
                  <p
                    role="status"
                    className="flex items-center gap-2 p-8 text-center text-sm"
                  >
                    {detail.isFetching && (
                      <Loader2 size={18} className="animate-spin" />
                    )}
                    {!selected
                      ? "选择候选查看预览"
                      : ["pending", "running", "submitted"].includes(
                            detail.data?.preview?.status || "",
                          )
                        ? "正在制作预览，可稍后回来查看"
                        : detail.data?.preview?.status === "failed"
                          ? "预览失败，请前往作品审阅重试"
                          : detail.isPending
                            ? "读取候选…"
                            : "此候选还没有可播放的预览"}
                  </p>
                )}
              </div>
            )}
            {detail.data?.preview?.error && (
              <p role="alert" className="text-sm text-danger">
                {detail.data.preview.error}
              </p>
            )}
            {detail.data?.timeline_update.summary?.map((s, i) => (
              <p className="text-sm" key={i}>
                {s}
              </p>
            ))}
            {detail.data?.timeline_update.timeline_id && (
              <Link
                className={linkStyle}
                to={editLink(detail.data.timeline_update.timeline_id, true)}
              >
                前往作品审阅
                <ArrowUpRight size={16} />
              </Link>
            )}
          </section>
        </div>
      )}
      {(tab === "candidates" || tab === "history") && (
        <div className="flex items-center justify-end gap-3 text-sm">
          <Button
            variant="ghost"
            disabled={offset === 0}
            onClick={() => {
              setOffset(Math.max(0, offset - 30));
              setRevision(null);
              setSelected("");
            }}
          >
            上一页
          </Button>
          <span>第 {offset / 30 + 1} 页</span>
          <Button
            variant="ghost"
            disabled={
              (tab === "history" ? history.data : changes.data)?.length !== 30
            }
            onClick={() => {
              setOffset(offset + 30);
              setRevision(null);
              setSelected("");
            }}
          >
            下一页
          </Button>
        </div>
      )}
    </div>
  );
}

function SavedPlayback({
  projectId,
  document,
}: {
  projectId: string;
  document: TimelineDocument;
}) {
  const [time, setTime] = useState(0);
  if (!document.items.length) return null;
  return (
    <div className="mt-5 max-w-3xl">
      <TimelinePlayback
        projectId={projectId}
        shots={[]}
        content={editableContent(document)}
        aspect="16:9"
        time={time}
        onTime={setTime}
        selected=""
      />
      <p className="mt-2 text-xs text-muted-foreground">
        内容快速预览 · 最终画面请以候选渲染预览为准
      </p>
    </div>
  );
}
