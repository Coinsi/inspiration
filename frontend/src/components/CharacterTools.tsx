import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, blobUrl, type Generation, type GenJob } from "@/lib/api";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";

type Mode = "view" | "expression" | "sheet";
type Preset = { id: string; label: string; label_en: string };
type Preview = {
  prompt: string;
  points: number;
  source_generation_id: string;
  count: number;
};

export function CharacterTools({
  projectId,
  generation,
  provider,
  onClose,
  onJob,
}: {
  projectId: string;
  generation: Generation;
  provider: string;
  onClose: () => void;
  onJob: (id: string) => void;
}) {
  const zh = useI18n().lang === "zh";
  const base = `/projects/${projectId}`;
  const [mode, setMode] = useState<Mode>("view");
  const [preset, setPreset] = useState("three_quarter");
  const [notes, setNotes] = useState("");
  const [result, setResult] = useState<Preview | null>(null);
  const [imageError, setImageError] = useState(false);
  const catalog = useQuery({
    queryKey: ["character-presets", projectId],
    queryFn: () =>
      api.get<{ groups: Record<Mode, Preset[]> }>(`${base}/character-presets`),
  });
  const payload = { provider, mode, preset, notes };
  const preview = useMutation({
    mutationFn: () =>
      api.post<Preview>(
        `${base}/generations/${generation.id}/character-preview`,
        payload,
      ),
    onSuccess: setResult,
  });
  const generate = useMutation({
    mutationFn: () =>
      api.post<GenJob>(
        `${base}/generations/${generation.id}/character`,
        payload,
      ),
    onSuccess: (j) => {
      onJob(j.id);
      onClose();
    },
  });
  const busy = preview.isPending || generate.isPending;
  const reset = () => {
    setResult(null);
    preview.reset();
    generate.reset();
  };
  const labels = zh
    ? { view: "角色视角", expression: "角色表情", sheet: "三视图" }
    : { view: "View", expression: "Expression", sheet: "Three-view sheet" };
  return (
    <Modal
      open
      onClose={onClose}
      title={zh ? "角色工具" : "Character tools"}
      width={940}
    >
      <p className="mb-4 text-sm text-muted-foreground">
        {zh
          ? "先选择单个角色的清晰参考图。预览只整理要求和估算点数，点击生成后才会调用图片模型。"
          : "Use a clear reference of one character. Preview only prepares instructions and an estimate; Generate calls the image model."}
      </p>
      <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="min-w-0 space-y-3">
          <img
            src={blobUrl(projectId, generation.output_blob_hash!)}
            alt={zh ? "角色参考图" : "Character reference"}
            onError={() => setImageError(true)}
            className="max-h-96 w-full rounded-lg bg-elevated object-contain"
          />
          <p className="text-xs text-muted-foreground">
            {zh ? "来源素材" : "Source"} {generation.id.slice(0, 8)} ·{" "}
            {provider}
          </p>
          {imageError && (
            <p role="alert" className="text-sm text-danger">
              {zh
                ? "参考图无法读取，请检查素材后重试。"
                : "Cannot load reference. Check the media and try again."}
            </p>
          )}
          <p className="text-xs leading-relaxed text-muted-foreground">
            {zh
              ? "结果保存为新版本。模型可能改变五官、服装细节或画风，请逐张检查后再钦定。三视图是一张正面、侧面、背面参考板；未展示的身体与背面细节由模型推演。"
              : "Results become new variants. Check identity, costume and style before selecting. The three-view sheet is one image; unseen body and back details are inferred."}
          </p>
        </div>
        <div className="min-w-0 space-y-4">
          <div className="flex flex-wrap gap-2">
            {(["view", "expression", "sheet"] as const).map((m) => (
              <Button
                key={m}
                size="sm"
                variant={mode === m ? "default" : "outline"}
                disabled={busy}
                onClick={() => {
                  setMode(m);
                  setPreset(
                    {
                      view: "three_quarter",
                      expression: "happy",
                      sheet: "three_view",
                    }[m],
                  );
                  reset();
                }}
              >
                {labels[m]}
              </Button>
            ))}
          </div>
          <label className="block text-sm">
            {zh ? "角色预设" : "Character preset"}
            <select
              aria-label={zh ? "角色预设" : "Character preset"}
              className="mt-1 h-9 w-full rounded-md border bg-bg px-2"
              value={preset}
              disabled={busy || !catalog.data}
              onChange={(e) => {
                setPreset(e.target.value);
                reset();
              }}
            >
              {catalog.data?.groups[mode].map((p) => (
                <option key={p.id} value={p.id}>
                  {zh ? p.label : p.label_en}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            {zh ? "补充要求（可选）" : "Additional instructions (optional)"}
            <textarea
              rows={3}
              aria-label={
                zh ? "补充要求（可选）" : "Additional instructions (optional)"
              }
              maxLength={2000}
              className="mt-1 w-full rounded-md border bg-bg p-2"
              value={notes}
              disabled={busy}
              onChange={(e) => {
                setNotes(e.target.value);
                reset();
              }}
              placeholder={
                zh
                  ? "例如：保持发饰和领口纹样"
                  : "For example: preserve the hair ornament and collar pattern"
              }
            />
          </label>
          {catalog.error && (
            <p role="alert" className="text-sm text-danger">
              {catalog.error.message}
            </p>
          )}
          <Button
            variant="outline"
            disabled={busy || !catalog.data || imageError}
            onClick={() => {
              setResult(null);
              preview.mutate();
            }}
          >
            {preview.isPending
              ? zh
                ? "正在预览…"
                : "Preparing…"
              : zh
                ? "预览要求与点数"
                : "Preview instructions and cost"}
          </Button>
          {result && (
            <div className="space-y-3 rounded-lg border p-3">
              <p className="text-sm font-medium">
                {zh
                  ? `预计 ${result.points} 点 · 生成 1 张`
                  : `Estimated ${result.points} points · 1 image`}
              </p>
              <label className="block text-xs text-muted-foreground">
                {zh ? "实际生成要求" : "Generation instructions"}
                <textarea
                  readOnly
                  aria-label={zh ? "实际生成要求" : "Generation instructions"}
                  rows={7}
                  value={result.prompt}
                  className="mt-1 w-full rounded-md border bg-bg p-2 text-sm text-foreground"
                />
              </label>
              <Button
                disabled={busy || imageError}
                onClick={() => generate.mutate()}
              >
                {generate.isPending
                  ? zh
                    ? "正在提交…"
                    : "Submitting…"
                  : zh
                    ? "生成角色素材"
                    : "Generate character image"}
              </Button>
            </div>
          )}
          {(preview.error || generate.error) && (
            <p role="alert" className="text-sm text-danger">
              {(preview.error || generate.error)?.message}
            </p>
          )}
        </div>
      </div>
    </Modal>
  );
}
