import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  persistedAttachmentDrafts, restoreAttachmentDrafts, uploadConversationAttachment,
  validateAttachmentFile, type AttachmentDraft, type ConversationAttachment,
} from "./attachmentUpload";
import type { ReferenceRole } from "./referenceInput";

export function useConversationAttachments(projectId: string, scopeKey: string) {
  const key = `${scopeKey}.attachments`;
  const read = (scope: string) => { try { return restoreAttachmentDrafts(localStorage.getItem(scope)); } catch { return []; } };
  const [stored, setStored] = useState(() => ({ key, items: read(key) }));
  const current = stored.key === key ? stored : { key, items: read(key) };
  if (stored.key !== key) setStored(current);
  const items = current.items;
  const latest = useRef(current);
  latest.current = current;
  const mounted = useRef(true);
  const files = useRef(new Map<string, File>());
  const urls = useRef(new Map<string, string>());
  const canceled = useRef(new Set<string>());
  const running = useRef(new Set<string>());
  const queue = useRef<(() => Promise<void>)[]>([]);
  const [error, setError] = useState("");
  const base = `/projects/${projectId}/attachments`;

  const write = useCallback((scope: string, change: (items: AttachmentDraft[]) => AttachmentDraft[]) => {
    const previous = latest.current.key === scope ? latest.current.items : read(scope);
    const next = change(previous);
    try { localStorage.setItem(scope, JSON.stringify(persistedAttachmentDrafts(next))); } catch { /* In-memory uploads still work. */ }
    if (latest.current.key === scope && mounted.current) {
      latest.current = { key: scope, items: next };
      setStored(latest.current);
    }
  }, []);

  const update = (scope: string, id: string, patch: Partial<AttachmentDraft>) =>
    write(scope, list => list.map(item => item.client_id === id ? { ...item, ...patch } : item));

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      urls.current.forEach(url => URL.revokeObjectURL(url));
      urls.current.clear();
    };
  }, []);

  const pump = () => {
    while (mounted.current && running.current.size < 2 && queue.current.length) void queue.current.shift()!();
  };
  const start = (draft: AttachmentDraft, file: File, scope = key) => {
    if (running.current.has(draft.client_id)) return;
    canceled.current.delete(draft.client_id);
    files.current.set(draft.client_id, file);
    update(scope, draft.client_id, { state: "preparing", error: undefined });
    queue.current.push(async () => {
      const id = draft.client_id;
      if (canceled.current.has(id) || !mounted.current) { pump(); return; }
      running.current.add(id);
      let server = draft.attachment;
      const stopped = () => canceled.current.has(id) || !mounted.current;
      try {
        const result = await uploadConversationAttachment(base, file, draft, attachment => {
          server = attachment;
          update(scope, id, { attachment, state: attachment.status === "ready" ? "ready" : attachment.status === "processing" ? "processing" : attachment.status === "failed" || attachment.status === "cancelled" ? "failed" : attachment.uploaded_bytes === attachment.size_bytes ? "processing" : "uploading", error: attachment.error || undefined });
        }, stopped);
        server = result || server;
        if (canceled.current.has(id) && server && !server.saved_at) {
          await api.del(`${base}/${server.id}`).catch(() => undefined);
        }
      } catch (e) {
        if (!stopped()) update(scope, id, { state: "failed", error: e instanceof Error ? e.message : "上传失败，请重试。" });
      } finally {
        running.current.delete(id);
        pump();
      }
    });
    pump();
  };

  // Recover processing status after reload without retransmitting the original file.
  const tracked = items.filter(item => item.attachment && item.state !== "ready").map(item => `${item.client_id}:${item.attachment!.id}`).join(",");
  useEffect(() => {
    if (!tracked) return;
    let alive = true;
    const poll = async () => {
      for (const pair of tracked.split(",")) {
        const [clientId, id] = pair.split(":");
        if (running.current.has(clientId) || canceled.current.has(clientId)) continue;
        try {
          const attachment = await api.get<ConversationAttachment>(`${base}/${id}`);
          if (!alive) return;
          const state = attachment.status === "uploading" ? "paused" : attachment.status === "cancelled" ? "failed" : attachment.status;
          update(key, clientId, { attachment, state, ...(attachment.error ? { error: attachment.error } : {}) });
        } catch { /* Keep the user's file and retry controls when disconnected. */ }
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2500);
    return () => { alive = false; window.clearInterval(timer); };
  }, [tracked, key, base]);

  const addFiles = (selected: FileList | File[], occupied = 0, roles?: ReadonlyMap<File, ReferenceRole>) => {
    setError("");
    const drafts: { item: AttachmentDraft; file: File }[] = [];
    const errors: string[] = [];
    const capacity = Math.max(0, 8 - latest.current.items.length - occupied);
    Array.from(selected).forEach(file => {
      if (drafts.length >= capacity) { errors.push("每次最多添加 8 项内容。"); return; }
      try {
        const kind = validateAttachmentFile(file);
        const id = crypto.randomUUID();
        const preview = URL.createObjectURL(file);
        urls.current.set(id, preview);
        drafts.push({ file, item: { client_id: id, filename: file.name, kind, size_bytes: file.size, role: roles?.get(file) || "reference", state: "preparing", preview_url: preview } });
      } catch (e) { errors.push(`${file.name}：${(e as Error).message}`); }
    });
    write(key, list => [...list, ...drafts.map(d => d.item)]);
    drafts.forEach(({ item, file }) => start(item, file));
    if (errors.length) setError([...new Set(errors)].join(" "));
  };
  const addSaved = (attachment: ConversationAttachment, occupied = 0) => {
    if (latest.current.items.some(item => item.attachment?.id === attachment.id)) return;
    if (latest.current.items.length + occupied >= 8) { setError("每次最多添加 8 项内容。"); return; }
    write(key, list => [...list, { client_id: crypto.randomUUID(), filename: attachment.filename, kind: attachment.kind, size_bytes: attachment.size_bytes, role: attachment.role || "reference", state: "ready", attachment }]);
  };
  const remove = (draft: AttachmentDraft) => {
    canceled.current.add(draft.client_id);
    write(key, list => list.filter(item => item.client_id !== draft.client_id));
    files.current.delete(draft.client_id);
    const url = urls.current.get(draft.client_id);
    if (url) URL.revokeObjectURL(url);
    urls.current.delete(draft.client_id);
    if (draft.attachment && !draft.attachment.saved_at && !running.current.has(draft.client_id))
      void api.del(`${base}/${draft.attachment.id}`).catch(() => undefined);
  };
  return {
    items, error, setError, addFiles, addSaved, remove,
    ready: items.every(item => item.state === "ready" && item.attachment?.blob_hash),
    references: items.flatMap(item => item.attachment ? [{ attachment_id: item.attachment.id, role: item.role }] : []),
    retry: (item: AttachmentDraft, file?: File) => {
      if (item.attachment?.status === "failed" && item.attachment.uploaded_bytes === item.attachment.size_bytes) {
        update(key, item.client_id, { state: "processing", error: undefined });
        void api.post<ConversationAttachment>(`${base}/${item.attachment.id}/complete`).then(attachment => update(key, item.client_id, { attachment, state: attachment.status === "ready" ? "ready" : "failed", error: attachment.error || undefined })).catch(e => update(key, item.client_id, { state: "failed", error: e.message }));
        return true;
      }
      const source = file || files.current.get(item.client_id);
      if (!source) return false;
      start(item, source);
      return true;
    },
    role: (id: string, role: ReferenceRole) => update(key, id, { role }),
    clear: () => {
      latest.current.items.forEach(item => {
        const url = urls.current.get(item.client_id);
        if (url) URL.revokeObjectURL(url);
        urls.current.delete(item.client_id);
        files.current.delete(item.client_id);
      });
      write(key, () => []); setError("");
    },
    restore: (attachments: ConversationAttachment[]) => write(key, () => attachments.map(attachment => ({ client_id: crypto.randomUUID(), filename: attachment.filename, kind: attachment.kind, size_bytes: attachment.size_bytes, role: attachment.role || "reference", state: "ready", attachment }))),
    restoreDrafts: (drafts: AttachmentDraft[]) => write(key, () => drafts),
  };
}
