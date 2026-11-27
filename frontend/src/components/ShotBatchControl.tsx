import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { SelectField } from "@/components/ui/select-field";
import { api, type ProviderConfig, type Shot } from "@/lib/api";
import { shotName } from "@/lib/shotLabels";

type Item = {
  id: string;
  shot_id: string;
  title: string;
  job_id: string | null;
  status: string;
  error?: string;
  action?: string;
  auto_retry_pending?: boolean;
  attempts: number;
};
type Batch = { id: string; media_type: string; items: Item[] };
const statusText: Record<string, string> = {
  pending: "排队中",
  submitted: "已提交",
  running: "生成中",
  succeeded: "已完成",
  failed: "失败",
  canceled: "已取消",
};

export function ShotBatchControl({
  projectId,
  sceneId,
  shots,
  onClose,
}: {
  projectId: string;
  sceneId: string;
  shots: Shot[];
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<string[]>(() => suggestedShots("image"));
  const [media, setMedia] = useState("image");
  const [manualModel, setManualModel] = useState("");
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  const [history, setHistory] = useState(true);
  const [pickedShots, setPickedShots] = useState(false);
  const base = `/projects/${projectId}`;
  const path = `${base}/scenes/${sceneId}/generation-batches`;
  const runs = useQuery({
    queryKey: ["shot-batches", projectId, sceneId],
    queryFn: () => api.get<Batch[]>(path),
    refetchInterval: (q) =>
      q.state.data?.some((b) =>
        b.items.some(
          (i) =>
            i.auto_retry_pending ||
            !["succeeded", "failed", "canceled"].includes(i.status),
        ),
      )
        ? 3000
        : false,
  });
  const models = useQuery({
    queryKey: ["providers", projectId],
    queryFn: () => api.get<ProviderConfig[]>(`${base}/providers`),
  });
  const available = (models.data ?? []).filter(
    (p) =>
      p.enabled &&
      p.kind === "generation" &&
      !p.config.hidden_from_catalog &&
      p.config.modality === media,
  );
  const provider =
    manualModel ||
    available.find((p) => p.config.is_default)?.provider_name ||
    available[0]?.provider_name ||
    "";
  const create = useMutation({
    mutationFn: () =>
      api.post<Batch>(path, {
        request_key: requestKey,
        provider,
        media_type: media,
        shot_ids: selected,
      }),
    onSuccess: async () => {
      setHistory(true);
      await runs.refetch();
    },
  });
  const retry = useMutation({
    mutationFn: (i: Item) =>
      api.post(`${base}/shot-batch-items/${i.id}/retry`, { job_id: i.job_id }),
    onSuccess: () => runs.refetch(),
  });
  function suggestedShots(type: string) {
    return shots
      .filter((s) => type === "video" || !s.selected_generation_id)
      .slice(0, 100)
      .map((s) => s.id);
  }
  function newBatch() {
    setRequestKey(crypto.randomUUID());
    setHistory(false);
    setSelected(suggestedShots(media));
    setPickedShots(false);
    create.reset();
    retry.reset();
  }
  const showRuns = history && !!runs.data?.length;
  return (
      <Modal
        open
        onClose={onClose}
        title="批量生成镜头"
        width={630}
      >
        {runs.isLoading ? (
          <p role="status">正在读取镜头任务…</p>
        ) : showRuns ? (
          <>
            <Button variant="outline" size="sm" onClick={newBatch}>
              选择镜头生成
            </Button>
            {runs.data!.map((run, index) => (
              <details
                key={run.id}
                open={index === 0}
                className="my-3 rounded-lg border p-3"
              >
                <summary>
                  {run.media_type === "video" ? "视频" : "画面"} ·{" "}
                  {run.items.filter((i) => i.status === "succeeded").length} /{" "}
                  {run.items.length} 已完成
                  {run.items.some((i) => i.status === "failed") &&
                    " · 有待处理镜头"}
                </summary>
                <ul className="mt-3 space-y-3">
                  {run.items.map((i) => (
                    <li key={i.id} className="border-t pt-2 text-sm">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <Link to={`${base}/shots?shot=${i.shot_id}&tab=gen`}>
                          {i.title}
                        </Link>
                        <span>
                          {i.auto_retry_pending
                            ? "限流，等待重试"
                            : statusText[i.status] || i.status}
                          {i.attempts > 1 && " · 已重试"}
                        </span>
                      </div>
                      {i.error && (
                        <p className="text-muted-foreground">{i.error}</p>
                      )}
                      {i.action === "edit" && (
                        <Link
                          className="underline"
                          to={`${base}/shots?shot=${i.shot_id}&tab=gen`}
                        >
                          调整镜头或生成参数
                        </Link>
                      )}
                      {i.action === "verify" && <span>请核对供应商记录</span>}
                      {["receive", "retry", "prepare"].includes(
                        i.action || "",
                      ) && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={retry.isPending}
                          onClick={() => retry.mutate(i)}
                        >
                          {i.action === "receive"
                            ? "取回原任务结果"
                            : "仅重试此镜头"}
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            ))}
          </>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-3">
              <SelectField
                label="生成内容"
                value={media}
                onChange={(v) => {
                  setMedia(v);
                  setManualModel("");
                  if (!pickedShots) setSelected(suggestedShots(v));
                  setRequestKey(crypto.randomUUID());
                }}
                options={[
                  { value: "image", label: "镜头画面" },
                  { value: "video", label: "镜头视频" },
                ]}
              />
              <SelectField
                label="生成模型"
                value={provider}
                onChange={(v) => {
                  setManualModel(v);
                  setRequestKey(crypto.randomUUID());
                }}
                options={available.map((p) => ({
                  value: p.provider_name,
                  label:
                    p.display_name || String(p.config.model || p.provider_name),
                }))}
              />
            </div>
            {!provider && !models.isLoading && (
              <p>
                请先在模型服务中配置{media === "image" ? "图片" : "视频"}模型。
              </p>
            )}
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {shots.map((s) => (
                <label key={s.id} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={selected.includes(s.id)}
                    disabled={
                      create.isPending ||
                      (!selected.includes(s.id) && selected.length >= 100)
                    }
                    onChange={(e) => {
                      setPickedShots(true);
                      setSelected(
                        e.target.checked
                          ? [...selected, s.id]
                          : selected.filter((id) => id !== s.id),
                      );
                      setRequestKey(crypto.randomUUID());
                    }}
                  />
                  {shotName(s)}
                  {s.selected_generation_id && <small>已采用画面</small>}
                </label>
              ))}
            </div>
            <p className="text-sm text-muted-foreground">
              每镜生成一个候选，按所选模型计费。相同输入会复用已有任务。
            </p>
            {media === "video" && (
              <p className="text-sm text-muted-foreground">
                支持首帧的视频模型会沿用镜头已采用的图片。
              </p>
            )}
            <Button
              disabled={
                !provider ||
                !selected.length ||
                selected.length > 100 ||
                create.isPending
              }
              onClick={() => create.mutate()}
            >
              {create.isPending
                ? "提交中…"
                : `生成选中的 ${selected.length} 个镜头`}
            </Button>
            {!!runs.data?.length && (
              <Button variant="ghost" onClick={() => setHistory(true)}>
                查看任务
              </Button>
            )}
          </div>
        )}
        {(create.error || retry.error || runs.error || models.error) && (
          <p role="alert">
            {
              (create.error || retry.error || runs.error || models.error)
                ?.message
            }
          </p>
        )}
      </Modal>
  );
}
