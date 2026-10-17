import { useState } from "react";
import { Check, Plus } from "lucide-react";
import { Modal } from "./ui/modal";
import { SearchField } from "./ui/search-field";
import { Button } from "./ui/button";

// Suggestions make adding familiar IDs easier; they are not capability verification.
export function modelSuggestion(id: string) {
  const code = id.toLowerCase();
  if (/gpt-image|dall-e/.test(code))
    return { modality: "image", protocol: "gpt_image" };
  if (/seedream/.test(code))
    return { modality: "image", protocol: "ark_image" };
  if (/seedance/.test(code))
    return { modality: "video", protocol: "ark_video" };
  if (/^sora/.test(code))
    return { modality: "video", protocol: "openai_video" };
  if (/hailuo/.test(code))
    return { modality: "video", protocol: "minimax_video" };
  if (
    !/audio|image|embed|tts|realtime|whisper/.test(code) &&
    /^(gpt-|claude-|deepseek-|qwen|o[134](?:-|$))/.test(code)
  )
    return { modality: "text", protocol: "cloud_llm" };
  return { modality: "", protocol: "" };
}

export function ModelDirectory({
  open,
  name,
  models,
  added,
  truncated,
  close,
  select,
  manual,
}: {
  open: boolean;
  name: string;
  models: string[];
  added: string[];
  truncated: boolean;
  close: () => void;
  select: (code: string) => void;
  manual: () => void;
}) {
  const [query, setQuery] = useState("");
  const matches = models.filter((id) =>
    id.toLowerCase().includes(query.trim().toLowerCase()),
  );
  return (
    <Modal open={open} title={`选择模型 · ${name}`} width={680} onClose={close}>
      <div className="service-model-picker">
        <p>选择需要的模型，确认用途后即可添加。读取目录不会调用生成服务。</p>
        <SearchField
          label="搜索服务模型"
          value={query}
          onValueChange={setQuery}
          placeholder="搜索模型名称或编号"
        />
        <div className="service-model-picker-count" role="status">
          {matches.length} 个模型 · 此服务已添加{" "}
          {models.filter((id) => added.includes(id)).length} 个
          {truncated ? " · 目录仅显示前1000项" : ""}
        </div>
        <div className="service-model-picker-list">
          {matches.map((id) => {
            const installed = added.includes(id);
            const type = modelSuggestion(id).modality;
            return (
              <button
                type="button"
                key={id}
                disabled={installed}
                onClick={() => select(id)}
              >
                <span>
                  <strong>{id}</strong>
                  <small>
                    {installed
                      ? "已添加到此服务"
                      : type
                        ? `建议用于${{ image: "图片", video: "视频", text: "文字" }[type]}创作 · 添加时可调整`
                        : "添加时选择模型用途"}
                  </small>
                </span>
                {installed ? <Check size={16} /> : <Plus size={16} />}
              </button>
            );
          })}
          {!matches.length && (
            <p className="service-model-picker-empty">
              {models.length
                ? "没有匹配的模型，试试其他名称。"
                : "服务没有返回模型，可手动填写已开通的模型编号。"}
            </p>
          )}
        </div>
        <footer>
          <span>目录中的模型不一定都已开通，实际可用性以服务返回为准。</span>
          <Button variant="outline" onClick={manual}>
            手动添加
          </Button>
        </footer>
      </div>
    </Modal>
  );
}
