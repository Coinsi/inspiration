import { useState } from "react";
import { SelectField } from "./ui/select-field";
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
  addMany,
  busy,
  error,
}: {
  open: boolean;
  name: string;
  models: string[];
  added: string[];
  truncated: boolean;
  close: () => void;
  select: (code: string) => void;
  manual: () => void;
  addMany: (items: {code: string; modality: string}[]) => void;
  busy: boolean;
  error?: string;
}) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Record<string, string>>({});
  const matches = models.filter((id) =>
    id.toLowerCase().includes(query.trim().toLowerCase()),
  );
  return (
    <Modal open={open} title={`选择模型 · ${name}`} width={680} onClose={() => { if (!busy) close(); }}>
      <div className="service-model-picker">
        <p>勾选需要的模型后一起添加。用途只是建议，可调整；实际能力尚未验证。</p>
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
              <div key={id} className="model-directory-row">
                <label className="flex items-center gap-3 min-w-0 flex-1">
                  <input type="checkbox" aria-label={`选择 ${id}`} disabled={installed || busy} checked={installed || Object.prototype.hasOwnProperty.call(selected, id)} onChange={e => setSelected(current => { if (e.target.checked) return {...current, [id]: type}; const next = {...current}; delete next[id]; return next; })} />
                  <span className="min-w-0"><strong className="break-all">{id}</strong><small>{installed ? "已添加到此服务" : "待验证 · 沿用服务连接"}</small></span>
                </label>
                {!installed && Object.prototype.hasOwnProperty.call(selected, id) && <SelectField disabled={busy} label={`${id} 用途`} value={selected[id]} options={[{value: "", label: "选择用途", disabled: true}, {value: "text", label: "文字对话"}, {value: "image", label: "图片生成"}, {value: "video", label: "视频生成"}]} onChange={value => setSelected(current => ({...current, [id]: value}))} />}
                {!installed && <button type="button" disabled={busy} onClick={() => select(id)}>单独配置</button>}
              </div>
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
        {error && <p role="alert" className="text-danger">{error}</p>}
        <footer>
          <span>目录中的模型不一定都已开通，实际可用性以服务返回为准。</span>
          <Button disabled={busy} variant="outline" onClick={manual}>
            手动添加
          </Button>
          <Button disabled={busy || !Object.keys(selected).length || Object.values(selected).some(value => !value) || Object.keys(selected).length > 100} onClick={() => addMany(Object.entries(selected).map(([code, modality]) => ({code, modality})))}>{busy ? "添加中…" : `添加已选模型（${Object.keys(selected).length}）`}</Button>
        </footer>
      </div>
    </Modal>
  );
}
