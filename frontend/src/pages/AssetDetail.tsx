import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Lock, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import AssistPanel from "@/components/AssistPanel";
import { AssetActions, invalidateAssetLists, useAssetPermissions, useAssetTrash } from "@/components/AssetActions";
import { AssetUsageList } from "@/components/AssetUsageList";
import { MaterialTabs } from "@/components/MaterialTabs";
import {
  AssetVisualFields,
  ImpactDialog,
  type ImpactResult,
  type VisualMetadata,
} from "@/components/AssetContinuity";
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
import { useWorkspaceScroll } from "@/lib/useWorkspaceScroll";
import { api, apiUpload, blobUrl, type Asset, type AssetVersion, type RefImage } from "@/lib/api";

export default function AssetDetail() {
  const { projectId, assetId } = useParams();
  return <AssetDetailContent key={assetId} projectId={projectId!} assetId={assetId!} />;
}

function AssetDetailContent({ projectId, assetId }: { projectId: string; assetId: string }) {
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const confirm = useConfirm();
  const { t: tr, lang } = useI18n();
  const zh = lang === "zh";
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const tab = requestedTab && ["ref", "gen", "ai", "ver", "usages"].includes(requestedTab) ? requestedTab : "info";
  const setTab = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value === "info") next.delete("tab");
    else next.set("tab", value);
    setSearchParams(next, { replace: true });
  };
  const here = location.pathname + location.search;
  const candidate = searchParams.get("return");
  const local = new URL(candidate?.startsWith(base + "/") ? candidate : base + "/assets", window.location.origin);
  const returnTo = local.origin === window.location.origin && local.pathname.startsWith(base + "/")
    ? local.pathname + local.search + local.hash : base + "/assets";
  const { canEdit, canLock } = useAssetPermissions(projectId);
  const trash = useAssetTrash(projectId, () => {
    const target = new URL(returnTo, window.location.origin);
    if (target.searchParams.get("preview") === assetId) target.searchParams.delete("preview");
    navigate(target.pathname + target.search + target.hash, { replace: true });
  });
  const fileRef = useRef<HTMLInputElement>(null);

  const assetQuery = useQuery({
    queryKey: ["asset", assetId],
    queryFn: () => api.get<Asset>(`${base}/assets/${assetId}`),
  });
  const asset = assetQuery.data;
  const versionQuery = useQuery({
    queryKey: ["asset-versions", assetId],
    queryFn: () => api.get<AssetVersion[]>(`${base}/assets/${assetId}/versions`),
  });
  const refQuery = useQuery({
    queryKey: ["asset-refimg", assetId],
    queryFn: () => api.get<RefImage[]>(`${base}/assets/${assetId}/reference-images`),
  });
  const versions = versionQuery.data, refImages = refQuery.data;
  useWorkspaceScroll(`asset.detail.${projectId}.${assetId}.${tab}`, !!asset && tab !== "usages"
    && (tab !== "ver" || versionQuery.isSuccess) && (tab !== "ref" || refQuery.isSuccess));

  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");
  const [metadata, setMetadata] = useState<VisualMetadata>({});
  const [impact, setImpact] = useState<{
    result: ImpactResult;
    editing: boolean;
  } | null>(null);
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  useEffect(() => {
    if (asset) {
      setName(asset.name);
      setSummary(asset.summary ?? "");
      setMetadata(asset.metadata || {});
      setTags(asset.tags ?? []);
    }
  }, [asset]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["asset", assetId] });
    qc.invalidateQueries({ queryKey: ["asset-versions", assetId] });
    qc.invalidateQueries({ queryKey: ["asset-refimg", assetId] });
    invalidateAssetLists(qc, projectId);
  };

  const save = useMutation({
    mutationFn: () =>
      api.patch(`${base}/assets/${assetId}`, {
        name,
        summary,
        metadata,
        expected_version_id: asset?.current_version_id,
      }),
    onSuccess: () => {
      setImpact(null);
      invalidate();
      toast.push(tr("toast.saved"), "success");
    },
    onError: (e) => toast.push(e.message, "error"),
  });
  const previewImpact = useMutation({
    mutationFn: (editing: boolean) =>
      api.post<ImpactResult>(
        `${base}/assets/${assetId}/impact-preview`,
        editing ? { name, summary, metadata } : undefined,
      ),
    onSuccess: (result, editing) => {
      if (editing && !result.shots.length && !result.outputs.length)
        save.mutate();
      else setImpact({ result, editing });
    },
    onError: (e) => toast.push(e.message, "error"),
  });
  const saveTags = useMutation({
    mutationFn: (next: string[]) => api.patch(`${base}/assets/${assetId}`, { tags: next }),
    onSuccess: () => invalidate(),
    onError: (e) => { setTags(asset?.tags ?? []); toast.push(e.message, "error"); },
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
    onError: (e) => toast.push(e.message, "error"),
  });
  const rollback = useMutation({
    mutationFn: (vid: string) => api.post(`${base}/assets/${assetId}/rollback?version_id=${vid}`),
    onSuccess: () => { invalidate(); toast.push(tr("toast.rolledback"), "success"); },
    onError: (e) => toast.push(e.message, "error"),
  });
  const lock = useMutation({
    mutationFn: () => api.post(`${base}/assets/${assetId}/lock`),
    onSuccess: () => { invalidate(); toast.push(tr("toast.locked"), "success"); },
    onError: (e) => toast.push(e.message, "error"),
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

  if (assetQuery.isError) return (
    <div role="alert" className="studio-empty"><p>{assetQuery.error.message}</p>
      <Button variant="outline" onClick={() => void assetQuery.refetch()}>{zh ? "重试" : "Retry"}</Button>
      <Link className="studio-link" to={returnTo}>{zh ? "返回" : "Back"}</Link>
    </div>
  );
  if (!asset) return <p className="p-6 text-muted-foreground">{tr("common.loading")}</p>;
  const locked = asset.status === "locked";
  const readOnly = locked || !canEdit;
  const cover = asset.representative_blob_hash ? blobUrl(projectId!, asset.representative_blob_hash, "thumbnail") : null;

  return (
    <div className="studio-page material-workspace unified-materials">
      <Link className="studio-link mb-2 inline-flex min-h-11 items-center gap-1.5 text-sm" to={returnTo}>
        <ArrowLeft size={15} />{returnTo.split("?")[0] === base + "/assets" ? (zh ? "返回角色与场景" : "Back to characters & scenes") : (zh ? "返回来源页" : "Back to source")}
      </Link>
      <header className="materials-heading">
        <div className="flex min-w-0 items-center gap-3">
          <Avatar src={cover} name={asset.name} size={52} className="shrink-0 border" />
          <div className="min-w-0"><h1 className="break-words">{asset.name}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Badge variant="primary">{tr(`asset.${asset.type}`)}</Badge><span className="break-all">{asset.code}</span>
              {locked && <span className="inline-flex items-center gap-1"><Lock size={12} />{zh ? "已锁定" : "Locked"}</span>}
            </div>
          </div>
        </div>
        <AssetActions projectId={projectId} asset={asset} returnTo={returnTo} canTrash={canEdit && !trash.isPending} onTrash={() => trash.mutate([asset])} />
      </header>
      <div className="materials-navigation"><MaterialTabs projectId={projectId} /></div>

      <div className="max-w-[960px]">
        {asset.type === "character" && <Link className="studio-link mb-4 inline-block text-sm" to={`${base}/evidence?asset=${asset.id}&return=${encodeURIComponent(here)}`}>{zh ? "查看角色关联片段" : "View related clips"} ↗</Link>}
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { key: "info", label: tr("detail.info") },
            { key: "usages", label: tr("assets.colShots") },
            { key: "ref", label: `${tr("detail.ref")} ${refImages?.length ? `(${refImages.length})` : ""}` },
            { key: "gen", label: tr("detail.gen") },
            { key: "ai", label: tr("detail.ai") },
            { key: "ver", label: `${tr("detail.ver")} (${versions?.length ?? 0})` },
          ]}
        />

        <div className="pt-5">
          {tab === "usages" && <AssetUsageList projectId={projectId} assetId={assetId} returnTo={here} />}
          {tab === "info" && (
            <div className="space-y-3 max-w-lg">
              <div>
                <label htmlFor="asset-name" className="text-xs text-muted-foreground">{tr("detail.name")}</label>
                <Input id="asset-name" value={name} onChange={(e) => setName(e.target.value)} disabled={readOnly} />
              </div>
              <div>
                <label htmlFor="asset-summary" className="text-xs text-muted-foreground">{tr("detail.summary")}</label>
                <Textarea id="asset-summary" value={summary} onChange={(e) => setSummary(e.target.value)} disabled={readOnly} rows={5} placeholder={tr("detail.summaryPh")} />
              </div>
              <div>
                <label htmlFor="asset-tag" className="text-xs text-muted-foreground">{tr("detail.tags")}</label>
                <div className="flex flex-wrap items-center gap-1.5 mt-1">
                  {tags.map((tg) => (
                    <span key={tg} className="inline-flex items-center gap-1 rounded-full bg-primary/15 text-primary px-2 py-0.5 text-xs">
                      {tg}
                      {!readOnly && (
                        <button disabled={saveTags.isPending} aria-label={zh ? `移除标签 ${tg}` : `Remove tag ${tg}`} onClick={() => removeTag(tg)} className="hover:text-foreground">×</button>
                      )}
                    </span>
                  ))}
                  {!readOnly && (
                    <input
                      id="asset-tag"
                      value={tagInput}
                      disabled={saveTags.isPending}
                      onChange={(e) => setTagInput(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && addTag()}
                      placeholder={tr("detail.tagsPh")}
                      className="h-7 w-36 rounded-md border border-border bg-bg px-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                    />
                  )}
                </div>
              </div>
              <AssetVisualFields
                images={refImages || []}
                type={asset.type}
                value={metadata}
                onChange={setMetadata}
                disabled={readOnly}
              />
              {impact && (
                <ImpactDialog
                  base={base}
                  result={impact.result}
                  saving={save.isPending}
                  onSave={impact.editing ? () => save.mutate() : undefined}
                  onClose={() => setImpact(null)}
                />
              )}
              <div className="flex flex-wrap gap-2 pt-1">
                <Button
                  onClick={() => previewImpact.mutate(true)}
                  disabled={readOnly || previewImpact.isPending || save.isPending}
                >
                  {tr("detail.save")}
                </Button>
                <Button
                  variant="outline"
                  disabled={previewImpact.isPending}
                  onClick={() => previewImpact.mutate(false)}
                >
                  查看影响范围
                </Button>
                <Button
                  variant="outline"
                  onClick={() => commit.mutate()}
                  disabled={readOnly || commit.isPending}
                >
                  {tr("detail.commit")}
                </Button>
                <Button
                  variant="outline"
                  disabled={locked || !canLock || lock.isPending}
                  onClick={async () => {
                    if (await confirm({ title: tr("detail.lock"), message: tr("detail.lockConfirm").replace("{name}", asset.name), confirmText: tr("detail.lock") })) {
                      lock.mutate();
                    }
                  }}
                >
                  {tr("detail.lock")}
                </Button>
              </div>
            </div>
          )}

          {tab === "ref" && (
            <div>
              <input ref={fileRef} type="file" accept="image/*" hidden
                onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
              <Button size="sm" variant="outline" className="mb-4" disabled={readOnly || upload.isPending} onClick={() => fileRef.current?.click()}>
                <Upload className="h-4 w-4 mr-1" /> {tr("detail.uploadRef")}
              </Button>
              {refQuery.isError ? (
                <div role="alert" className="flex items-center gap-2 text-sm text-danger">{tr("common.loadFailed")}
                  <Button size="sm" variant="outline" onClick={() => void refQuery.refetch()}>{zh ? "重试" : "Retry"}</Button></div>
              ) : refQuery.isPending ? <p className="text-sm text-muted-foreground">{tr("common.loading")}</p> : refImages && refImages.length > 0 ? (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-3">
                  {refImages.map((r, i) => (
                    <ZoomableImage
                      key={r.id}
                      src={blobUrl(projectId!, r.blob_hash)}
                      previewSrc={blobUrl(projectId!, r.blob_hash, "thumbnail")}
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
              {versionQuery.isError && <div role="alert" className="flex items-center gap-2 text-sm text-danger">{tr("common.loadFailed")}
                <Button size="sm" variant="outline" onClick={() => void versionQuery.refetch()}>{zh ? "重试" : "Retry"}</Button></div>}
              {versionQuery.isPending && <p className="text-sm text-muted-foreground">{tr("common.loading")}</p>}
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
                    disabled={readOnly || rollback.isPending}
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
