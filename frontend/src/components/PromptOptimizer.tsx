import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";

type Result = {
  prompt: string;
  avoid: string;
  explanation: string;
  assumptions: string[];
};
export function PromptOptimizer({
  projectId,
  initial,
  mediaType,
  onClose,
  onApply,
}: {
  projectId: string;
  initial: string;
  mediaType: string;
  onClose: () => void;
  onApply: (value: string) => void;
}) {
  const zh = useI18n().lang === "zh";
  const [original, setOriginal] = useState(initial),
    [mode, setMode] = useState("refine"),
    [style, setStyle] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const optimize = useMutation({
    mutationFn: () =>
      api.post<Result>(`/projects/${projectId}/prompts/optimize`, {
        prompt: original,
        mode,
        media_type: mediaType,
        style,
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
        ].map(([value, label]) => (
          <Button
            key={value}
            size="sm"
            variant={mode === value ? "default" : "ghost"}
            disabled={optimize.isPending}
            onClick={() => {
              setMode(value);
              setResult(null);
            }}
          >
            {label}
          </Button>
        ))}
      </div>
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
          (mode === "style" && !style.trim())
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
