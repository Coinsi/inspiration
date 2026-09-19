import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Captions, Download, Plus, Save, Trash2, Undo2 } from "lucide-react";
import { api, blobUrl, getToken } from "@/lib/api";
import { mediaTime } from "@/lib/library";
import {
  LibraryVersionPicker,
  useLibraryVersion,
} from "@/components/LibraryVersionPicker";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { MediaSegmentPlayer } from "@/components/MediaSegmentPlayer";
import { useConfirm } from "@/components/ui/confirm";
type Cue = { id: string; start_ms: number; end_ms: number; text: string };
type Run = {
  id: string;
  version_id: string;
  status: string;
  model_key: string;
  language: string | null;
  total: number;
  completed: number;
  revision: number;
  published_revision: number | null;
  cue_count: number;
  cancel_requested: boolean;
  error: string | null;
  cues: Cue[];
};
const field =
  "w-full min-w-0 rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary";
const labels: Record<string, string> = {
  queued: "等待转写",
  processing: "识别中",
  ready: "草稿已就绪",
  failed: "转写中断",
  canceled: "已取消",
};
export default function Transcriptions() {
  const { projectId } = useParams();
  return <Workspace key={projectId} projectId={projectId!} />;
}
function Workspace({ projectId }: { projectId: string }) {
  const [params] = useSearchParams(),
    root = `/projects/${projectId}`,
    base = root + "/transcriptions",
    qc = useQueryClient();
  const [version, setVersion] = useState(params.get("version") ?? ""),
    [language, setLanguage] = useState("zh"),
    [selected, setSelected] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const selectedVersion = useLibraryVersion(projectId, version),
    v = selectedVersion.isError ? undefined : selectedVersion.data;
  const available = useQuery({
    queryKey: ["library-version-catalog", projectId, "availability"],
    queryFn: () =>
      api.get<{ total: number }>(`${root}/library/version-catalog?limit=1`),
    refetchInterval: 5000,
  });
  const emptyLibrary = !version && available.data?.total === 0;
  const runs = useQuery({
    queryKey: ["transcriptions", projectId, version],
    queryFn: () => api.get<Run[]>(`${base}?version_id=${version}`),
    enabled: !!version,
    refetchInterval: 2000,
  });
  const runId = selected || runs.data?.[0]?.id;
  const run = useQuery({
    queryKey: ["transcription", projectId, runId],
    queryFn: () => api.get<Run>(`${base}/${runId}`),
    enabled: !!runId,
    refetchInterval: (query) =>
      ["queued", "processing"].includes(query.state.data?.status ?? "")
        ? 1500
        : false,
  });
  async function perform(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
      await qc.invalidateQueries({ queryKey: ["transcriptions", projectId] });
      await qc.invalidateQueries({ queryKey: ["transcription", projectId] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="studio-page space-y-5">
      <header className="flex flex-wrap justify-between gap-3">
        <div>
          <p className="mb-2 text-[10px] tracking-[.2em] text-primary">
            TRANSCRIPT STUDIO
          </p>
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <Captions className="h-6 w-6" />
            转写与字幕
          </h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            把原片声音整理成字幕草稿，核对文字和时间后，用于台词检索或导出剪辑。
          </p>
        </div>
        <Link to={root + "/library"}>
          <Button variant="outline">返回视频素材</Button>
        </Link>
      </header>
      {error && (
        <p
          role="alert"
          className="rounded-xl border border-destructive/40 bg-destructive/10 p-3 text-sm"
        >
          {error}
        </p>
      )}
      {available.isPending && !version ? (
        <p role="status" className="studio-empty">
          正在读取视频素材…
        </p>
      ) : available.isError && !version ? (
        <div role="alert" className="studio-empty">
          视频素材读取失败
          <Button onClick={() => void available.refetch()}>重试</Button>
        </div>
      ) : emptyLibrary ? (
        <section className="studio-empty !min-h-80">
          <Captions size={36} />
          <h2 className="text-xl text-foreground">先放入一段视频</h2>
          <p>视频处理完成后，就可以识别声音、核对字幕并用于剪辑。</p>
          <Link className="studio-primary" to={root + "/library?import=1"}>
            导入视频
          </Link>
        </section>
      ) : (
        <section className="space-y-4 rounded-xl border border-border bg-card p-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-0 flex-1 space-y-2 text-xs">
              <p>视频原片版本</p>
              <LibraryVersionPicker
                projectId={projectId}
                value={version}
                label="转写视频版本"
                disabled={busy}
                onChange={(id) => {
                  setVersion(id);
                  setSelected("");
                }}
              />
            </div>
            <label className="space-y-2 text-xs">
              音轨语言
              <select
                aria-label="转写语言"
                className={field}
                value={language}
                disabled={busy}
                onChange={(e) => setLanguage(e.target.value)}
              >
                {[
                  ["zh", "中文"],
                  ["en", "英语"],
                  ["ja", "日语"],
                  ["ko", "韩语"],
                  ["", "自动判断"],
                  ["fr", "法语"],
                  ["de", "德语"],
                  ["es", "西班牙语"],
                ].map(([k, l]) => (
                  <option key={k} value={k}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            <Button
              disabled={busy || v?.status !== "ready"}
              onClick={() =>
                void perform(async () => {
                  const r = await api.post<Run>(base, {
                    version_id: version,
                    language: language || null,
                  });
                  setSelected(r.id);
                })
              }
            >
              开始转写
            </Button>
          </div>
          <p className="text-xs leading-6 text-muted-foreground">
            按30秒片段保存进度，识别中断后可续接。结果属于机器草稿，不会自动进入检索；背景音乐、口音和片段边界请重点核对。
          </p>
        </section>
      )}
      {runs.isError && (
        <Button onClick={() => void runs.refetch()}>重新读取转写任务</Button>
      )}
      {runs.data && runs.data.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">转写记录</span>
          <select
            aria-label="转写记录"
            className={`${field} max-w-md`}
            value={runId}
            onChange={(e) => setSelected(e.target.value)}
          >
            {runs.data.map((r, i) => (
              <option key={r.id} value={r.id}>
                {i === 0 ? "最新 · " : ""}
                {
                  labels[r.id === run.data?.id ? run.data.status : r.status]
                } · {r.id === run.data?.id ? run.data.cue_count : r.cue_count}{" "}
                条 · {r.language ?? "自动语言"}
              </option>
            ))}
          </select>
          <Button
            variant="ghost"
            disabled={
              busy ||
              v?.status !== "ready" ||
              ["queued", "processing"].includes(run.data?.status ?? "")
            }
            onClick={() =>
              void perform(async () => {
                const r = await api.post<Run>(base, {
                  version_id: version,
                  language: language || null,
                  new_run: true,
                });
                setSelected(r.id);
              })
            }
          >
            重新识别为新草稿
          </Button>
        </div>
      )}
      {run.isError && (
        <Button onClick={() => void run.refetch()}>重新读取转写结果</Button>
      )}
      {run.data && (
        <section className="space-y-3 rounded-xl border border-border bg-card p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-medium">
              {labels[run.data.status] ?? run.data.status}
            </h2>
            <span className="text-xs text-muted-foreground">
              已处理 {run.data.completed}/{run.data.total} 段 ·{" "}
              {run.data.cue_count} 条字幕
            </span>
          </div>
          <progress
            aria-label="转写任务进度"
            className="h-2 w-full accent-primary"
            max={run.data.total || 1}
            value={run.data.completed}
          />
          {run.data.error && (
            <p role="alert" className="text-sm text-destructive">
              {run.data.error}
            </p>
          )}
          {["queued", "processing"].includes(run.data.status) && (
            <Button
              variant="outline"
              disabled={busy || run.data.cancel_requested}
              onClick={() =>
                void perform(async () => {
                  await api.post(`${base}/${runId}/control`, {
                    action: "cancel",
                  });
                })
              }
            >
              {run.data.cancel_requested ? "正在结束当前片段…" : "取消转写"}
            </Button>
          )}
          {["failed", "canceled"].includes(run.data.status) && (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  await api.post(`${base}/${runId}/control`, {
                    action: "retry",
                  });
                })
              }
            >
              从已完成片段续接
            </Button>
          )}
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer">识别来源与版本</summary>
            <p className="mt-2 break-all">{run.data.model_key}</p>
          </details>
        </section>
      )}
      {run.data?.status === "ready" && v && (
        <Editor
          key={run.data.id}
          projectId={projectId}
          run={run.data}
          duration={v.duration_ms ?? 0}
          proxy={v.proxy_hash}
        />
      )}{" "}
      {!runId &&
        !emptyLibrary &&
        !available.isPending &&
        !available.isError && (
          <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
            选择视频并开始转写，或从视频素材详情进入。
          </div>
        )}
    </div>
  );
}
function Editor({
  projectId,
  run,
  duration,
  proxy,
}: {
  projectId: string;
  run: Run;
  duration: number;
  proxy: string | null;
}) {
  const base = `/projects/${projectId}/transcriptions/${run.id}`,
    { me } = useAuth(),
    confirm = useConfirm(),
    qc = useQueryClient(),
    key = `transcript-draft:${me?.user.id}:${run.id}`;
  const [cues, setCues] = useState(run.cues),
    [saved, setSaved] = useState(JSON.stringify(run.cues)),
    [revision, setRevision] = useState(run.revision),
    [offset, setOffset] = useState(0),
    [active, setActive] = useState<Cue | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [recovery, setRecovery] = useState<Cue[] | null>(null);
  const undo = useRef<Cue[][]>([]);
  const dirty = JSON.stringify(cues) !== saved;
  useEffect(() => {
    try {
      const d = JSON.parse(localStorage.getItem(key) || "null");
      if (Array.isArray(d)) setRecovery(d);
    } catch {
      /* unavailable */
    }
  }, [key]);
  useEffect(() => {
    try {
      if (dirty) localStorage.setItem(key, JSON.stringify(cues));
      else localStorage.removeItem(key);
    } catch {
      setError("浏览器草稿空间不足，请及时保存");
    }
  }, [cues, dirty, key]);
  useEffect(() => {
    const fn = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", fn);
    return () => window.removeEventListener("beforeunload", fn);
  }, [dirty]);
  function change(next: Cue[]) {
    undo.current = [...undo.current, cues].slice(-20);
    setCues(next);
  }
  function edit(id: string, values: Partial<Cue>) {
    change(cues.map((c) => (c.id === id ? { ...c, ...values } : c)));
  }
  async function perform(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await fn();
      await qc.invalidateQueries({ queryKey: ["transcription", projectId] });
      await qc.invalidateQueries({ queryKey: ["transcriptions", projectId] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const invalid = cues.some(
      (c) =>
        !c.text.trim() ||
        c.start_ms < 0 ||
        c.end_ms - c.start_ms < 100 ||
        c.end_ms > duration,
    ),
    visible = cues.slice(offset, offset + 50);
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-lg font-medium">
          字幕草稿{" "}
          <span className="text-xs text-muted-foreground">
            {dirty ? "未保存修改" : `版本 ${revision}`}
          </span>
        </h2>
        <Button
          variant="outline"
          disabled={busy || !undo.current.length}
          onClick={() => setCues(undo.current.pop()!)}
        >
          <Undo2 className="mr-1 h-4 w-4" />
          撤销
        </Button>
        <Button
          disabled={busy || !dirty || invalid}
          onClick={() =>
            void perform(async () => {
              const next = await api.put<Run>(base, { revision, cues });
              setCues(next.cues);
              setRevision(next.revision);
              setSaved(JSON.stringify(next.cues));
              setMessage("字幕草稿已保存");
            })
          }
        >
          <Save className="mr-1 h-4 w-4" />
          保存字幕
        </Button>
        <Button
          variant="outline"
          disabled={busy || dirty}
          onClick={() =>
            void perform(async () => {
              const response = await fetch(`/api/v1${base}/srt`, {
                headers: { Authorization: `Bearer ${getToken() ?? ""}` },
              });
              if (!response.ok) throw Error("字幕导出失败");
              const url = URL.createObjectURL(await response.blob()),
                a = document.createElement("a");
              a.href = url;
              a.download = "字幕草稿.srt";
              a.click();
              setTimeout(() => URL.revokeObjectURL(url), 30000);
            })
          }
        >
          <Download className="mr-1 h-4 w-4" />
          导出 SRT
        </Button>
        <Button
          variant="outline"
          disabled={busy || dirty || run.published_revision === revision}
          onClick={() =>
            void perform(async () => {
              if (
                !(await confirm({
                  title: "发布到台词检索？",
                  message:
                    "请确认已核对字幕与时间。当前字幕会作为台词证据进入检索，不作为画面出现的证据；此视频之前发布的自动转写会更新，手工标注保留。",
                }))
              )
                return;
              await api.post(base + "/publish", { revision });
              await qc.invalidateQueries({
                queryKey: ["library-annotations", projectId],
              });
              setMessage("核对后的字幕已发布到台词检索");
            })
          }
        >
          核对完成，发布到检索
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="text-sm text-primary">
          {message}
        </p>
      )}
      {invalid && (
        <p role="alert" className="text-xs text-destructive">
          请检查空字幕、结束早于开始或超出原片的时间。
        </p>
      )}
      {run.revision !== revision && (
        <p className="text-xs text-destructive">
          服务端已有新版本，当前草稿仍保留；请核对后重新载入。
        </p>
      )}
      {recovery && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-primary/30 p-3 text-sm">
          <span>发现未保存的字幕草稿</span>
          <Button
            onClick={() => {
              change(recovery);
              setRecovery(null);
            }}
          >
            恢复字幕草稿
          </Button>
          <Button variant="ghost" onClick={() => setRecovery(null)}>
            忽略
          </Button>
        </div>
      )}
      <div className="grid gap-4 xl:grid-cols-[minmax(300px,.7fr)_minmax(0,1fr)]">
        <aside className="space-y-3 self-start xl:sticky xl:top-4">
          {proxy && active ? (
            <MediaSegmentPlayer
              key={`${active.id}:${active.start_ms}:${active.end_ms}`}
              src={blobUrl(projectId, proxy)}
              start={active.start_ms}
              end={active.end_ms}
              label="字幕对应音轨片段"
              autoPlay
            />
          ) : proxy ? (
            <video
              src={blobUrl(projectId, proxy)}
              className="aspect-video w-full rounded-xl bg-black"
              controls
              preload="metadata"
              aria-label="字幕原片预览"
            />
          ) : null}
          <p className="text-xs leading-6 text-muted-foreground">
            点击字幕编号播放对应片段。字幕按原片时间计时，导入剪辑后需按实际截取位置调整。自动识别不包含说话人身份判断。
          </p>
        </aside>
        <div className="min-w-0 space-y-3">
          {visible.map((c, i) => (
            <article
              key={c.id}
              className={`space-y-2 rounded-xl border bg-card p-3 ${active?.id === c.id ? "border-primary" : "border-border"}`}
            >
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  aria-label={`播放字幕 ${offset + i + 1}`}
                  onClick={() => setActive(c)}
                >
                  {offset + i + 1}
                </Button>
                <label className="min-w-0 flex-1 text-xs">
                  <span className="sr-only">开始秒数</span>
                  <input
                    aria-label={`字幕 ${offset + i + 1} 开始秒数`}
                    className={field}
                    type="number"
                    value={c.start_ms / 1000}
                    min={0}
                    step={0.1}
                    disabled={busy}
                    onChange={(e) => {
                      if (Number.isFinite(e.target.valueAsNumber))
                        edit(c.id, {
                          start_ms: Math.round(e.target.valueAsNumber * 1000),
                        });
                    }}
                  />
                </label>
                <span className="text-xs text-muted-foreground">→</span>
                <label className="min-w-0 flex-1 text-xs">
                  <span className="sr-only">结束秒数</span>
                  <input
                    aria-label={`字幕 ${offset + i + 1} 结束秒数`}
                    className={field}
                    type="number"
                    value={c.end_ms / 1000}
                    min={0}
                    step={0.1}
                    disabled={busy}
                    onChange={(e) => {
                      if (Number.isFinite(e.target.valueAsNumber))
                        edit(c.id, {
                          end_ms: Math.round(e.target.valueAsNumber * 1000),
                        });
                    }}
                  />
                </label>
                <Button
                  variant="ghost"
                  aria-label={`删除字幕 ${offset + i + 1}`}
                  disabled={busy}
                  onClick={() => change(cues.filter((row) => row.id !== c.id))}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
              <textarea
                aria-label={`字幕 ${offset + i + 1} 内容`}
                className={`${field} min-h-16`}
                maxLength={4000}
                value={c.text}
                disabled={busy}
                onChange={(e) => edit(c.id, { text: e.target.value })}
              />
            </article>
          ))}
          {!cues.length && (
            <p className="rounded-xl border border-dashed border-border p-6 text-sm text-muted-foreground">
              没有识别出有效语音。可以手动添加字幕，或检查语言、音轨后重新识别。
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              disabled={busy || cues.length >= 10000 || duration < 100}
              onClick={() => {
                const start = Math.min(
                  Math.max(0, duration - 1000),
                  cues[cues.length - 1]?.end_ms ?? 0,
                );
                change([
                  ...cues,
                  {
                    id: crypto.randomUUID(),
                    start_ms: start,
                    end_ms: Math.min(duration, start + 1000),
                    text: "新字幕",
                  },
                ]);
                setOffset(Math.floor(cues.length / 50) * 50);
              }}
            >
              <Plus className="mr-1 h-4 w-4" />
              添加字幕
            </Button>
            {offset > 0 && (
              <Button variant="ghost" onClick={() => setOffset(offset - 50)}>
                上一页
              </Button>
            )}
            <span className="text-xs text-muted-foreground">
              {cues.length
                ? `${offset + 1}—${Math.min(offset + 50, cues.length)} / ${cues.length}`
                : "0 条"}{" "}
              · {mediaTime(duration)}
            </span>
            {offset + 50 < cues.length && (
              <Button variant="ghost" onClick={() => setOffset(offset + 50)}>
                下一页
              </Button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
