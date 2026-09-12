import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Lock, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import AssistPanel from "@/components/AssistPanel";
import GenerationPanel from "@/components/GenerationPanel";
import { ZoomableImage } from "@/components/ImageViewer";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { useConfirm } from "@/components/ui/confirm";
import { useI18n } from "@/lib/i18n";
import { api, apiUpload, blobUrl, type Asset, type AssetVersion, type RefImage } from "@/lib/api";

export default function AssetDetail() {
  const { projectId, assetId } = useParams();
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const confirm = useConfirm();
  const { t: tr } = useI18n();
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState(searchParams.get("tab") ?? "info");
  const fileRef = useRef<HTMLInputElement>(null);

  const { data: asset } = useQuery({
    queryKey: ["asset", assetId],
    queryFn: () => api.get<Asset>(`${base}/assets/${assetId}`),
  });
  const { data: versions } = useQuery({
    queryKey: ["asset-versions", assetId],
    queryFn: () => api.get<AssetVersion[]>(`${base}/assets/${assetId}/versions`),
  });
  const { data: refImages } = useQuery({
    queryKey: ["asset-refimg", assetId],
    queryFn: () => api.get<RefImage[]>(`${base}/assets/${assetId}/reference-images`),
  });

  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  useEffect(() => {
    if (asset) {
      setName(asset.name);
      setSummary(asset.summary ?? "");
      setTags(asset.tags ?? []);
    }
  }, [asset]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["asset", assetId] });
    qc.invalidateQueries({ queryKey: ["asset-versions", assetId] });
    qc.invalidateQueries({ queryKey: ["asset-refimg", assetId] });
    qc.invalidateQueries({ queryKey: ["assets", projectId] });
  };

  const save = useMutation({
    mutationFn: () => api.patch(`${base}/assets/${assetId}`, { name, summary }),
    onSuccess: () => { invalidate(); toast.push(tr("toast.saved"), "success"); },
  });
  const saveTags = useMutation({
    mutationFn: (next: string[]) => api.patch(`${base}/assets/${assetId}`, { tags: next }),
    onSuccess: () => invalidate(),
  });
  const addTag = () => {
    const v = tagInput.trim();
    if (!v || tags.includes(v)) return setTagInput("");
    const next = [...tags, v];
    setTags(next);
    setTagInput("");
    saveTags.mutate(next);
  };
  const removeTag = (tg: string) => {
    const next = tags.filter((x) => x !== tg);
    setTags(next);
    saveTags.mutate(next);
  };
  const commit = useMutation({
    mutationFn: () => api.post(`${base}/assets/${assetId}/versions`, { label: tr("detail.manualCommit") }),
    onSuccess: () => { invalidate(); toast.push(tr("toast.committed"), "success"); },
  });
  const rollback = useMutation({
    mutationFn: (vid: string) => api.post(`${base}/assets/${assetId}/rollback?version_id=${vid}`),
    onSuccess: () => { invalidate(); toast.push(tr("toast.rolledback"), "success"); },
  });
  const lock = useMutation({
    mutationFn: () => api.post(`${base}/assets/${assetId}/lock`),
    onSuccess: () => { invalidate(); toast.push(tr("toast.locked"), "success"); },
  });
  const del = useMutation({
    mutationFn: () => api.del(`${base}/assets/${assetId}`),
    onSuccess: () => navigate(`${base}/assets`),
  });
  const upload = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("role", "ref");
      return apiUpload(`${base}/assets/${assetId}/reference-images`, fd);
    },
    onSuccess: () => { invalidate(); toast.push(tr("toast.refUploaded"), "success"); },
    onError: (e) => toast.push((e as Error).message, "error"),
  });

  if (!asset) return <p className="p-6 text-muted-foreground">{tr("common.loading")}</p>;
  const locked = asset.status === "locked";
  const cover = asset.representative_blob_hash ? blobUrl(projectId!, asset.representative_blob_hash) : null;

  return (
    <div>
      {/* Hero */}
      <div className="relative h-44 overflow-hidden bg-surface border-b border-border">
        <button
          onClick={() => navigate(`${base}/assets`)}
          className="absolute top-4 left-6 flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> {tr("detail.back")}
        </button>
        <div className="absolute bottom-4 left-6 flex items-end gap-4">
          <Avatar src={cover} name={asset.name} size={72} className="border border-border" />
          <div className="pb-1">
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold">{asset.name}</h1>
              {locked && <Lock className="h-4 w-4 text-muted-foreground" />}
            </div>
            <div className="flex items-center gap-2 mt-1">
              <Badge variant="primary">{tr(`asset.${asset.type}`)}</Badge>
              <span className="text-xs text-muted-foreground font-code">{asset.code}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-[960px] mx-auto p-4 md:p-6">
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { key: "info", label: tr("detail.info") },
            { key: "ref", label: `${tr("detail.ref")} ${refImages?.length ? `(${refImages.length})` : ""}` },
            { key: "gen", label: tr("detail.gen") },
            { key: "ai", label: tr("detail.ai") },
            { key: "ver", label: `${tr("detail.ver")} (${versions?.length ?? 0})` },
          ]}
        />

        <div className="pt-5">
          {tab === "info" && (
            <div className="space-y-3 max-w-lg">
              <div>
                <label className="text-xs text-muted-foreground">{tr("detail.name")}</label>
                <Input value={name} onChange={(e) => setName(e.target.value)} disabled={locked} />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">{tr("detail.summary")}</label>
                <Textarea value={summary} onChange={(e) => setSummary(e.target.value)} disabled={locked} rows={5} placeholder={tr("detail.summaryPh")} />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">{tr("detail.tags")}</label>
                <div className="flex flex-wrap items-center gap-1.5 mt-1">
                  {tags.map((tg) => (
                    <span key={tg} className="inline-flex items-center gap-1 rounded-full bg-primary/15 text-primary px-2 py-0.5 text-xs">
                      {tg}
                      {!locked && (
                        <button onClick={() => removeTag(tg)} className="hover:text-foreground">×</button>
                      )}
                    </span>
                  ))}
                  {!locked && (
                    <input
                      value={tagInput}
                      onChange={(e) => setTagInput(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && addTag()}
                      placeholder={tr("detail.tagsPh")}
                      className="h-7 w-36 rounded-md border border-border bg-bg px-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                    />
                  )}
                </div>
              </div>
              <div className="flex flex-wrap gap-2 pt-1">
                <Button onClick={() => save.mutate()} disabled={locked}>{tr("detail.save")}</Button>
                <Button variant="outline" onClick={() => commit.mutate()} disabled={locked}>{tr("detail.commit")}</Button>
                <Button
                  variant="outline"
                  disabled={locked}
                  onClick={async () => {
                    if (await confirm({ title: tr("detail.lock"), message: tr("detail.lockConfirm").replace("{name}", asset.name), confirmText: tr("detail.lock") })) {
                      lock.mutate();
                    }
                  }}
                >
                  {tr("detail.lock")}
                </Button>
                <Button
                  variant="ghost"
                  className="text-danger"
                  onClick={async () => {
                    const ok = await confirm({
                      title: tr("detail.del"),
                      message: tr("detail.delConfirm").replace("{name}", asset.name),
                      confirmText: tr("common.delete"),
                      danger: true,
                    });
                    if (ok) del.mutate();
                  }}
                >
                  {tr("detail.del")}
                </Button>
              </div>
            </div>
          )}

          {tab === "ref" && (
            <div>
              <input ref={fileRef} type="file" accept="image/*" hidden
                onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
              <Button size="sm" variant="outline" className="mb-4" onClick={() => fileRef.current?.click()}>
                <Upload className="h-4 w-4 mr-1" /> {tr("detail.uploadRef")}
              </Button>
              {refImages && refImages.length > 0 ? (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-3">
                  {refImages.map((r, i) => (
                    <ZoomableImage
                      key={r.id}
                      src={blobUrl(projectId!, r.blob_hash)}
                      alt={r.role}
                      filename={`${asset.name}-ref-${i + 1}`}
                      className="aspect-square w-full object-cover rounded-lg border border-border transition hover:border-primary/60"
                    />
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">{tr("detail.noRef")}</p>
              )}
            </div>
          )}

          {tab === "gen" && (
            <GenerationPanel projectId={projectId!} targetType="asset" targetId={asset.id} />
          )}

          {tab === "ai" && (
            <AssistPanel
              projectId={projectId!}
              targetType="asset"
              targetId={asset.id}
              className="h-[480px]"
              onApplied={invalidate}
            />
          )}

          {tab === "ver" && (
            <div className="space-y-2 max-w-lg">
              {versions?.map((v) => (
                <div key={v.id} className="flex items-center justify-between rounded-md border border-border bg-card px-3 py-2 text-sm">
                  <span>
                    <span className="font-code">v{v.version_no}</span>
                    {v.label && <span className="text-muted-foreground"> · {v.label}</span>}
                    {v.is_locked && <Lock className="inline h-3 w-3 ml-1" />}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={locked}
                    onClick={async () => {
                      if (await confirm({ title: tr("detail.rollback"), message: tr("detail.rollbackConfirm").replace("{n}", String(v.version_no)), confirmText: tr("detail.rollback") })) {
                        rollback.mutate(v.id);
                      }
                    }}
                  >
                    {tr("detail.rollback")}
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
