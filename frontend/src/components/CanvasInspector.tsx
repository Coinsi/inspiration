import { useState } from "react";
import { Link } from "react-router-dom";
import type { UseQueryResult } from "@tanstack/react-query";
import type { Connection } from "@xyflow/react";
import { History, MousePointer2 } from "lucide-react";
import { api, type Asset, type Shot, type ProviderConfig } from "@/lib/api";
import { Button } from "@/components/ui/button";
import type { ConfirmOptions } from "@/components/ui/confirm";
import { CanvasRunMedia } from "@/components/CanvasMedia";
import {
  labels,
  statuses,
  validConnection,
  type CanvasDoc,
  type CanvasNode,
  type Graph,
  type NodeData,
  type Run,
} from "@/lib/canvas";
const field =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary";
type Panel = "edit" | "runs" | "history";
type Props = {
  projectId: string;
  path: string;
  graph: Graph;
  selected?: CanvasNode;
  busy: boolean;
  panel: Panel;
  setPanel: (p: Panel) => void;
  update: (d: Partial<NodeData>) => void;
  connect: (c: Connection) => void;
  run?: Run;
  runs: UseQueryResult<Run[], Error>;
  history: UseQueryResult<{ revision: number; created_at: string }[], Error>;
  revision: number;
  viewRun: string;
  setViewRun: (v: string) => void;
  runOffset: number;
  setRunOffset: (v: number | ((n: number) => number)) => void;
  historyOffset: number;
  setHistoryOffset: (v: number | ((n: number) => number)) => void;
  assets: UseQueryResult<Asset[], Error>;
  shots: UseQueryResult<Shot[], Error>;
  providers: UseQueryResult<ProviderConfig[], Error>;
  perform: (fn: () => Promise<void>) => Promise<void>;
  accept: (c: CanvasDoc) => void;
  dirty: boolean;
  confirm: (o?: ConfirmOptions) => Promise<boolean>;
  ungroup: () => void;
};
export function CanvasInspector({
  projectId,
  path,
  graph,
  selected,
  busy,
  panel,
  setPanel,
  update,
  connect,
  run,
  runs,
  history,
  revision,
  setViewRun,
  runOffset,
  setRunOffset,
  historyOffset,
  setHistoryOffset,
  assets,
  shots,
  providers,
  perform,
  accept,
  dirty,
  confirm,
  ungroup,
}: Props) {
  const base = `/projects/${projectId}`;
  return (
    <aside className="canvas-inspector">
      <div className="flex border-b border-border p-2 gap-1">
        {(["edit", "runs", "history"] as const).map((p) => (
          <Button
            size="sm"
            variant={p === panel ? "default" : "ghost"}
            key={p}
            onClick={() => setPanel(p)}
          >
            {p === "edit" ? "节点" : p === "runs" ? "运行记录" : "保存历史"}
          </Button>
        ))}
      </div>
      <div className="p-4 space-y-4">
        {panel === "edit" &&
          (selected ? (
            <>
              <span className="text-xs tracking-widest text-muted-foreground">
                {labels[selected.data.kind]}
              </span>
              <label className="block text-xs space-y-2">
                <span>节点名称</span>
                <input
                  className={field}
                  value={selected.data.label}
                  maxLength={120}
                  disabled={busy}
                  onChange={(e) => update({ label: e.target.value })}
                />
              </label>
              {selected.data.kind !== "group" && (
                <label className="block text-xs space-y-2">
                  <span>
                    {selected.data.kind === "generate" ? "提示词" : "创作说明"}
                  </span>
                  <textarea
                    className={`${field} min-h-32 resize-y`}
                    value={selected.data.text}
                    maxLength={10000}
                    disabled={busy}
                    onChange={(e) =>
                      update({
                        text: e.target.value,
                        ...(selected.data.kind === "generate"
                          ? {
                              generate: {
                                ...selected.data.generate,
                                prompt_override: null,
                              },
                            }
                          : {}),
                      })
                    }
                  />
                </label>
              )}
              {!["text", "group"].includes(selected.data.kind) && (
                <>
                  {selected.data.kind === "generate" && (
                    <label className="block text-xs space-y-2">
                      <span>结果归属</span>
                      <select
                        className={field}
                        value={selected.data.target_type}
                        disabled={busy}
                        onChange={(e) =>
                          update({
                            target_type: e.target.value as "asset" | "shot",
                            target_id: null,
                            generation_id: null,
                          })
                        }
                      >
                        <option value="shot">镜头</option>
                        <option value="asset">角色与资产</option>
                      </select>
                    </label>
                  )}
                  <label className="block text-xs space-y-2">
                    <span>引用项目对象</span>
                    <select
                      className={field}
                      value={selected.data.target_id ?? ""}
                      disabled={busy}
                      onChange={(e) => {
                        const id = e.target.value;
                        const item =
                          assets.data?.find((a) => a.id === id) ||
                          shots.data?.find((s) => s.id === id);
                        update({
                          target_id: id || null,
                          generation_id: null,
                          ...(item
                            ? {
                                label:
                                  "name" in item
                                    ? item.name
                                    : item.title || item.code,
                              }
                            : {}),
                        });
                      }}
                    >
                      <option value="">请选择…</option>
                      {selected.data.kind === "asset" ||
                      (selected.data.kind === "generate" &&
                        selected.data.target_type === "asset")
                        ? assets.data?.map((a) => (
                            <option key={a.id} value={a.id}>
                              {a.code} · {a.name}
                            </option>
                          ))
                        : shots.data?.map((s) => (
                            <option key={s.id} value={s.id}>
                              {s.code} · {s.title}
                            </option>
                          ))}
                    </select>
                  </label>
                  {(assets.error || shots.error) && (
                    <p role="alert" className="text-xs text-danger">
                      项目对象加载失败，请重新载入页面。
                    </p>
                  )}
                  {selected.data.target_id && (
                    <Link
                      className="text-xs text-primary underline"
                      to={`${base}/${selected.data.target_type === "asset" ? `assets/${selected.data.target_id}` : "shots"}`}
                    >
                      打开原始对象 ↗
                    </Link>
                  )}
                </>
              )}
              {selected.data.kind === "generate" && (
                <>
                  <label className="block text-xs space-y-2">
                    <span>生成类型</span>
                    <select
                      className={field}
                      value={selected.data.generate.request_type}
                      disabled={busy}
                      onChange={(e) =>
                        update({
                          generate: {
                            ...selected.data.generate,
                            request_type: e.target.value as
                              "image" | "video" | "audio",
                          },
                        })
                      }
                    >
                      <option value="image">图片</option>
                      <option value="video">视频</option>
                      <option value="audio">声音</option>
                    </select>
                  </label>
                  <label className="block text-xs space-y-2">
                    <span>供应商</span>
                    <select
                      className={field}
                      value={selected.data.generate.provider}
                      disabled={busy}
                      onChange={(e) => {
                        const model = providers.data?.find(
                          (p) => p.provider_name === e.target.value,
                        );
                        update({
                          generate: {
                            ...selected.data.generate,
                            provider: e.target.value,
                            request_type:
                              model?.config.modality === "video"
                                ? "video"
                                : model?.config.modality === "image"
                                  ? "image"
                                  : selected.data.generate.request_type,
                            count:
                              model?.config.protocol === "openai_video"
                                ? 1
                                : selected.data.generate.count,
                            provider_params: {},
                          },
                        });
                      }}
                    >
                      <option value="mock">离线测试 · 不是真实生成</option>
                      {providers.data
                        ?.filter(
                          (p) =>
                            p.enabled &&
                            p.kind !== "llm" &&
                            p.provider_name !== "mock",
                        )
                        .map((p) => (
                          <option key={p.id} value={p.provider_name}>
                            {p.channel_name
                              ? `${p.channel_name} / ${p.display_name}`
                              : p.provider_name}
                          </option>
                        ))}
                    </select>
                  </label>
                  {providers.error && (
                    <p className="text-xs text-danger" role="alert">
                      供应商列表加载失败
                    </p>
                  )}
                  {Object.entries(
                    (providers.data?.find(
                      (p) =>
                        p.provider_name === selected.data.generate.provider,
                    )?.config.param_schema || {}) as Record<
                      string,
                      { enum?: (string | number)[] }
                    >,
                  ).map(([name, schema]) => (
                    <label key={name} className="block text-xs space-y-2">
                      <span>
                        {{
                          size: "画面尺寸",
                          quality: "图片质量",
                          duration: "时长（秒）",
                          aspect_ratio: "画面比例",
                          resolution: "清晰度",
                        }[name] || name}
                      </span>
                      <select
                        className={field}
                        disabled={busy}
                        value={String(
                          selected.data.generate.provider_params[name] ?? "",
                        )}
                        onChange={(e) => {
                          const params = {
                            ...selected.data.generate.provider_params,
                          };
                          if (!e.target.value) delete params[name];
                          else
                            params[name] =
                              name === "duration"
                                ? Number(e.target.value)
                                : e.target.value;
                          update({
                            generate: {
                              ...selected.data.generate,
                              provider_params: params,
                            },
                          });
                        }}
                      >
                        <option value="">服务默认</option>
                        {schema.enum?.map((v) => (
                          <option key={v} value={v}>
                            {v}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                  <label className="block text-xs space-y-2">
                    <span>候选数量</span>
                    <input
                      className={field}
                      type="number"
                      min={1}
                      max={8}
                      value={selected.data.generate.count}
                      disabled={busy}
                      onChange={(e) =>
                        update({
                          generate: {
                            ...selected.data.generate,
                            count: Math.max(
                              1,
                              Math.min(8, Number(e.target.value) || 1),
                            ),
                          },
                        })
                      }
                    />
                  </label>
                  <p className="text-xs leading-5 text-muted-foreground">
                    先完成上游，再运行此节点。多个候选都保留，下游图片输入使用第一个结果。结果归入原始对象的生成记录，采用时由你选择。
                  </p>
                </>
              )}
              {selected.parentId && (
                <Button variant="outline" disabled={busy} onClick={ungroup}>
                  移出分组
                </Button>
              )}
              {selected.data.kind !== "group" && (
                <div className="border-t border-border pt-3">
                  <p className="text-xs mb-2 text-muted-foreground">
                    连线也可以用下面的表单建立
                  </p>
                  <ConnectForm
                    key={selected.id}
                    graph={graph}
                    selected={selected}
                    busy={busy}
                    connect={connect}
                  />
                </div>
              )}
            </>
          ) : (
            <div className="py-12 text-center text-sm text-muted-foreground">
              <MousePointer2 className="mx-auto mb-4" />
              <p>选择节点，编辑内容与参考</p>
              <p className="mt-3 text-xs leading-5">
                拖动空白区域框选，Shift
                点选多个节点，按住空格平移。蓝色端口传递文字，紫色端口传递图片。
              </p>
            </div>
          ))}
        {panel === "runs" && (
          <>
            <h2 className="font-medium">执行批次</h2>
            {runs.error && (
              <p role="alert" className="text-danger">
                {runs.error.message}
              </p>
            )}
            {runs.isLoading ? (
              <p>加载中…</p>
            ) : !runs.data?.length ? (
              <p className="text-sm text-muted-foreground">
                运行后会在这里保留每一步的任务、结果和错误。
              </p>
            ) : (
              <>
                <select
                  aria-label="查看运行批次"
                  className={field}
                  value={run?.id}
                  onChange={(e) => setViewRun(e.target.value)}
                >
                  {runs.data?.map((r) => (
                    <option value={r.id} key={r.id}>
                      版本 {r.revision} ·{" "}
                      {r.target_node_id ? "节点生成" : "整个画布"} ·{" "}
                      {statuses[r.status]}
                    </option>
                  ))}
                </select>
                {run && (
                  <>
                    <div className="flex flex-wrap gap-2">
                      {run.status === "running" && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() =>
                            void perform(async () => {
                              await api.post(`${path}/runs/${run.id}/control`, {
                                action: "pause",
                              });
                              await runs.refetch();
                            })
                          }
                        >
                          暂停后续节点
                        </Button>
                      )}
                      {run.status === "paused" && (
                        <Button
                          size="sm"
                          disabled={busy}
                          onClick={() =>
                            void perform(async () => {
                              await api.post(`${path}/runs/${run.id}/control`, {
                                action: "resume",
                              });
                              await runs.refetch();
                            })
                          }
                        >
                          继续运行
                        </Button>
                      )}
                      {run.status === "failed" && (
                        <Button
                          size="sm"
                          disabled={busy}
                          onClick={() =>
                            void perform(async () => {
                              if (
                                !(await confirm({
                                  title: "重试失败节点",
                                  message:
                                    "只重新执行失败节点。已发送的云端请求可能已经计费，重试可能产生新的费用。",
                                }))
                              )
                                return;
                              await api.post(`${path}/runs/${run.id}/control`, {
                                action: "retry",
                              });
                              await runs.refetch();
                            })
                          }
                        >
                          重试失败节点
                        </Button>
                      )}
                      {["running", "paused", "failed"].includes(run.status) && (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          onClick={() =>
                            void perform(async () => {
                              await api.post(`${path}/runs/${run.id}/control`, {
                                action: "cancel",
                              });
                              await runs.refetch();
                            })
                          }
                        >
                          取消批次
                        </Button>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      暂停会保留正在进行的生成，阻止后续节点启动。批次使用版本{" "}
                      {run.revision} 的固定输入。
                    </p>
                    {run.error && (
                      <p role="alert" className="text-xs text-danger">
                        {run.error}
                      </p>
                    )}
                    {Object.entries(run.steps).map(([id, step]) => (
                      <div
                        key={id}
                        className="rounded-lg border border-border p-3 space-y-2"
                      >
                        <div className="flex justify-between gap-2 text-xs">
                          <span className="truncate">
                            {graph.nodes.find((n) => n.id === id)?.data.label ||
                              "历史节点"}
                          </span>
                          <span
                            className={`canvas-status status-${step.status}`}
                          >
                            {statuses[step.status]}
                          </span>
                        </div>
                        {step.output?.blob_hash && (
                          <div className="h-32 rounded overflow-hidden">
                            <CanvasRunMedia
                              projectId={projectId}
                              node={graph.nodes.find((n) => n.id === id)}
                              generationId={step.output.generation_id}
                              hash={step.output.blob_hash}
                            />
                          </div>
                        )}
                        {step.error && (
                          <p className="text-xs text-danger">{step.error}</p>
                        )}
                        {step.job_id && (
                          <Link
                            to={`${base}/tasks?job=${step.job_id}`}
                            className="text-xs text-primary"
                          >
                            查看任务与全部候选 ↗
                          </Link>
                        )}
                      </div>
                    ))}
                  </>
                )}
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={!runOffset}
                    onClick={() => setRunOffset((x) => Math.max(0, x - 20))}
                  >
                    较新批次
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={runs.data?.length !== 20}
                    onClick={() => setRunOffset((x) => x + 20)}
                  >
                    较早批次
                  </Button>
                </div>
              </>
            )}
          </>
        )}
        {panel === "history" && (
          <>
            <div className="flex items-center gap-2">
              <History size={16} />
              <h2 className="font-medium">保存历史</h2>
            </div>
            <p className="text-xs text-muted-foreground">
              恢复会创建新版本，已运行的批次保留原始记录。
            </p>
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  if (
                    dirty &&
                    !(await confirm({
                      message: "重新载入将放弃当前未保存的修改，是否继续？",
                    }))
                  )
                    return;
                  accept(await api.get<CanvasDoc>(path));
                })
              }
            >
              重新载入服务器版本
            </Button>
            {history.error && (
              <p className="text-danger" role="alert">
                {history.error.message}
              </p>
            )}
            {history.data?.map((h) => (
              <div
                className="flex justify-between items-center border-b border-border py-2"
                key={h.revision}
              >
                <div className="text-xs">
                  <div>版本 {h.revision}</div>
                  <div className="text-muted-foreground mt-1">
                    {new Date(h.created_at).toLocaleString()}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy || h.revision === revision}
                  onClick={() =>
                    void perform(async () => {
                      if (
                        !(await confirm({
                          message: `恢复版本 ${h.revision}？当前未保存的修改将被替换。`,
                        }))
                      )
                        return;
                      accept(
                        await api.post<CanvasDoc>(
                          `${path}/history/${h.revision}/restore`,
                          { revision },
                        ),
                      );
                      await history.refetch();
                    })
                  }
                >
                  恢复
                </Button>
              </div>
            ))}
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="ghost"
                disabled={!historyOffset}
                onClick={() => setHistoryOffset((x) => Math.max(0, x - 50))}
              >
                较新版本
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={history.data?.length !== 50}
                onClick={() => setHistoryOffset((x) => x + 50)}
              >
                较早版本
              </Button>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
function ConnectForm({
  graph,
  selected,
  busy,
  connect,
}: {
  graph: Graph;
  selected: CanvasNode;
  busy: boolean;
  connect: (c: Connection) => void;
}) {
  const [target, setTarget] = useState(""),
    [port, setPort] = useState("text");
  const targetPort = port === "text" ? "prompt" : "reference";
  return (
    <div className="space-y-2">
      <select
        aria-label="连接输出类型"
        className={field}
        value={port}
        onChange={(e) => setPort(e.target.value)}
      >
        <option value="text">文字输出</option>
        {selected.data.kind !== "text" && selected.data.kind !== "group" && (
          <option value="image">图片输出</option>
        )}
      </select>
      <select
        aria-label="连接到节点"
        className={field}
        value={target}
        onChange={(e) => setTarget(e.target.value)}
      >
        <option value="">选择下游生成节点</option>
        {graph.nodes
          .filter((n) => n.data.kind === "generate" && n.id !== selected.id)
          .map((n) => (
            <option value={n.id} key={n.id}>
              {n.data.label}
            </option>
          ))}
      </select>
      <Button
        variant="outline"
        size="sm"
        disabled={
          busy || !validConnection(graph, selected.id, target, port, targetPort)
        }
        onClick={() =>
          connect({
            source: selected.id,
            target,
            sourceHandle: port,
            targetHandle: targetPort,
          })
        }
      >
        建立连线
      </Button>
    </div>
  );
}
