import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import {
  ImageReferencePicker,
  type PromptReference,
} from "@/components/ImageReferencePicker";

type Result = {
  prompt: string;
  avoid: string;
  explanation: string;
  assumptions: string[];
  optimizer_model: string;
  target_provider: string | null;
  reference_sources: PromptReference[];
};
export function PromptOptimizer({
  projectId,
  initial,
  mediaType,
  targetType,
  targetId,
  provider,
  onClose,
  onApply,
}: {
  projectId: string;
  initial: string;
  mediaType: string;
  targetType: "shot" | "asset";
  targetId: string;
  provider: string;
  onClose: () => void;
  onApply: (value: string) => void;
}) {
  const zh = useI18n().lang === "zh";
  const [original, setOriginal] = useState(initial),
    [mode, setMode] = useState("refine"),
    [style, setStyle] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [references, setReferences] = useState<PromptReference[]>([]);
  const optimize = useMutation({
    mutationFn: () =>
      api.post<Result>(`/projects/${projectId}/prompts/optimize`, {
        prompt: original,
        mode,
        media_type: mediaType,
        style,
        references:
          mode === "reference"
            ? references.map(({ kind, id }) => ({ kind, id }))
            : [],
        target_provider: mode === "model-adapt" ? provider : undefined,
      }),
    onSuccess: setResult,
  });
  return (
    <Modal
      open
      onClose={onClose}
      title={zh ? "提示词优化" : "Optimize prompt"}
      width={800}
    >
      <p className="mb-3 text-xs text-muted-foreground">
        {zh
          ? "使用项目已配置的文本模型。先预览，再采用；不会自动生成图片或视频。"
          : "Uses the project text model. Review before applying; no media is generated automatically."}
      </p>
      <label className="block text-xs">
        {zh ? "原始想法" : "Original idea"}
        <textarea
          aria-label={zh ? "原始想法" : "Original idea"}
          className="mt-1 w-full rounded-md border bg-bg p-2 text-sm"
          rows={4}
          maxLength={8000}
          value={original}
          disabled={optimize.isPending}
          onChange={(e) => {
            setOriginal(e.target.value);
            setResult(null);
          }}
        />
      </label>
      <div className="my-3 flex flex-wrap gap-2">
        {[
          ["refine", zh ? "精修表达" : "Refine"],
          ["expand", zh ? "扩展想法" : "Expand"],
          ["style", zh ? "强化风格" : "Style"],
          ["reference", zh ? "结合参考" : "Use references"],
          ["model-adapt", zh ? "适配生成能力" : "Adapt to provider"],
        ].map(([value, label]) => (
          <Button
            key={value}
            size="sm"
            variant={mode === value ? "default" : "ghost"}
            disabled={optimize.isPending}
            aria-pressed={mode === value}
            onClick={() => {
              setMode(value);
              setResult(null);
            }}
          >
            {label}
          </Button>
        ))}
      </div>
      {mode === "reference" && (
        <div className="mb-3">
          <ImageReferencePicker
            projectId={projectId}
            targetType={targetType}
            targetId={targetId}
            value={references}
            disabled={optimize.isPending}
            onChange={(refs) => {
              setReferences(refs);
              setResult(null);
            }}
          />
          <p className="mt-2 text-xs text-muted-foreground">
            {zh
              ? "需要项目文本模型支持看图。仅用于理解提示词，不会自动添加为生成参考图。"
              : "The project text model must support vision. These images inform the prompt; they are not automatically added as generation references."}
          </p>
        </div>
      )}
      {mode === "model-adapt" && (
        <p className="mb-3 rounded-lg border bg-surface p-3 text-xs text-muted-foreground">
          {zh
            ? `目标：${provider} · ${mediaType === "video" ? "视频" : "图片"}。依据已声明能力整理表达，不自动改变参数，也不保证提高生成质量。`
            : `Target: ${provider} · ${mediaType}. Uses declared capabilities; settings remain unchanged and quality improvements are not guaranteed.`}
        </p>
      )}
      {mode === "style" && (
        <input
          aria-label={zh ? "目标风格" : "Target style"}
          value={style}
          maxLength={1000}
          disabled={optimize.isPending}
          onChange={(e) => {
            setStyle(e.target.value);
            setResult(null);
          }}
          placeholder={
            zh
              ? "例如：水墨动画、低饱和电影摄影"
              : "Describe the desired visual style"
          }
          className="mb-3 h-9 w-full rounded-md border bg-bg px-2 text-sm"
        />
      )}
      <Button
        disabled={
          !original.trim() ||
          optimize.isPending ||
          (mode === "style" && !style.trim()) ||
          (mode === "reference" && !references.length)
        }
        onClick={() => {
          setResult(null);
          optimize.mutate();
        }}
      >
        {optimize.isPending
          ? zh
            ? "优化中…"
            : "Optimizing…"
          : zh
            ? "生成优化建议"
            : "Optimize"}
      </Button>
      {optimize.error && (
        <p role="alert" className="mt-3 text-xs text-danger">
          {optimize.error.message}
        </p>
      )}
      {result && (
        <div className="mt-4 space-y-3 border-t pt-4">
          <p className="text-xs text-muted-foreground">
            {zh ? "优化模型" : "Optimizer"} · {result.optimizer_model}
          </p>
          {!!result.reference_sources?.length && (
            <p className="text-xs text-muted-foreground">
              {zh ? "本次参考" : "References used"}：
              {result.reference_sources
                .map((r, i) => `${i + 1}. ${r.title}`)
                .join(" · ")}
            </p>
          )}
          <label className="block text-sm">
            {zh ? "优化结果（可编辑）" : "Result (editable)"}
            <textarea
              aria-label={zh ? "优化结果" : "Optimized prompt"}
              rows={5}
              maxLength={12000}
              value={result.prompt}
              onChange={(e) => setResult({ ...result, prompt: e.target.value })}
              className="mt-1 w-full rounded-md border bg-bg p-2"
            />
          </label>
          {result.avoid && (
            <p className="text-xs text-muted-foreground">
              {zh ? "建议规避：" : "Avoid: "}
              {result.avoid}
            </p>
          )}
          <p className="text-xs text-muted-foreground">{result.explanation}</p>
          {result.assumptions.length > 0 && (
            <div className="rounded-md border p-2 text-xs">
              <p>{zh ? "请核对新增假设：" : "Review added assumptions:"}</p>
              <ul className="list-disc pl-4">
                {result.assumptions.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="flex justify-end">
            <Button
              disabled={!result.prompt.trim()}
              onClick={() => {
                onApply(result.prompt);
                onClose();
              }}
            >
              {zh ? "采用到生成指令" : "Apply to prompt"}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
