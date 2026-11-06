import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { UploadCloud } from "lucide-react";
import { api } from "@/lib/api";
import { mediaSize, type MediaVersion } from "@/lib/library";
import { Button } from "./ui/button";
import { useConfirm } from "./ui/confirm";

/** Server-backed queue: survives refresh, filters, navigation and a closed dialog. */
export function PendingUploads({ projectId, onResume, onChange, canEdit }: {
  projectId: string; onResume: (version: MediaVersion) => void; onChange: () => void; canEdit: boolean;
}) {
  const base = `/projects/${projectId}/library`;
  const qc = useQueryClient(), confirm = useConfirm();
  const [error, setError] = useState("");
  const [canceling, setCanceling] = useState("");
  const uploads = useQuery({
    queryKey: ["pending-uploads", projectId],
    queryFn: () => api.get<(MediaVersion & { name: string })[]>(`${base}/uploads`),
    refetchInterval: query => query.state.data?.length ? 5000 : 30_000,
    staleTime: 3000,
  });
  async function cancel(v: MediaVersion) {
    if (!await confirm({ title: "取消这次上传？", message: "已上传的分块会被清理。原文件和素材的其他版本不受影响。", confirmText: "取消上传", danger: true })) return;
    setCanceling(v.id); setError("");
    try {
      await api.post(`${base}/versions/${v.id}/cancel`);
      await qc.invalidateQueries({ queryKey: ["pending-uploads", projectId] });
      onChange();
    } catch (e) { setError((e as Error).message); }
    finally { setCanceling(""); }
  }
  if (uploads.isError && !uploads.data) return <p className="pending-upload-error" role="alert">未能读取待完成上传。<button onClick={() => void uploads.refetch()}>重试</button></p>;
  if (!uploads.data?.length) return null;
  return <section className="pending-uploads" aria-label="待完成上传">
    <div className="pending-upload-heading"><UploadCloud size={18} /><strong>待完成上传 · {uploads.data.length}</strong><span>进度已保留，选择原文件即可继续</span></div>
    <div className="pending-upload-list">{uploads.data.map(v => <div key={v.id} className="pending-upload-row">
      <div className="pending-upload-copy"><strong title={v.filename}>{v.filename}</strong><span>{mediaSize(v.uploaded_bytes)} / {mediaSize(v.size_bytes)} · {Math.floor(v.uploaded_bytes / Math.max(1, v.size_bytes) * 100)}%</span><progress aria-label={`${v.filename} 已上传`} value={v.uploaded_bytes} max={v.size_bytes} /></div>
      {canEdit && <div className="pending-upload-actions"><Button variant="outline" disabled={!!canceling} onClick={() => onResume(v)}>继续上传</Button><button disabled={!!canceling} onClick={() => void cancel(v)}>{canceling === v.id ? "取消中…" : "取消"}</button></div>}
    </div>)}</div>
    {error && <p role="alert">{error}</p>}
  </section>;
}
