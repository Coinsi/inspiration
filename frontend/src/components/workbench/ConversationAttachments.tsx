import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, FileAudio, Film, FolderOpen, ImagePlus, Loader2, Paperclip, RotateCcw, Upload, X } from "lucide-react";
import { api, blobUrl } from "@/lib/api";
import { mediaSize } from "@/lib/library";
import { Modal } from "@/components/ui/modal";
import { SearchField } from "@/components/ui/search-field";
import { SelectField } from "@/components/ui/select-field";
import { ComposerReferences, type ComposerReference } from "./ComposerReferences";
import { attachmentAccept, type AttachmentDraft, type ConversationAttachment } from "./attachmentUpload";
import type { useConversationAttachments } from "./useConversationAttachments";
import type { ReferenceRole } from "./referenceInput";
import { referencePickState, referenceSelectionHint, type ReferenceCapabilities, type ReferenceSelection } from "./referenceProblem";
import "./conversation-attachments.css";

type PreviewItem = { title: string; kind?: string; blob_hash?: string | null; thumbnail_hash?: string | null; preview_url?: string };
export function AttachmentPreview({ projectId, item }: { projectId: string; item: PreviewItem }) {
  const url = item.preview_url || (item.blob_hash ? blobUrl(projectId, item.blob_hash) : "");
  return <div className="attachment-preview-stage">{item.kind === "audio" ? <><FileAudio size={54} /><audio src={url} controls /></> : item.kind === "video" ? <video src={url} controls playsInline /> : <img src={url} alt={item.title} />}</div>;
}
function Thumbnail({ projectId, item }: { projectId: string; item: PreviewItem }) {
  const image = item.thumbnail_hash || (item.kind !== "video" && item.kind !== "audio" ? item.blob_hash : "");
  if (image) return <img src={blobUrl(projectId, image)} alt="" loading="lazy" />;
  if (item.kind === "audio") return <FileAudio size={28} />;
  const source = item.preview_url || (item.blob_hash ? blobUrl(projectId, item.blob_hash) : "");
  if (source) return item.kind === "video" ? <video src={source} muted preload="metadata" /> : <img src={source} alt="" />;
  return item.kind === "video" ? <Film size={28} /> : <ImagePlus size={28} />;
}
const stateLabel = (item: AttachmentDraft) => item.state === "ready" ? mediaSize(item.size_bytes) : item.state === "paused" ? "重新选择原文件续传" : item.state === "failed" ? "上传失败，可重试" : item.state === "processing" ? "正在准备附件…" : item.state === "preparing" ? "准备上传…" : `上传中 ${Math.round((item.attachment?.uploaded_bytes || 0) / Math.max(1, item.size_bytes) * 100)}%`;

export function ComposerAttachmentTray({ projectId, uploads, references, setReferences, disabled, frameRoles = [] }: {
  projectId: string; uploads: ReturnType<typeof useConversationAttachments>; references: ComposerReference[];
  setReferences: (fn: (items: ComposerReference[]) => ComposerReference[]) => void; disabled: boolean; frameRoles?: string[];
}) {
  const [preview, setPreview] = useState<PreviewItem | null>(null);
  const [resume, setResume] = useState<AttachmentDraft | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const rolePicker = (item: { kind?: string; role: ReferenceRole }, onChange: (role: ReferenceRole) => void) => item.kind !== "audio" && item.kind !== "video" && frameRoles.length > 0 && <SelectField label="参考用途" value={item.role} disabled={disabled} options={[{ value: "reference", label: "参考图" }, ...frameRoles.map(role => ({ value: role, label: role === "first_frame" ? "首帧" : "尾帧" }))]} onChange={value => onChange(value as ReferenceRole)} />;
  return <>
    {(uploads.items.length > 0 || references.length > 0) && <div className="conversation-attachment-tray" aria-label="本次发送的内容">
      {uploads.items.map(item => {
        const label = item.attachment?.saved_at ? item.attachment.title : item.filename;
        const visual = { ...item.attachment, title: label, kind: item.kind, preview_url: item.preview_url };
        return <div className={`conversation-attachment-card is-${item.state}`} key={item.client_id}>
          <button type="button" className="attachment-thumbnail" aria-label={`预览 ${label}`} disabled={!item.preview_url && !item.attachment?.blob_hash} onClick={() => setPreview(visual)}><Thumbnail projectId={projectId} item={visual} />{["uploading", "preparing", "processing"].includes(item.state) && <span className="attachment-busy"><Loader2 size={18} className="animate-spin" /></span>}</button>
          <div className="attachment-caption"><strong title={item.filename}>{label}</strong><span>{stateLabel(item)}</span></div>
          {item.state === "uploading" && <progress max={item.size_bytes} value={item.attachment?.uploaded_bytes || 0} aria-label={`${item.filename} 上传进度`} />}
          {item.error && <p className="attachment-error" role="alert">{item.error}</p>}
          {(item.state === "failed" || item.state === "paused") && <button type="button" className="attachment-retry" disabled={disabled} onClick={() => { if (!uploads.retry(item)) { setResume(item); input.current?.click(); } }}><RotateCcw size={13} />{item.state === "paused" ? "继续上传" : "重试"}</button>}
          {rolePicker(item, role => uploads.role(item.client_id, role))}
          <button type="button" className="attachment-remove" disabled={disabled} aria-label={`移除 ${label}`} onClick={() => uploads.remove(item)}><X size={13} /></button>
        </div>;
      })}
      {references.map(item => <div className="conversation-attachment-card is-ready" key={`${item.source_type}:${item.id}`}>
        <button type="button" className="attachment-thumbnail" aria-label={`预览 ${item.title}`} onClick={() => setPreview(item)}><Thumbnail projectId={projectId} item={item} /></button>
        <div className="attachment-caption"><strong title={item.title}>{item.title}</strong><span>来自素材库</span></div>
        {rolePicker(item, role => setReferences(list => list.map(value => value.id === item.id && value.source_type === item.source_type ? { ...value, role } : value)))}
        <button type="button" className="attachment-remove" disabled={disabled} aria-label={`移除 ${item.title}`} onClick={() => setReferences(list => list.filter(value => !(value.id === item.id && value.source_type === item.source_type)))}><X size={13} /></button>
      </div>)}
    </div>}
    <input ref={input} type="file" accept={attachmentAccept} hidden onChange={event => { const file = event.target.files?.[0]; if (resume && file) uploads.retry(resume, file); event.target.value = ""; setResume(null); }} />
    <Modal open={!!preview} title={preview?.title || "预览附件"} onClose={() => setPreview(null)} width={900}>{preview && <AttachmentPreview projectId={projectId} item={preview} />}</Modal>
  </>;
}

export type MessageAttachmentItem = ConversationAttachment | ComposerReference;

export function MessageAttachments({ projectId, attachments = [], references = [], onReuse, reuseDisabled = false }: {
  projectId: string; attachments?: ConversationAttachment[]; references?: ComposerReference[];
  onReuse?: (item: MessageAttachmentItem) => void; reuseDisabled?: boolean;
}) {
  const [preview, setPreview] = useState<PreviewItem | null>(null);
  const items: MessageAttachmentItem[] = [...attachments.map(item => ({ ...item, source_type: "attachment" as const, title: item.title || item.filename })), ...references];
  if (!items.length) return null;
  return <>
    <div className="message-attachments">{items.map((item, i) => <div className="message-attachment-card" key={`${item.id}:${i}`}>
      <button type="button" className="message-attachment-preview" onClick={() => setPreview(item)} aria-label={`预览 ${item.title}`}>
        <span className="message-attachment-visual"><Thumbnail projectId={projectId} item={item} />{item.kind === "video" && <Film size={19} className="message-attachment-kind" />}</span>
        <span className="message-attachment-title">{item.title}</span>
      </button>
      {onReuse && <button type="button" className="message-attachment-reuse" disabled={reuseDisabled} aria-label={`再次引用 ${item.title}`} onClick={() => onReuse(item)}><RotateCcw size={13} />再次引用</button>}
    </div>)}</div>
    <Modal open={!!preview} title={preview?.title || "附件"} onClose={() => setPreview(null)} width={900}>{preview && <AttachmentPreview projectId={projectId} item={preview} />}</Modal>
  </>;
}

export function ConversationAddMenu({ disabled, onFiles, onLibrary, onObjects }: { disabled: boolean; onFiles: (files: FileList) => void; onLibrary: () => void; onObjects: () => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [position, setPosition] = useState({ left: 12, bottom: 70, width: 290 });
  useLayoutEffect(() => { if (!open) return; const place = () => { const rect = root.current?.getBoundingClientRect(); if (!rect) return; const width = Math.min(290, window.innerWidth - 24); setPosition({ width, left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)), bottom: window.innerHeight - rect.top + 10 }); }; place(); window.addEventListener("resize", place); window.addEventListener("scroll", place, true); return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); }; }, [open]);
  useEffect(() => { if (!open) return; const close = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); }; const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); }; document.addEventListener("pointerdown", close); document.addEventListener("keydown", escape); return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", escape); }; }, [open]);
  return <div className="conversation-add-menu" ref={root}><button type="button" className="conversation-tool" disabled={disabled} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}><Paperclip size={17} /><span>添加内容</span></button>{open && <div className="conversation-add-options" role="menu" style={{ position: "fixed", ...position }}><button type="button" role="menuitem" onClick={() => { setOpen(false); input.current?.click(); }}><Upload size={16} /><span>从电脑上传<small>图片、视频、音频 · 也可拖入或粘贴</small></span></button><button type="button" role="menuitem" onClick={() => { setOpen(false); onLibrary(); }}><ImagePlus size={16} /><span>已有素材与对话附件</span></button><button type="button" role="menuitem" onClick={() => { setOpen(false); onObjects(); }}><FolderOpen size={16} /><span>项目中的创作对象</span></button></div>}<input ref={input} type="file" accept={attachmentAccept} multiple hidden onChange={event => { if (event.target.files) onFiles(event.target.files); event.target.value = ""; }} /></div>;
}

export function ConversationAttachmentPicker({ projectId, open, onClose, references, pickReference, uploads, mode, capabilities, capabilitiesLoading, capabilitiesError, onRetryCapabilities }: {
  mode: string; projectId: string; open: boolean; onClose: () => void; references: ComposerReference[];
  pickReference: (item: ComposerReference) => void; uploads: ReturnType<typeof useConversationAttachments>;
  capabilities?: ReferenceCapabilities; capabilitiesLoading?: boolean; capabilitiesError?: string | null; onRetryCapabilities?: () => void;
}) {
  const [tab, setTab] = useState("library");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const saved = useQuery({ queryKey: ["saved-conversation-attachments", projectId, query, offset], enabled: open && tab === "attachments", queryFn: () => api.get<{ items: ConversationAttachment[]; has_more: boolean }>(`/projects/${projectId}/attachments?query=${encodeURIComponent(query)}&offset=${offset}&limit=24`) });
  const selection: ReferenceSelection = { mode, capabilities, capabilitiesLoading, capabilitiesError, references: [...references, ...uploads.items].map(item => ({ ...item, role: mode === "image" ? "reference" : item.role })) };
  return <Modal open={open} onClose={onClose} title="添加已有内容" width={800}>
    <div className="attachment-picker-tabs"><button type="button" aria-pressed={tab === "library"} onClick={() => setTab("library")}>素材库</button><button type="button" aria-pressed={tab === "attachments"} onClick={() => setTab("attachments")}>对话附件</button></div>
    <p className="attachment-picker-capabilities" role={mode !== "text" && capabilitiesError ? "alert" : "status"}>
      {referenceSelectionHint(selection)}
      {mode !== "text" && capabilitiesError && onRetryCapabilities && <button type="button" onClick={onRetryCapabilities}>重新读取</button>}
    </p>
    {tab === "library" ? <ComposerReferences projectId={projectId} includeVideos includeAudio maxSelected={8 - uploads.items.length} selected={references} pick={pickReference} selection={selection} /> : <>
      <p className="attachment-picker-hint">发送过的附件会自动保留在这里，可在后续对话中继续使用。</p>
      <SearchField label="搜索对话附件" placeholder="搜索附件名称" value={query} onValueChange={value => { setQuery(value); setOffset(0); }} />
      {saved.isPending ? <p role="status">正在读取附件…</p> : saved.error ? <p role="alert">{saved.error.message}<button type="button" onClick={() => void saved.refetch()}>重试</button></p> : <div className="composer-reference-grid">{saved.data.items.map(item => {
        const selected = uploads.items.find(value => value.attachment?.id === item.id);
        const selectedReference = references.find(value => value.source_type === "attachment" && value.id === item.id);
        const isSelected = !!selected || !!selectedReference;
        const state = referencePickState(selection, { ...item, role: item.role || "reference" });
        const problem = isSelected ? "" : state.problem;
        return <button type="button" key={item.id} aria-label={item.title || item.filename} aria-pressed={isSelected} disabled={!!problem} title={problem || (isSelected ? "已选择，点击移除" : undefined)} onClick={() => selected ? uploads.remove(selected) : selectedReference ? pickReference(selectedReference) : uploads.addSaved({ ...item, role: state.role }, references.length)}>
          <div className="saved-attachment-visual"><Thumbnail projectId={projectId} item={item} /></div>
          <span>{isSelected && <Check size={14} />}{item.title || item.filename}</span>
          {problem && <small className="reference-selection-reason">{problem}</small>}
          {isSelected && <small className="reference-selection-status">已选择 · 点击移除</small>}
          {!problem && !isSelected && state.role !== "reference" && <small className="reference-selection-status">将用作{state.role === "first_frame" ? "首帧" : "尾帧"}</small>}
        </button>;
      })}</div>}
      {saved.data?.items.length === 0 && <p>还没有匹配的对话附件。直接上传并发送后，就会出现在这里。</p>}
      <div className="attachment-picker-pages"><button type="button" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 24))}>上一页</button><button type="button" disabled={!saved.data?.has_more} onClick={() => setOffset(offset + 24)}>下一页</button></div>
    </>}
    <div className="attachment-picker-done"><span>已添加 {uploads.items.length + references.length} / 8 项</span><button type="button" onClick={onClose}>完成</button></div>
  </Modal>;
}
