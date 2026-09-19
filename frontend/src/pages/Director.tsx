import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import {
  Camera,
  Download,
  History,
  Pause,
  Play,
  Plus,
  Redo2,
  Save,
  Trash2,
  Undo2,
  Copy,
  Focus,
  Grid3X3,
  Lightbulb,
  Box,
  Maximize2,
  X,
} from "lucide-react";
import "./director.css";
import { navigateTabs } from "@/lib/tab-navigation";
import { api, apiUpload, getToken } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import {
  DirectorViewport,
  type ViewportHandle,
} from "@/components/DirectorViewport";
import {
  person,
  cameraPath,
  sampleCamera,
  patchCamera,
  insertionProgress,
  sceneTemplate,
  type Stage,
  type StageDocument,
  type StageObject,
  type Vec,
  type StageCamera,
  type CameraKeyframe,
} from "@/lib/director";
const input =
  "w-full min-w-0 rounded-lg border border-border bg-background px-2 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary";
function NumberField({
  label,
  value,
  onChange,
  min = -500,
  max = 500,
  step = 0.1,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <label className="min-w-0 space-y-1 text-xs text-muted-foreground">
      <span>{label}</span>
      <input
        aria-label={label}
        type="number"
        className={input}
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => {
          const n = e.target.valueAsNumber;
          if (Number.isFinite(n)) onChange(Math.min(max, Math.max(min, n)));
        }}
      />
    </label>
  );
}
function VectorFields({
  label,
  value,
  onChange,
}: {
  label: string;
  value: Vec;
  onChange: (v: Vec) => void;
}) {
  return (
    <div className="grid grid-cols-3 gap-2">
      {value.map((v, i) => (
        <NumberField
          key={i}
          label={`${label} ${["X", "Y", "Z"][i]}`}
          value={v}
          onChange={(n) =>
            onChange(value.map((x, j) => (i === j ? n : x)) as Vec)
          }
        />
      ))}
    </div>
  );
}
function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
export default function Director() {
  const { projectId } = useParams();
  return <Workspace key={projectId} projectId={projectId!} />;
}
function Workspace({ projectId }: { projectId: string }) {
  const { me } = useAuth(),
    confirm = useConfirm(),
    qc = useQueryClient(),
    root = `/projects/${projectId}`,
    base = root + "/director-scenes",
    viewport = useRef<ViewportHandle>(null);
  const [stage, setStage] = useState<Stage | null>(null),
    [saved, setSaved] = useState(""),
    [selected, setSelected] = useState(""),
    [view, setView] = useState<"layout" | "camera">("layout"),
    [progress, setProgress] = useState(0),
    [playing, setPlaying] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [target, setTarget] = useState(""),
    [cameraTab, setCameraTab] = useState("camera"),
    [historyOpen, setHistoryOpen] = useState(false),
    [historyOffset, setHistoryOffset] = useState(0),
    [offset, setOffset] = useState(0),
    [recovery, setRecovery] = useState<Stage | null>(null);
  const [inspector, setInspector] = useState<"object" | "camera" | "light">(
      "camera",
    ),
    [guides, setGuides] = useState(false),
    [focused, setFocused] = useState(false),
    [exportProgress, setExportProgress] = useState<number | null>(null);
  const busyRef = useRef(false),
    exportAbort = useRef<AbortController | null>(null);
  useEffect(() => () => exportAbort.current?.abort(), []);
  const undo = useRef<StageDocument[]>([]),
    redo = useRef<StageDocument[]>([]),
    [historyTick, tick] = useState(0);
  void historyTick;
  const key = `director-draft:${me?.user.id}:${projectId}`;
  const list = useQuery({
    queryKey: ["director-list", projectId, offset],
    queryFn: () =>
      api.get<Pick<Stage, "id" | "name" | "revision">[]>(
        `${base}?offset=${offset}`,
      ),
  });
  const shots = useQuery({
    queryKey: ["shots", projectId],
    queryFn: () =>
      api.get<{ id: string; title: string; code: string }[]>(root + "/shots"),
  });
  const history = useQuery({
    queryKey: ["director-history", stage?.id, historyOffset],
    queryFn: () =>
      api.get<{ revision: number; created_at: string }[]>(
        `${base}/${stage!.id}/history?offset=${historyOffset}`,
      ),
    enabled: !!stage && historyOpen,
  });
  const dirty = !!stage && JSON.stringify(stage) !== saved;
  useEffect(() => {
    try {
      const d = JSON.parse(localStorage.getItem(key) || "null");
      if (d?.document?.objects && d.id) setRecovery(d);
    } catch {
      /* no saved draft */
    }
  }, [key]);
  useEffect(() => {
    if (!stage) return;
    try {
      if (dirty) localStorage.setItem(key, JSON.stringify(stage));
      else localStorage.removeItem(key);
    } catch {
      setError("浏览器草稿存储不可用，请及时保存场景");
    }
  }, [stage, dirty, key]);
  useEffect(() => {
    const fn = (e: BeforeUnloadEvent) => {
      if (dirty || busy) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", fn);
    return () => window.removeEventListener("beforeunload", fn);
  }, [dirty, busy]);
  useEffect(() => {
    if (!playing || !stage) return;
    let frame = 0;
    const start = performance.now() - progress * stage.document.duration * 1000;
    const animate = () => {
      const t = Math.min(
        1,
        (performance.now() - start) / (stage.document.duration * 1000),
      );
      setProgress(t);
      if (t >= 1) setPlaying(false);
      else frame = requestAnimationFrame(animate);
    };
    frame = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frame);
  }, [playing, stage?.document.duration]);
  async function perform(fn: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setPlaying(false);
    setError("");
    setMessage("");
    try {
      await fn();
    } catch (e) {
      if ((e as Error).name === "AbortError")
        setMessage("预演导出已取消，场景保持不变");
      else setError((e as Error).message);
    } finally {
      busyRef.current = false;
      setBusy(false);
      setExportProgress(null);
      exportAbort.current = null;
    }
  }
  function accept(next: Stage) {
    setStage(next);
    setSaved(JSON.stringify(next));
    undo.current = [];
    redo.current = [];
    setSelected("");
    setHistoryOffset(0);
    setProgress(0);
    setInspector("camera");
    setCameraTab("camera");
  }
  async function switchAllowed() {
    return (
      !dirty ||
      (await confirm({
        title: "离开当前预演？",
        message: "尚未保存的修改将被放弃。可以取消并先保存。",
      }))
    );
  }
  function change(document: StageDocument) {
    if (!stage || busyRef.current || playing) return;
    undo.current = [...undo.current, stage.document].slice(-100);
    redo.current = [];
    setStage({ ...stage, document });
    tick((n) => n + 1);
  }
  function moveHistory(forward: boolean) {
    if (!stage || busy) return;
    const from = forward ? redo : undo,
      to = forward ? undo : redo,
      next = from.current.pop();
    if (next) {
      to.current.push(stage.document);
      setStage({ ...stage, document: next });
      tick((n) => n + 1);
    }
  }
  function selectObject(id: string) {
    setSelected(id);
    if (id) setInspector("object");
  }
  function duplicateObject() {
    if (
      !stage ||
      busyRef.current ||
      playing ||
      stage.document.objects.length >= 80
    )
      return;
    const source = stage.document.objects.find((o) => o.id === selected);
    if (!source) return;
    const copy = {
      ...structuredClone(source),
      id: crypto.randomUUID(),
      name: `${source.name.slice(0, 115)} 副本`,
      position: [
        Math.min(500, source.position[0] + 0.8),
        source.position[1],
        source.position[2],
      ] as Vec,
    };
    change({ ...stage.document, objects: [...stage.document.objects, copy] });
    selectObject(copy.id);
  }
  function removeObject() {
    if (!stage || busyRef.current || playing || !selected) return;
    change({
      ...stage.document,
      objects: stage.document.objects.filter((o) => o.id !== selected),
    });
    setSelected("");
  }
  function frame(mode: "scene" | "selected" | "front" | "top") {
    setPlaying(false);
    setView("layout");
    viewport.current?.frame(mode);
  }
  function objectChange(changeSet: Partial<StageObject>) {
    if (stage)
      change({
        ...stage.document,
        objects: stage.document.objects.map((o) =>
          o.id === selected ? { ...o, ...changeSet } : o,
        ),
      });
  }
  async function save() {
    if (!stage) return;
    const next = await api.put<Stage>(`${base}/${stage.id}`, {
      name: stage.name,
      revision: stage.revision,
      document: stage.document,
    });
    setStage(next);
    setSaved(JSON.stringify(next));
    setMessage(`已保存版本 ${next.revision}`);
    await qc.invalidateQueries({ queryKey: ["director-list", projectId] });
    await qc.invalidateQueries({ queryKey: ["director-history", stage.id] });
    return next;
  }
  const obj = stage?.document.objects.find((o) => o.id === selected),
    d = stage?.document;
  const path = d ? cameraPath(d) : [];
  const activeFrame = path.find((k) => k.id === cameraTab) ?? path[0];
  const frameIndex = path.findIndex((k) => k.id === activeFrame?.id);
  const nextFrame = path[frameIndex + 1],
    previousFrame = path[frameIndex - 1];
  function chooseFrame(point: CameraKeyframe) {
    setPlaying(false);
    setCameraTab(point.id);
    setProgress(point.at);
    setView("camera");
    setInspector("camera");
  }
  function updateCamera(patch: Partial<StageCamera>) {
    if (d && activeFrame)
      change(
        patchCamera(d, activeFrame.id, { ...activeFrame.camera, ...patch }),
      );
  }
  function updateFrame(
    patch: Partial<Pick<CameraKeyframe, "at" | "hold" | "ease">>,
  ) {
    if (!d || !activeFrame || activeFrame.id === "end_camera") return;
    if (activeFrame.id === "camera") {
      change({
        ...d,
        camera_hold: patch.hold ?? d.camera_hold ?? 0,
        camera_ease: patch.ease ?? d.camera_ease ?? "smooth",
      });
    } else {
      change({
        ...d,
        camera_keyframes: (d.camera_keyframes ?? []).map((k) =>
          k.id === activeFrame.id ? { ...k, ...patch } : k,
        ),
      });
    }
    setProgress(patch.at ?? activeFrame.at);
    setView("camera");
  }
  function addFrame() {
    if (!d || busyRef.current || playing) return;
    const at = insertionProgress(d, progress);
    if (at === null) return;
    const point: CameraKeyframe = {
      id: crypto.randomUUID(),
      at,
      camera: sampleCamera(d, at),
      hold: 0,
      ease: "smooth",
    };
    change({
      ...d,
      camera_keyframes: [...(d.camera_keyframes ?? []), point].sort(
        (a, b) => a.at - b.at,
      ),
    });
    chooseFrame(point);
  }
  useEffect(() => {
    const handle = (e: KeyboardEvent) => {
      if (document.querySelector('[role="alertdialog"], [role="dialog"]'))
        return;
      const editing = (e.target as HTMLElement)?.closest(
        'input, textarea, select, [contenteditable="true"]',
      );
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (stage && dirty && stage.name.trim() && !busyRef.current)
          void perform(async () => {
            await save();
          });
        return;
      }
      if (editing || !stage || busyRef.current || playing) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        moveHistory(e.shiftKey);
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") {
        e.preventDefault();
        duplicateObject();
      } else if (
        !e.ctrlKey &&
        !e.metaKey &&
        !e.altKey &&
        e.key.toLowerCase() === "f"
      ) {
        e.preventDefault();
        frame(selected ? "selected" : "scene");
      } else if (e.key === "Delete") {
        e.preventDefault();
        removeObject();
      } else if (e.key === "Escape") setFocused(false);
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  });
  return (
    <div className={`director-page ${focused ? "is-focused" : ""}`}>
      <header className="director-page-header">
        <div>
          <p className="text-[9px] tracking-[.2em] text-muted-foreground">
            DIRECTOR STUDIO
          </p>
          <h1 className="flex items-center gap-2 text-base font-semibold">
            <Camera className="h-4 w-4" />
            导演台
          </h1>
        </div>
        <Link to={root + "/storyboard"}>
          <Button variant="outline">回到分镜</Button>
        </Link>
      </header>
      {error && (
        <div
          role="alert"
          className="director-notice border border-destructive/40 bg-destructive/10 text-sm"
        >
          {error}
        </div>
      )}
      {message && (
        <p role="status" className="director-notice text-xs text-primary">
          {message}
        </p>
      )}
      {recovery && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-primary/40 bg-primary/10 p-3 text-sm">
          <span>发现未保存的预演草稿「{recovery.name}」</span>
          <Button
            onClick={() =>
              void perform(async () => {
                const current = await api.get<Stage>(`${base}/${recovery.id}`);
                setSaved(JSON.stringify(current));
                setStage({ ...recovery, revision: current.revision });
                setRecovery(null);
                setMessage(
                  "草稿已恢复，请检查后保存。服务端版本已保留在历史中。",
                );
              })
            }
          >
            恢复草稿
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              localStorage.removeItem(key);
              setRecovery(null);
            }}
          >
            丢弃草稿
          </Button>
        </div>
      )}
      <div className="director-document-bar">
        <select
          aria-label="预演场景"
          className={`${input} max-w-60`}
          value={stage?.id ?? ""}
          disabled={busy || list.isLoading}
          onChange={(e) => {
            const id = e.target.value;
            if (id)
              void perform(async () => {
                if (await switchAllowed())
                  accept(await api.get<Stage>(`${base}/${id}`));
              });
          }}
        >
          <option value="">
            {list.isLoading ? "正在加载场景…" : "选择预演场景"}
          </option>
          {stage && !list.data?.some((s) => s.id === stage.id) && (
            <option value={stage.id}>
              {stage.name} · v{stage.revision}
            </option>
          )}
          {list.data?.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} · v{s.revision}
            </option>
          ))}
        </select>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() =>
            void perform(async () => {
              if (!(await switchAllowed())) return;
              accept(await api.post<Stage>(base, { name: "新的镜头预演" }));
              await qc.invalidateQueries({
                queryKey: ["director-list", projectId],
              });
            })
          }
        >
          <Plus className="mr-1 h-4 w-4" />
          新建预演
        </Button>
        {offset > 0 && (
          <Button variant="ghost" onClick={() => setOffset(offset - 50)}>
            上一页
          </Button>
        )}
        {list.data?.length === 50 && (
          <Button variant="ghost" onClick={() => setOffset(offset + 50)}>
            下一页
          </Button>
        )}
        {list.isError && (
          <Button variant="outline" onClick={() => void list.refetch()}>
            重试场景列表
          </Button>
        )}
        {stage && (
          <>
            <span className="text-xs text-muted-foreground">
              {dirty ? "有未保存的修改" : `已保存 · v${stage.revision}`}
            </span>
            <div className="ml-auto flex gap-2">
              <Button
                variant="ghost"
                aria-label="撤销预演修改"
                disabled={!undo.current.length || busy || playing}
                onClick={() => moveHistory(false)}
              >
                <Undo2 className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                aria-label="重做预演修改"
                disabled={!redo.current.length || busy || playing}
                onClick={() => moveHistory(true)}
              >
                <Redo2 className="h-4 w-4" />
              </Button>
              <Button
                disabled={busy || !dirty || !stage.name.trim()}
                onClick={() =>
                  void perform(async () => {
                    await save();
                  })
                }
              >
                <Save className="mr-1 h-4 w-4" />
                保存场景
              </Button>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => setHistoryOpen(!historyOpen)}
              >
                <History className="mr-1 h-4 w-4" />
                历史
              </Button>
            </div>
          </>
        )}
      </div>
      {!stage ? (
        <div className="grid min-h-[440px] place-items-center rounded-2xl border border-dashed border-border bg-card p-8 text-center">
          <div className="max-w-lg space-y-4">
            <Camera className="mx-auto h-12 w-12 text-primary/70" />
            <h2 className="text-xl">先排场面，再决定镜头</h2>
            <p className="text-sm leading-7 text-muted-foreground">
              新建一个预演，用双人对话或室内场景开始。人物替身用于判断位置、视线与构图，取景画面可以回填到镜头的候选记录。
            </p>
          </div>
        </div>
      ) : (
        d && (
          <>
            {historyOpen && (
              <div className="rounded-xl border border-border bg-card p-4">
                <h2 className="mb-3 text-sm font-medium">场景版本</h2>
                {history.isError ? (
                  <Button onClick={() => void history.refetch()}>
                    重新加载历史
                  </Button>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {history.data?.map((h) => (
                      <Button
                        key={h.revision}
                        variant="outline"
                        disabled={busy || h.revision === stage.revision}
                        onClick={() =>
                          void perform(async () => {
                            if (
                              !(await confirm({
                                title: `恢复版本 ${h.revision}？`,
                                message:
                                  "恢复会创建新版本，当前保存版本仍保留在历史中。未保存的修改会被替换。",
                              }))
                            )
                              return;
                            accept(
                              await api.post<Stage>(
                                `${base}/${stage.id}/history/${h.revision}/restore`,
                                { revision: stage.revision },
                              ),
                            );
                            await qc.invalidateQueries({
                              queryKey: ["director-history", stage.id],
                            });
                            await qc.invalidateQueries({
                              queryKey: ["director-list", projectId],
                            });
                          })
                        }
                      >
                        恢复 v{h.revision}
                      </Button>
                    ))}
                  </div>
                )}
                {historyOffset > 0 && (
                  <Button
                    variant="ghost"
                    onClick={() => setHistoryOffset(historyOffset - 50)}
                  >
                    较新版本
                  </Button>
                )}
                {history.data?.length === 50 && (
                  <Button
                    variant="ghost"
                    onClick={() => setHistoryOffset(historyOffset + 50)}
                  >
                    更早版本
                  </Button>
                )}
              </div>
            )}
            <div className="director-workspace">
              <aside className="director-scene-panel space-y-4">
                <h2 className="director-panel-title">
                  <Box className="h-4 w-4" /> 场景与布景
                </h2>
                <label className="block space-y-2 text-xs">
                  预演名称
                  <input
                    aria-label="预演名称"
                    className={input}
                    maxLength={120}
                    value={stage.name}
                    disabled={busy}
                    onChange={(e) =>
                      setStage({ ...stage, name: e.target.value })
                    }
                  />
                </label>
                <fieldset disabled={busy || playing} className="space-y-2">
                  <legend className="mb-2 text-xs text-muted-foreground">
                    从布景开始
                  </legend>
                  <Button
                    className="w-full"
                    variant="outline"
                    onClick={() =>
                      void perform(async () => {
                        if (
                          d.objects.length &&
                          !(await confirm({
                            title: "使用双人对话布景？",
                            message: "当前布景会替换，可通过撤销恢复。",
                          }))
                        )
                          return;
                        undo.current.push(d);
                        setStage({
                          ...stage,
                          document: sceneTemplate(d, "dialogue"),
                        });
                        redo.current = [];
                      })
                    }
                  >
                    双人对话
                  </Button>
                  <Button
                    className="w-full"
                    variant="outline"
                    onClick={() =>
                      void perform(async () => {
                        if (
                          d.objects.length &&
                          !(await confirm({
                            title: "使用室内布景？",
                            message: "当前布景会替换，可通过撤销恢复。",
                          }))
                        )
                          return;
                        undo.current.push(d);
                        setStage({
                          ...stage,
                          document: sceneTemplate(d, "interior"),
                        });
                        redo.current = [];
                      })
                    }
                  >
                    室内场景
                  </Button>
                </fieldset>
                <div className="director-object-list space-y-2">
                  <h2 className="text-xs text-muted-foreground">
                    场景对象 · {d.objects.length}/80
                  </h2>
                  {d.objects.map((o) => (
                    <button
                      key={o.id}
                      disabled={busy}
                      aria-pressed={o.id === selected}
                      className={`flex w-full items-center gap-2 rounded-lg border p-2 text-left text-xs ${o.id === selected ? "border-primary bg-primary/10" : "border-transparent hover:bg-muted"}`}
                      onClick={() => selectObject(o.id)}
                    >
                      <span
                        className="h-2 w-2 shrink-0 rounded-full"
                        style={{ background: o.color }}
                      />
                      <span className="truncate">{o.name}</span>
                    </button>
                  ))}
                  {!d.objects.length && (
                    <p className="py-5 text-xs leading-6 text-muted-foreground">
                      从上方选择布景，或添加人物、道具开始排练。
                    </p>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {(["person", "box", "sphere", "wall"] as const).map(
                    (kind, i) => (
                      <Button
                        variant="outline"
                        key={kind}
                        disabled={busy || playing || d.objects.length >= 80}
                        onClick={() => {
                          const o = {
                            ...person(
                              ["人物", "桌台", "球体", "墙面"][i],
                              (d.objects.length % 5) - 2,
                            ),
                            kind,
                          };
                          change({ ...d, objects: [...d.objects, o] });
                          selectObject(o.id);
                        }}
                      >
                        {["+ 人物", "+ 桌台", "+ 球体", "+ 墙面"][i]}
                      </Button>
                    ),
                  )}
                </div>
              </aside>
              <section className="director-stage-panel">
                <div className="director-viewport">
                  <DirectorViewport
                    ref={viewport}
                    document={d}
                    selected={selected}
                    view={view}
                    progress={progress}
                    disabled={busy || playing}
                    guides={guides}
                    onSelect={selectObject}
                    onMove={(id, position) =>
                      change({
                        ...d,
                        objects: d.objects.map((o) =>
                          o.id === id ? { ...o, position } : o,
                        ),
                      })
                    }
                  />
                  <div className="director-view-toolbar">
                    {(["layout", "camera"] as const).map((v, i) => (
                      <button
                        key={v}
                        className={`rounded px-3 py-2 text-xs ${v === view ? "bg-white/20" : ""}`}
                        aria-pressed={v === view}
                        disabled={busy}
                        onClick={() => {
                          setPlaying(false);
                          setView(v);
                        }}
                      >
                        {["布景视角", "相机取景"][i]}
                      </button>
                    ))}
                  </div>
                  <div className="director-view-actions">
                    <button
                      title="三分构图辅助线"
                      aria-label="构图辅助线"
                      aria-pressed={guides}
                      disabled={busy}
                      onClick={() => {
                        setGuides(!guides);
                        setView("camera");
                      }}
                    >
                      <Grid3X3 />
                    </button>
                    <button
                      title="聚焦选中对象 (F)"
                      aria-label="聚焦选中对象"
                      disabled={busy || !obj}
                      onClick={() => frame("selected")}
                    >
                      <Focus />
                    </button>
                    <button
                      title={focused ? "退出专注" : "专注画面"}
                      aria-label={focused ? "退出专注" : "专注画面"}
                      aria-pressed={focused}
                      onClick={() => setFocused(!focused)}
                    >
                      {focused ? <X /> : <Maximize2 />}
                    </button>
                  </div>
                  <div className="director-angle-toolbar">
                    {(["scene", "front", "top"] as const).map((mode, i) => (
                      <button
                        key={mode}
                        disabled={busy}
                        onClick={() => frame(mode)}
                      >
                        {["全景", "正面", "俯视"][i]}
                      </button>
                    ))}
                  </div>
                  <p className="director-view-hint">
                    {view === "layout"
                      ? "拖动旋转 · 右键平移 · 滚轮缩放 · 选中人物可拖动坐标轴"
                      : "实际取景 · 安全画框外不进入导出"}
                  </p>
                </div>
                <div className="director-playback">
                  <Button
                    aria-label={playing ? "暂停预演" : "播放预演"}
                    variant="ghost"
                    disabled={busy}
                    onClick={() => {
                      setView("camera");
                      if (progress >= 1) setProgress(0);
                      setPlaying(!playing);
                    }}
                  >
                    {playing ? (
                      <Pause className="h-4 w-4" />
                    ) : (
                      <Play className="h-4 w-4" />
                    )}
                  </Button>
                  <input
                    aria-label="预演进度"
                    className="min-w-0 flex-1 accent-primary"
                    type="range"
                    min={0}
                    max={1}
                    step={0.001}
                    value={progress}
                    disabled={busy}
                    onChange={(e) => {
                      setPlaying(false);
                      setProgress(+e.target.value);
                      setView("camera");
                    }}
                  />
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {(progress * d.duration).toFixed(1)} / {d.duration}s
                  </span>
                </div>
                <section
                  className="director-motion-strip"
                  aria-label="运镜关键帧"
                >
                  <div className="director-motion-heading">
                    <span>运镜 · {path.length} 个机位</span>
                    <Button
                      variant="ghost"
                      disabled={
                        busy ||
                        playing ||
                        insertionProgress(d, progress) === null
                      }
                      onClick={addFrame}
                    >
                      <Plus className="h-3 w-3" />
                      添加机位
                    </Button>
                  </div>
                  <div className="director-motion-track">
                    <div
                      className="director-motion-playhead"
                      style={{ left: `${progress * 100}%` }}
                    />
                    {path.map((point, index) => (
                      <button
                        key={point.id}
                        aria-label={`运镜机位 ${index + 1}`}
                        aria-pressed={point.id === activeFrame?.id}
                        title={`${index === 0 ? "起始" : index === path.length - 1 ? "结束" : `机位 ${index + 1}`} · ${(point.at * d.duration).toFixed(2)}s${point.hold ? ` · 停留 ${(point.hold * d.duration).toFixed(2)}s` : ""}`}
                        disabled={busy || playing}
                        style={{ left: `${point.at * 100}%` }}
                        onClick={() => chooseFrame(point)}
                      >
                        {index + 1}
                      </button>
                    ))}
                  </div>
                  <div className="director-motion-times">
                    <span>0s</span>
                    <span>拖动进度选择位置，再添加机位</span>
                    <span>{d.duration}s</span>
                  </div>
                </section>
                <div className="director-delivery">
                  <div className="director-panel-title">
                    <Camera className="h-4 w-4" /> 输出与分镜参考{" "}
                    <span className="ml-auto text-[10px] font-normal text-muted-foreground">
                      {d.aspect} · {d.duration}s
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        void perform(async () =>
                          download(
                            await viewport.current!.capture(),
                            `${stage.name}.png`,
                          ),
                        )
                      }
                    >
                      <Download className="mr-1 h-4 w-4" />
                      下载取景图
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy || dirty}
                      onClick={() =>
                        void perform(async () => {
                          if (dirty)
                            throw Error("请先保存场景，再导出运镜视频");
                          setView("camera");
                          const controller = new AbortController();
                          exportAbort.current = controller;
                          setExportProgress(0);
                          const frames = await viewport.current!.record(
                              setExportProgress,
                              controller.signal,
                            ),
                            form = new FormData();
                          form.append("revision", String(stage.revision));
                          frames.forEach((f, i) =>
                            form.append("frames", f, `${i}.jpg`),
                          );
                          const response = await fetch(
                            `/api/v1${base}/${stage.id}/previsualization`,
                            {
                              method: "POST",
                              headers: {
                                Authorization: `Bearer ${getToken() ?? ""}`,
                              },
                              body: form,
                              signal: controller.signal,
                            },
                          );
                          if (!response.ok) {
                            const error = await response
                              .json()
                              .catch(() => null);
                            throw Error(
                              error?.error?.message || "预演视频合成失败",
                            );
                          }
                          download(await response.blob(), `${stage.name}.mp4`);
                          setMessage("预演视频已导出");
                        })
                      }
                    >
                      导出运镜视频
                    </Button>
                    <select
                      aria-label="参考回填镜头"
                      className={`${input} max-w-60`}
                      value={target}
                      disabled={busy}
                      onChange={(e) => setTarget(e.target.value)}
                    >
                      <option value="">选择回填镜头</option>
                      {shots.data?.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.code} · {s.title || "未命名镜头"}
                        </option>
                      ))}
                    </select>
                    <Button
                      disabled={busy || !target || dirty}
                      onClick={() =>
                        void perform(async () => {
                          const blob = await viewport.current!.capture(),
                            form = new FormData();
                          form.append("file", blob, "director.png");
                          form.append("revision", String(stage.revision));
                          form.append("shot_id", target);
                          form.append("request_key", crypto.randomUUID());
                          form.append("progress", String(progress));
                          await apiUpload(
                            `${base}/${stage.id}/references`,
                            form,
                          );
                          await qc.invalidateQueries({
                            queryKey: ["generations"],
                          });
                          setMessage(
                            "取景图已加入镜头候选，可回到分镜比较后采用。",
                          );
                        })
                      }
                    >
                      加入镜头候选
                    </Button>
                  </div>
                  {shots.isError && (
                    <Button
                      variant="ghost"
                      disabled={busy}
                      onClick={() => void shots.refetch()}
                    >
                      镜头加载失败，点击重试
                    </Button>
                  )}
                  {!shots.isLoading &&
                    !shots.isError &&
                    !shots.data?.length && (
                      <p className="text-xs text-muted-foreground">
                        暂无可回填的镜头，可先在分镜中创建；取景图仍可下载。
                      </p>
                    )}
                  {exportProgress !== null && (
                    <div className="director-export-progress" role="status">
                      <progress
                        aria-label="预演导出进度"
                        max={1}
                        value={exportProgress}
                      />
                      <span>
                        {exportProgress < 1
                          ? `正在绘制画面 ${Math.round(exportProgress * 100)}%`
                          : "正在合成视频…"}
                      </span>
                      <Button
                        variant="ghost"
                        onClick={() => exportAbort.current?.abort()}
                      >
                        取消导出
                      </Button>
                    </div>
                  )}
                  <p className="text-xs leading-5 text-muted-foreground">
                    {busy
                      ? "正在处理，请稍候…"
                      : dirty
                        ? "回填前请先保存，参考画面会记录对应的场景版本。"
                        : "预演用于构图、站位与运镜参考。人物为简化替身，不代表最终角色外观。"}{" "}
                    运镜视频按当前机位路径录制，最长15秒。
                  </p>
                </div>
              </section>
              <aside className="director-inspector">
                <div
                  className="director-inspector-tabs"
                  role="tablist"
                  aria-label="预演参数"
                  onKeyDown={navigateTabs}
                >
                  {(["object", "camera", "light"] as const).map((tab, i) => (
                    <button
                      key={tab}
                      id={`director-tab-${tab}`}
                      role="tab"
                      tabIndex={inspector === tab ? 0 : -1}
                      aria-selected={inspector === tab}
                      aria-controls={`director-panel-${tab}`}
                      onClick={() => setInspector(tab)}
                    >
                      {i === 0 ? <Box /> : i === 1 ? <Camera /> : <Lightbulb />}
                      {["对象", "机位", "灯光"][i]}
                    </button>
                  ))}
                </div>
                <fieldset disabled={busy || playing} className="space-y-4">
                  <div
                    hidden={inspector !== "object"}
                    role="tabpanel"
                    id="director-panel-object"
                    aria-labelledby="director-tab-object"
                  >
                    {obj ? (
                      <section className="space-y-3">
                        <div className="flex items-center justify-between">
                          <h2 className="text-sm font-medium">对象设置</h2>
                          <Button
                            variant="ghost"
                            aria-label="复制选中对象"
                            title="复制 (Ctrl+D)"
                            disabled={d.objects.length >= 80}
                            onClick={duplicateObject}
                          >
                            <Copy className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            aria-label="删除选中对象"
                            onClick={removeObject}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                        <label className="block space-y-1 text-xs">
                          对象名称
                          <input
                            aria-label="对象名称"
                            className={input}
                            value={obj.name}
                            maxLength={120}
                            onChange={(e) =>
                              objectChange({ name: e.target.value })
                            }
                          />
                        </label>
                        <VectorFields
                          label="位置"
                          value={obj.position}
                          onChange={(position) => objectChange({ position })}
                        />
                        <div className="grid grid-cols-2 gap-2">
                          <NumberField
                            label="朝向角度"
                            value={obj.rotation}
                            onChange={(rotation) => objectChange({ rotation })}
                            min={-360}
                            max={360}
                            step={5}
                          />
                          <NumberField
                            label="对象大小"
                            value={obj.scale}
                            onChange={(scale) => objectChange({ scale })}
                            min={0.1}
                            max={20}
                          />
                        </div>
                        <label className="flex items-center justify-between text-xs">
                          对象颜色
                          <input
                            aria-label="对象颜色"
                            type="color"
                            value={obj.color}
                            onChange={(e) =>
                              objectChange({ color: e.target.value })
                            }
                          />
                        </label>
                        {obj.kind === "person" && (
                          <label className="block space-y-1 text-xs">
                            姿态
                            <select
                              aria-label="人物姿态"
                              className={input}
                              value={obj.pose}
                              onChange={(e) =>
                                objectChange({
                                  pose: e.target.value as StageObject["pose"],
                                })
                              }
                            >
                              <option value="standing">站立</option>
                              <option value="sitting">坐姿</option>
                            </select>
                          </label>
                        )}
                      </section>
                    ) : (
                      <p className="text-xs leading-6 text-muted-foreground">
                        选中人物或道具，调整位置、朝向和大小。
                      </p>
                    )}
                  </div>
                  <section
                    hidden={inspector !== "camera"}
                    role="tabpanel"
                    id="director-panel-camera"
                    aria-labelledby="director-tab-camera"
                    className="space-y-3"
                  >
                    <h2 className="text-sm font-medium">机位与运镜</h2>
                    <div className="flex flex-wrap gap-2">
                      {path.map((point, i) => (
                        <Button
                          key={point.id}
                          variant={
                            activeFrame.id === point.id ? "default" : "outline"
                          }
                          onClick={() => chooseFrame(point)}
                        >
                          {i === 0
                            ? "起始机位"
                            : i === path.length - 1
                              ? "结束机位"
                              : `机位 ${i + 1}`}
                        </Button>
                      ))}
                    </div>
                    {activeFrame.id !== "camera" &&
                      activeFrame.id !== "end_camera" && (
                        <div className="director-keyframe-time">
                          <NumberField
                            label="到达时间（秒）"
                            value={Number(
                              (activeFrame.at * d.duration).toFixed(4),
                            )}
                            min={
                              (previousFrame.at + previousFrame.hold + 0.01) *
                              d.duration
                            }
                            max={
                              (nextFrame.at - activeFrame.hold - 0.01) *
                              d.duration
                            }
                            step={0.1}
                            onChange={(seconds) =>
                              updateFrame({ at: seconds / d.duration })
                            }
                          />
                          <Button
                            variant="ghost"
                            aria-label="删除当前机位"
                            onClick={() => {
                              change({
                                ...d,
                                camera_keyframes: (
                                  d.camera_keyframes ?? []
                                ).filter((k) => k.id !== activeFrame.id),
                              });
                              chooseFrame(previousFrame);
                            }}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      )}
                    {nextFrame && (
                      <div className="grid grid-cols-2 gap-2">
                        <NumberField
                          label="停留时间（秒）"
                          value={Number(
                            (activeFrame.hold * d.duration).toFixed(4),
                          )}
                          min={0}
                          max={Math.max(
                            0,
                            (nextFrame.at - activeFrame.at - 0.01) * d.duration,
                          )}
                          step={0.1}
                          onChange={(seconds) =>
                            updateFrame({ hold: seconds / d.duration })
                          }
                        />
                        <label className="space-y-1 text-xs text-muted-foreground">
                          <span>前往下一机位</span>
                          <select
                            aria-label="运镜速度曲线"
                            className={input}
                            value={activeFrame.ease}
                            onChange={(e) =>
                              updateFrame({
                                ease: e.target.value as CameraKeyframe["ease"],
                              })
                            }
                          >
                            <option value="smooth">平滑起止</option>
                            <option value="linear">匀速</option>
                          </select>
                        </label>
                      </div>
                    )}
                    <Button
                      variant="outline"
                      className="w-full"
                      disabled={view !== "layout"}
                      onClick={() => updateCamera(viewport.current!.camera())}
                    >
                      将布景视角设为此机位
                    </Button>
                    {view !== "layout" && (
                      <p className="text-[11px] leading-5 text-muted-foreground">
                        切到布景视角，找到构图后可设为当前机位。
                      </p>
                    )}
                    <Button
                      variant="ghost"
                      className="w-full"
                      onClick={() =>
                        change({
                          ...d,
                          [activeFrame.id === "camera"
                            ? "end_camera"
                            : "camera"]: structuredClone(activeFrame.camera),
                        })
                      }
                    >
                      复制到{activeFrame.id === "camera" ? "结束" : "起始"}机位
                    </Button>
                    <VectorFields
                      label="机位"
                      value={activeFrame.camera.position}
                      onChange={(position) => updateCamera({ position })}
                    />
                    <VectorFields
                      label="看向"
                      value={activeFrame.camera.target}
                      onChange={(target) => updateCamera({ target })}
                    />
                    <div className="grid grid-cols-2 gap-2">
                      <NumberField
                        label="视角度数"
                        value={activeFrame.camera.fov}
                        min={15}
                        max={100}
                        step={1}
                        onChange={(fov) => updateCamera({ fov })}
                      />
                      <NumberField
                        label="预演秒数"
                        value={d.duration}
                        min={1}
                        max={15}
                        step={1}
                        onChange={(duration) => change({ ...d, duration })}
                      />
                    </div>
                    {path.length > 2 && (
                      <p className="text-[11px] leading-5 text-muted-foreground">
                        调整预演总时长，会按比例调整各机位的到达与停留时间。最多
                        12 个机位。
                      </p>
                    )}
                    <label className="block space-y-1 text-xs">
                      画面比例
                      <select
                        aria-label="画面比例"
                        className={input}
                        value={d.aspect}
                        onChange={(e) =>
                          change({
                            ...d,
                            aspect: e.target.value as StageDocument["aspect"],
                          })
                        }
                      >
                        <option>16:9</option>
                        <option>9:16</option>
                        <option>1:1</option>
                      </select>
                    </label>
                  </section>
                  <section
                    hidden={inspector !== "light"}
                    role="tabpanel"
                    id="director-panel-light"
                    aria-labelledby="director-tab-light"
                    className="space-y-3"
                  >
                    <h2 className="text-sm font-medium">灯光与环境</h2>
                    <VectorFields
                      label="灯光"
                      value={d.lighting.position}
                      onChange={(position) =>
                        change({ ...d, lighting: { ...d.lighting, position } })
                      }
                    />
                    <div className="grid grid-cols-2 gap-2">
                      <NumberField
                        label="主光强度"
                        min={0}
                        max={10}
                        value={d.lighting.intensity}
                        onChange={(intensity) =>
                          change({
                            ...d,
                            lighting: { ...d.lighting, intensity },
                          })
                        }
                      />
                      <NumberField
                        label="环境亮度"
                        min={0}
                        max={3}
                        value={d.lighting.ambient}
                        onChange={(ambient) =>
                          change({ ...d, lighting: { ...d.lighting, ambient } })
                        }
                      />
                    </div>
                    <label className="flex items-center justify-between text-xs">
                      灯光颜色
                      <input
                        aria-label="灯光颜色"
                        type="color"
                        value={d.lighting.color}
                        onChange={(e) =>
                          change({
                            ...d,
                            lighting: { ...d.lighting, color: e.target.value },
                          })
                        }
                      />
                    </label>
                    {(["background", "ground"] as const).map((k, i) => (
                      <label
                        key={k}
                        className="flex items-center justify-between text-xs"
                      >
                        {["背景颜色", "地面颜色"][i]}
                        <input
                          aria-label={["背景颜色", "地面颜色"][i]}
                          type="color"
                          value={d[k]}
                          onChange={(e) =>
                            change({ ...d, [k]: e.target.value })
                          }
                        />
                      </label>
                    ))}
                  </section>
                </fieldset>
              </aside>
            </div>
          </>
        )
      )}
    </div>
  );
}
