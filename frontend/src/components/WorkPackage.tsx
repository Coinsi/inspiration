import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, blobUrl, getToken } from "@/lib/api";
import { Button } from "./ui/button";
import { SelectField } from "./ui/select-field";
import { MediaAudio } from "./MediaAudio";
import { MediaVideo } from "./MediaVideo";

export function WorkPackage({
  projectId,
  workId,
}: {
  projectId: string;
  workId: string;
}) {
  const path = `/projects/${projectId}/works/${workId}/engineering`;
  const picker = useRef<HTMLInputElement>(null);
  const resumeId = useRef<string | null>(null);
  const refreshedImports = useRef(new Set<string>());
  const cache = useQueryClient();
  const [message, setMessage] = useState("");
  const jobs = useQuery({
    queryKey: [path, "archive-jobs"],
    queryFn: () =>
      api.get<
        Array<{
          id: string;
          revision: number;
          status: string;
          available: boolean;
          operation: "export" | "import";
          filename: string | null;
          expected_bytes: number;
          uploaded_bytes: number;
          retention_days: number;
          error: string | null;
          progress: {
            phase?: string;
            completed_bytes?: number;
            total_bytes?: number;
          };
        }>
      >(`${path}/archive-jobs`),
    refetchInterval: (query) =>
      query.state.data?.some((job) =>
        ["uploading", "pending", "running"].includes(job.status),
      )
        ? 2000
        : false,
  });
  useEffect(() => {
    const finished =
      jobs.data?.filter(
        (job) =>
          job.operation === "import" &&
          job.status === "succeeded" &&
          !refreshedImports.current.has(job.id),
      ) || [];
    if (finished.length) {
      finished.forEach((job) => refreshedImports.current.add(job.id));
      void cache.invalidateQueries();
    }
  }, [jobs.data, cache]);
  const archive = useMutation({
    mutationFn: () =>
      api.post(`${path}/archive-jobs?request_key=${crypto.randomUUID()}`, {}),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: [path, "archive-jobs"] });
      setMessage("正在后台准备归档，离开页面后仍会继续。完成后可在这里下载。");
    },
  });
  const manageArchive = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "cancel" | "retry" }) =>
      api.post(`${path}/archive-jobs/${id}/${action}`, {}),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: [path, "archive-jobs"] });
    },
  });
  const saveArchive = useMutation({
    mutationFn: async (id: string) => {
      const pickerWindow = window as unknown as {
        showSaveFilePicker?: (
          options: unknown,
        ) => Promise<{ createWritable(): Promise<WritableStream<Uint8Array>> }>;
      };
      const handle = pickerWindow.showSaveFilePicker
        ? await pickerWindow.showSaveFilePicker({
            suggestedName: `work-${workId}.inspiration.zip`,
          })
        : null;
      const response = await fetch(
        `/api/v1${path}/archive-jobs/${id}/download`,
        {
          headers: { Authorization: `Bearer ${getToken() || ""}` },
        },
      );
      if (!response.ok) {
        const error = await response.json().catch(() => null);
        throw new Error(error?.error?.message || "工程归档导出失败");
      }
      if (handle && response.body) {
        await response.body.pipeTo(await handle.createWritable());
      } else {
        const size = Number(response.headers.get("content-length"));
        if (!size || size > 512 * 1024 * 1024) {
          await response.body?.cancel();
          throw new Error(
            "大工程请使用支持直接保存文件的 Chrome 或 Edge 浏览器导出",
          );
        }
        const url = URL.createObjectURL(await response.blob());
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `work-${workId}.inspiration.zip`;
        anchor.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    },
    onSuccess: () =>
      setMessage(
        "完整工程归档已保存，包含固定版本和所需媒体，可导入其他项目的空作品。",
      ),
  });
  const download = useMutation({
    mutationFn: () => api.get<Record<string, unknown>>(`${path}/package`),
    onSuccess: (data) => {
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `work-${workId}.inspiration.json`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage(
        "已导出已采用版本的工程结构。媒体文件不包含在此包中，请保留原素材。",
      );
    },
  });
  const upload = useMutation({
    mutationFn: async (file: File) => {
      if (file.name.toLowerCase().endsWith(".zip")) {
        const selected = resumeId.current;
        resumeId.current = null;
        type Receipt = {
          id: string;
          uploaded_bytes: number;
          expected_bytes: number;
          filename: string;
        };
        let task: Receipt;
        if (selected) {
          const list = await api.get<Receipt[]>(`${path}/archive-jobs`);
          const found = list.find((job) => job.id === selected);
          if (
            !found ||
            found.expected_bytes !== file.size ||
            found.filename !== file.name
          )
            throw new Error(
              "请重新选择这次上传的原工程包，文件名称和大小必须一致",
            );
          task = found;
        } else {
          task = await api.post<Receipt>(
            `${path}/archive-uploads?request_key=${crypto.randomUUID()}&size=${file.size}&filename=${encodeURIComponent(file.name)}`,
            {},
          );
        }
        void cache.invalidateQueries({ queryKey: [path, "archive-jobs"] });
        const send = async (offset: number, end: number) => {
          const response = await fetch(
            `/api/v1${path}/archive-uploads/${task.id}?offset=${offset}`,
            {
              method: "PUT",
              headers: {
                Authorization: `Bearer ${getToken() || ""}`,
                "Content-Type": "application/octet-stream",
              },
              body: file.slice(offset, end),
            },
          );
          if (!response.ok) {
            const detail = await response.json().catch(() => null);
            throw new Error(
              detail?.error?.message || "上传中断，可重新选择原文件继续上传",
            );
          }
          return response.json() as Promise<Receipt>;
        };
        const chunk = 8 * 1024 * 1024;
        if (task.uploaded_bytes)
          await send(0, Math.min(chunk, task.uploaded_bytes));
        while (task.uploaded_bytes < file.size) {
          task = await send(
            task.uploaded_bytes,
            Math.min(file.size, task.uploaded_bytes + chunk),
          );
          setMessage(
            `上传工程包 ${Math.floor((task.uploaded_bytes / file.size) * 100)}%`,
          );
        }
        return api.post(`${path}/archive-uploads/${task.id}/complete`, {});
      }
      if (file.size > 16 * 1024 * 1024)
        throw new Error("工程描述文件不能超过 16 MB");
      let content: unknown;
      try {
        content = JSON.parse(await file.text());
      } catch {
        throw new Error(
          "无法读取工程文件，请选择导出的 .inspiration.json 文件",
        );
      }
      const head = await api.get<{ revision: number }>(path);
      return api.post(`${path}/changes`, {
        request_key: crypto.randomUUID(),
        base_revision: head.revision,
        commands: [{ operation: "package.import", package: content }],
      });
    },
    onSuccess: () => {
      void cache.invalidateQueries();
      setMessage(
        "恢复请求已提交，工程包将在后台校验。完成后请到「候选预览」检查内容并采用。",
      );
    },
  });
  return (
    <div className="space-y-3 rounded-xl border p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium">保存与恢复工程</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            完整归档包含编辑结构与原始媒体，可恢复到其他项目的空作品。轻量引用包不含媒体，需要保留原项目素材。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={
              archive.isPending || upload.isPending || download.isPending
            }
            onClick={() => archive.mutate()}
          >
            {archive.isPending ? "正在打包与保存…" : "导出完整归档"}
          </Button>
          <Button
            variant="outline"
            disabled={
              download.isPending || upload.isPending || archive.isPending
            }
            onClick={() => download.mutate()}
          >
            {download.isPending ? "正在导出…" : "导出工程引用包"}
          </Button>
          <Button
            variant="outline"
            disabled={
              upload.isPending || download.isPending || archive.isPending
            }
            onClick={() => {
              resumeId.current = null;
              picker.current?.click();
            }}
          >
            {upload.isPending ? "正在校验与恢复…" : "从工程包恢复"}
          </Button>
        </div>
      </div>
      <input
        ref={picker}
        type="file"
        accept=".json,.zip"
        className="hidden"
        aria-label="选择工程包"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) upload.mutate(file);
        }}
      />
      {!!jobs.data?.length && (
        <div className="space-y-2 border-t pt-3" aria-label="工程归档任务">
          {jobs.data.map((job) => (
            <div
              key={job.id}
              className="flex flex-wrap items-center gap-3 text-sm"
            >
              <span>
                {job.operation === "import"
                  ? `导入 ${job.filename || "工程包"}`
                  : `版本 ${job.revision}`}{" "}
                ·{" "}
                {(
                  {
                    pending: "等待处理",
                    uploading: upload.isPending ? "正在上传" : "等待继续上传",
                    running: "正在准备",
                    succeeded:
                      job.operation === "import" ? "已恢复为候选" : "可下载",
                    failed: "失败",
                    canceled: "已取消",
                    expired: "暂存已过期",
                    purging: "正在释放暂存空间",
                  } as Record<string, string>
                )[job.status] || job.status}
              </span>
              {job.status === "running" && job.progress.total_bytes ? (
                <span className="text-muted-foreground">
                  {job.progress.phase === "packing" ? "打包" : "准备素材"}{" "}
                  {Math.min(
                    100,
                    Math.floor(
                      ((job.progress.completed_bytes || 0) /
                        job.progress.total_bytes) *
                        100,
                    ),
                  )}
                  %
                </span>
              ) : null}
              {job.error && <span className="text-danger">{job.error}</span>}
              <span className="text-xs text-muted-foreground">暂存保留 {job.retention_days} 天</span>
              {job.operation === "export" &&
                job.status === "succeeded" &&
                !job.available && (
                  <span className="text-muted-foreground">
                    归档文件不可用，请重新准备
                  </span>
                )}
              {job.status === "succeeded" && job.available && (
                <Button
                  variant="outline"
                  disabled={saveArchive.isPending}
                  onClick={() => saveArchive.mutate(job.id)}
                >
                  下载归档
                </Button>
              )}
              {(["failed", "canceled", "expired"].includes(job.status) ||
                (job.operation === "export" &&
                  job.status === "succeeded" &&
                  !job.available)) && (
                <Button
                  variant="outline"
                  disabled={manageArchive.isPending}
                  onClick={() =>
                    manageArchive.mutate({ id: job.id, action: "retry" })
                  }
                >
                  重试
                </Button>
              )}
              {job.status === "uploading" && (
                <Button
                  variant="outline"
                  disabled={upload.isPending}
                  onClick={() => {
                    resumeId.current = job.id;
                    picker.current?.click();
                  }}
                >
                  选择原文件续传
                </Button>
              )}
              {["uploading", "pending", "running"].includes(job.status) && (
                <Button
                  variant="ghost"
                  disabled={manageArchive.isPending}
                  onClick={() =>
                    manageArchive.mutate({ id: job.id, action: "cancel" })
                  }
                >
                  取消
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
      {(download.error ||
        upload.error ||
        archive.error ||
        saveArchive.error ||
        manageArchive.error ||
        jobs.error) && (
        <p role="alert" className="text-sm text-danger">
          {
            (
              download.error ||
              upload.error ||
              archive.error ||
              saveArchive.error ||
              manageArchive.error ||
              jobs.error
            )?.message
          }
        </p>
      )}
      {message && (
        <p role="status" className="text-sm text-muted-foreground">
          {message}
        </p>
      )}
    </div>
  );
}

type OutputPreview = {
  job_id?: string;
  output_id?: string | null;
  status: string;
  error: string | null;
  blob_hash: string | null;
  output_type: string | null;
};

type TemporalImpact = {
  output: string;
  truncated: boolean;
  before: { status: string; intervals: TimeSpan[] };
  after: { status: string; intervals: TimeSpan[] };
};
type TimeSpan = {
  start: { numerator: number; denominator: number };
  end: { numerator: number; denominator: number };
};
function timeLabel(time: TimeSpan["start"]) {
  return (time.numerator / time.denominator).toLocaleString("zh-CN", {
    maximumFractionDigits: 6,
  });
}

export function DocumentChangeReview({
  projectId,
  workId,
  changeId,
}: {
  projectId: string;
  workId: string;
  changeId: string;
}) {
  const path = `/projects/${projectId}/works/${workId}/engineering`;
  const cache = useQueryClient();
  const [report, setReport] = useState<{
    fingerprint: string;
    status: string;
    affected: number;
    outputs: { value: string; label: string }[];
    usages: {
      label: string;
      slot: string;
      before: number | null;
      after: number | null;
    }[];
    ambiguous: number;
    primaryTimeline?: string;
    dependencyPaths: string[];
    pathsTruncated: boolean;
    temporal: (TemporalImpact & { title: string })[];
    syncReviews: {
      key: string;
      name: string;
      document_title: string;
      reasons: string[];
    }[];
  } | null>(null);
  const [output, setOutput] = useState("");
  const [watch, setWatch] = useState(false);
  const [acknowledged, setAcknowledged] = useState<string[]>([]);
  const result = useQuery({
    queryKey: ["native-preview", workId, changeId],
    queryFn: () =>
      api.get<{
        preview_job_id: string | null;
        preview: OutputPreview | null;
        previews?: OutputPreview[];
      }>(`${path}/changes/${changeId}`),
    enabled: watch,
    refetchInterval: (q) =>
      (q.state.data?.previews || [q.state.data?.preview]).some((item) =>
        ["pending", "running", "submitted"].includes(item?.status || ""),
      )
        ? 1500
        : false,
  });
  const inspect = useMutation({
    mutationFn: async () => {
      const [change, impact, docs] = await Promise.all([
        api.get<{
          fingerprint: string;
          status: string;
          snapshot?: {
            native_roots?: Record<string, { id: string; revision?: number }>;
          };
          timeline_update?: {
            timeline_id?: string;
            additional_updates?: { timeline_id: string }[];
          };
        }>(`${path}/changes/${changeId}`),
        api.get<{
          affected_outputs: unknown[];
          instance_input_changes?: {
            document_id: string;
            instance_id: string;
            instance_name?: string;
            slot: string;
            before: { revision: number } | null;
            after: { revision: number } | null;
          }[];
          ambiguous_revision_documents?: string[];
          dependency_paths?: { path: { name: string; slot: string }[] }[];
          dependency_paths_truncated?: boolean;
          temporal_impacts?: TemporalImpact[];
          sync_reviews?: {
            key: string;
            name: string;
            document_title: string;
            reasons: string[];
          }[];
        }>(`${path}/changes/${changeId}/impact`),
        api.get<{
          documents: {
            id: string;
            kind: string;
            title: string;
            source_timeline_id?: string;
            revisions?: { revision: number; schema_version?: number }[];
          }[];
          preview_executors?: { kind: string; schema_version?: number }[];
        }>(`${path}/documents`),
      ]);
      const outputs = Object.entries(
        change.snapshot?.native_roots || {},
      ).flatMap(([id, ref]) => {
        const doc = docs.documents.find(
          (d) =>
            d.id === ref.id &&
            docs.preview_executors?.some(
              (e) =>
                e.kind === d.kind &&
                (e.schema_version || 1) ===
                  (d.revisions?.find((r) => r.revision === ref.revision)
                    ?.schema_version || 1),
            ),
        );
        return doc ? [{ value: id, label: doc.title }] : [];
      });
      const usages = (impact.instance_input_changes || []).map((item) => ({
        label: `${docs.documents.find((d) => d.id === item.document_id)?.title || "工程文档"} / ${item.instance_name || "使用位置"}`,
        slot: item.slot === "mask" ? "蒙版" : "素材引用",
        before: item.before?.revision ?? null,
        after: item.after?.revision ?? null,
      }));
      if (change.timeline_update?.timeline_id) {
        const timelines = [
          change.timeline_update,
          ...(change.timeline_update.additional_updates || []),
        ];
        outputs.unshift(
          ...timelines.map((item, index) => ({
            value: `timeline:${item.timeline_id}`,
            label:
              docs.documents.find(
                (d) => d.source_timeline_id === item.timeline_id,
              )?.title || `剪辑 ${index + 1}`,
          })),
        );
      }
      return {
        ...change,
        affected: impact.affected_outputs.length,
        outputs,
        usages,
        ambiguous: impact.ambiguous_revision_documents?.length || 0,
        primaryTimeline: change.timeline_update?.timeline_id,
        dependencyPaths: (impact.dependency_paths || [])
          .filter((item) => item.path.length > 1)
          .map((item) =>
            item.path
              .map(
                (step) =>
                  `${step.name}${step.slot === "mask" ? "（蒙版）" : ""}`,
              )
              .join(" → "),
          ),
        pathsTruncated: impact.dependency_paths_truncated || false,
        syncReviews: impact.sync_reviews || [],
        temporal: (impact.temporal_impacts || []).map((item) => ({
          ...item,
          title:
            docs.documents.find((d) => d.id === item.output)?.title ||
            "工程输出",
        })),
      };
    },
    onSuccess: (data) => {
      setReport(data);
      setAcknowledged([]);
      setWatch(true);
    },
  });
  const selectedOutput = output || report?.outputs[0]?.value;
  const selectedPreview = result.data?.previews
    ? result.data.previews.find(
        (item) =>
          item.output_id === selectedOutput ||
          (!item.output_id &&
            selectedOutput === `timeline:${report?.primaryTimeline}`),
      )
    : result.data?.preview;
  const preview = useMutation({
    mutationFn: () =>
      api.post(`${path}/changes/${changeId}/preview`, {
        fingerprint: report!.fingerprint,
        output_id: output || report!.outputs[0]?.value,
        retry_job_id: ["failed", "canceled"].includes(
          selectedPreview?.status || "",
        )
          ? selectedPreview?.job_id || result.data?.preview_job_id
          : null,
      }),
    onSuccess: () => {
      setWatch(true);
      void result.refetch();
    },
  });
  const adopt = useMutation({
    mutationFn: () =>
      api.post(`${path}/changes/${changeId}/accept`, {
        fingerprint: report!.fingerprint,
        acknowledged_reviews: acknowledged,
      }),
    onSuccess: () => {
      setReport(null);
      void cache.invalidateQueries();
    },
  });
  return (
    <div className="space-y-3 rounded-xl border p-5">
      <h3 className="font-medium">工程内容修改</h3>
      <p className="text-sm text-muted-foreground">
        此候选保存编辑文档或导入内容，不代表已完成画面渲染。先检查影响，再决定是否采用。
      </p>
      <Button
        variant="outline"
        onClick={() => inspect.mutate()}
        disabled={inspect.isPending}
      >
        检查修改影响
      </Button>
      {report && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span>涉及 {report.affected} 个输出；已采用历史保持不变。</span>
          {report.status !== "accepted" && (
            <Button
              disabled={
                adopt.isPending ||
                report.syncReviews.some(
                  (item) => !acknowledged.includes(item.key),
                )
              }
              onClick={() => adopt.mutate()}
            >
              确认采用工程内容
            </Button>
          )}
          {report.status === "accepted" && <span>此候选已采用</span>}
        </div>
      )}
      {!!report?.temporal.length && (
        <details className="rounded-xl border p-4" aria-label="时间范围影响">
          <summary className="cursor-pointer font-medium">
            可能受影响的时间范围
          </summary>
          <p className="my-2 text-sm text-muted-foreground">
            范围为保守估计，请结合预览复核。
          </p>
          <div className="max-h-64 space-y-4 overflow-auto">
            {report.temporal.map((item) => (
              <div key={item.output} className="space-y-1 text-sm">
                <p className="font-medium">{item.title}</p>
                {(["before", "after"] as const).map((side) => (
                  <p key={side}>
                    {side === "before" ? "原版" : "候选版"}：
                    {item[side].status === "unknown_or_untimed"
                      ? "此类型尚不能定位时间范围"
                      : item[side].intervals.length
                        ? item[side].intervals
                            .map(
                              (span) =>
                                `${timeLabel(span.start)}–${timeLabel(span.end)} 秒`,
                            )
                            .join("；")
                        : "未发现时间内容变化"}
                  </p>
                ))}
                {item.truncated && (
                  <p className="text-muted-foreground">
                    嵌套范围较大，已按整段扩大估计。
                  </p>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
      {!!report?.syncReviews.length && (
        <section
          className="space-y-3 rounded-xl border p-4"
          aria-label="同步变化复核"
        >
          <p className="font-medium">这些同步关系需要复核</p>
          {report.syncReviews.map((item) => (
            <label key={item.key} className="flex items-start gap-3 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={acknowledged.includes(item.key)}
                onChange={(event) =>
                  setAcknowledged((values) =>
                    event.target.checked
                      ? [...values, item.key]
                      : values.filter((key) => key !== item.key),
                  )
                }
              />
              <span>
                {item.document_title} · {item.name}
                <span className="mt-1 block text-muted-foreground">
                  {item.reasons.join("；")}。确认保留此变化并采用。
                </span>
              </span>
            </label>
          ))}
        </section>
      )}
      {!!report?.usages.length && (
        <ul
          className="space-y-1 text-sm text-muted-foreground"
          aria-label="使用位置变化"
        >
          {report.usages.map((usage, index) => (
            <li key={index}>
              {usage.label} · {usage.slot}：
              {usage.before === null ? "未引用" : `修订 ${usage.before}`} →{" "}
              {usage.after === null ? "已移除" : `修订 ${usage.after}`}
            </li>
          ))}
        </ul>
      )}
      {!!report?.dependencyPaths.length && (
        <div
          className="space-y-1 text-sm text-muted-foreground"
          aria-label="嵌套修改位置"
        >
          <p>嵌套使用位置</p>
          {report.dependencyPaths.map((path, index) => (
            <p key={index}>{path}</p>
          ))}
        </div>
      )}
      {report?.pathsTruncated && (
        <p className="text-sm text-muted-foreground">
          依赖路径较多，此处仅展示部分，请分批检查修改范围。
        </p>
      )}
      {!!report?.ambiguous && (
        <p className="text-sm text-muted-foreground">
          {report.ambiguous}{" "}
          份文档同时使用多个修订，请结合固定输出检查；暂不推断实例之间的替换关系。
        </p>
      )}
      {report?.status === "proposed" && !!report.outputs.length && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-3">
            <SelectField
              label="预览输出"
              value={output || report.outputs[0].value}
              options={report.outputs}
              onChange={(value) => {
                setOutput(value);
                setWatch(true);
              }}
            />
            <Button
              variant="outline"
              disabled={
                preview.isPending ||
                ["pending", "running", "submitted"].includes(
                  selectedPreview?.status || "",
                )
              }
              onClick={() => preview.mutate()}
            >
              生成工程预览
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            按输出类型生成预览；未支持的效果或超出执行范围的内容会在预检时提示。
          </p>
        </div>
      )}
      {watch && selectedPreview && (
        <div role="status" className="space-y-2 text-sm">
          {selectedPreview.status === "succeeded" &&
          selectedPreview.blob_hash &&
          selectedPreview.output_type === "image" ? (
            <img
              className="max-h-96 max-w-full rounded-xl border object-contain"
              src={blobUrl(projectId, selectedPreview.blob_hash)}
              alt="固定工程修订的图像预览"
            />
          ) : selectedPreview.status === "succeeded" &&
            selectedPreview.blob_hash &&
            selectedPreview.output_type === "audio" ? (
            <MediaAudio
              src={blobUrl(projectId, selectedPreview.blob_hash)}
              label="固定工程修订的音频预览"
            />
          ) : selectedPreview.status === "succeeded" &&
            selectedPreview.blob_hash &&
            selectedPreview.output_type === "video" ? (
            <MediaVideo
              src={blobUrl(projectId, selectedPreview.blob_hash)}
              label="固定工程修订的合成预览"
            />
          ) : (
            <p>
              {selectedPreview.error ||
                (["pending", "running", "submitted"].includes(
                  selectedPreview.status,
                )
                  ? "正在制作工程预览…"
                  : "预览尚未完成，可重试")}
            </p>
          )}
        </div>
      )}
      {(inspect.error || adopt.error || preview.error || result.error) && (
        <p role="alert" className="text-sm text-danger">
          {
            (inspect.error || adopt.error || preview.error || result.error)
              ?.message
          }
        </p>
      )}
    </div>
  );
}
