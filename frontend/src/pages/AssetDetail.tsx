import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as Tabs from "@radix-ui/react-tabs";
import { ArrowLeft, Film, History, Lock, Pencil, Sparkles, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import AssistPanel from "@/components/AssistPanel";
import {
  AssetActions,
  invalidateAssetLists,
  useAssetPermissions,
  useAssetTrash,
} from "@/components/AssetActions";
import { AssetUsageList } from "@/components/AssetUsageList";
import {
  AssetVisualFields,
  ImpactDialog,
  type ImpactResult,
  type VisualMetadata,
} from "@/components/AssetContinuity";
import GenerationPanel from "@/components/GenerationPanel";
import { ZoomableImage } from "@/components/ImageViewer";
import { Avatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { ActionMenuItem } from "@/components/ui/header-menu";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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
  const tab =
    requestedTab && ["ref", "gen", "ai", "ver", "info"].includes(requestedTab) ? requestedTab : "usages";
  const setTab = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value === "usages") next.delete("tab");
    else next.set("tab", value);
    setSearchParams(next, { replace: true });
  };
  const here = location.pathname + location.search;
  const candidate = searchParams.get("return");
  const local = new URL(
    candidate?.startsWith(base + "/") ? candidate : base + "/assets",
    window.location.origin,
  );
  if (local.pathname === base + "/assets") local.searchParams.delete("preview");
  const returnTo =
    local.origin === window.location.origin && local.pathname.startsWith(base + "/")
      ? local.pathname + local.search + local.hash
      : base + "/assets";
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
    enabled: tab === "ver",
  });
  const refQuery = useQuery({
    queryKey: ["asset-refimg", assetId],
    queryFn: () => api.get<RefImage[]>(`${base}/assets/${assetId}/reference-images`),
    enabled: tab === "ref" || tab === "info",
  });
  const versions = versionQuery.data,
    refImages = refQuery.data;
  useWorkspaceScroll(
    `asset.detail.${projectId}.${assetId}.${tab}`,
    !!asset &&
      tab !== "usages" &&
      (tab !== "ver" || versionQuery.isSuccess) &&
      (tab !== "ref" || refQuery.isSuccess),
  );

  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");
  const [metadata, setMetadata] = useState<VisualMetadata>({});
  const [impact, setImpact] = useState<{
    result: ImpactResult;
    editing: boolean;
  } | null>(null);
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const editSource = useRef<Asset | null>(null);
  useEffect(() => {
    if (!asset || (tab === "info" && editSource.current)) return;
    editSource.current = tab === "info" ? asset : null;
    setName(asset.name);
    setSummary(asset.summary ?? "");
    setMetadata(asset.metadata || {});
    setTags(asset.tags ?? []);
    setTagInput("");
  }, [asset, tab]);

  const pendingTag = tagInput.trim();
  const draftTags = pendingTag && !tags.includes(pendingTag) ? [...tags, pendingTag] : tags;
  const draft = { name: name.trim(), summary, metadata, tags: draftTags };

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["asset", assetId] });
    qc.invalidateQueries({ queryKey: ["asset-versions", assetId] });
    qc.invalidateQueries({ queryKey: ["asset-refimg", assetId] });
    invalidateAssetLists(qc, projectId);
  };

  const save = useMutation({
    mutationFn: () =>
      api.patch<Asset>(`${base}/assets/${assetId}`, {
        ...draft,
        expected_version_id: editSource.current?.current_version_id,
      }),
    onSuccess: (updated) => {
      qc.setQueryData(["asset", assetId], updated);
      setImpact(null);
      invalidate();
      setTab("usages");
      toast.push(tr("toast.saved"), "success");
    },
    onError: (e) => toast.push(e.message, "error"),
  });
  const previewImpact = useMutation({
    mutationFn: (editing: boolean) =>
      api.post<ImpactResult>(`${base}/assets/${assetId}/impact-preview`, editing ? draft : undefined),
    onSuccess: (result, editing) => {
      if (editing && !result.shots.length && !result.outputs.length) save.mutate();
      else setImpact({ result, editing });
    },
    onError: (e) => toast.push(e.message, "error"),
  });
  const commit = useMutation({
    mutationFn: () =>
      api.post(`${base}/assets/${assetId}/versions`, { label: tr("detail.manualCommit") }),
    onSuccess: () => {
      invalidate();
      toast.push(tr("toast.committed"), "success");
    },
    onError: (e) => toast.push(e.message, "error"),
  });
  const rollback = useMutation({
    mutationFn: (vid: string) => api.post(`${base}/assets/${assetId}/rollback?version_id=${vid}`),
    onSuccess: () => {
      invalidate();
      toast.push(tr("toast.rolledback"), "success");
    },
    onError: (e) => toast.push(e.message, "error"),
  });
  const lock = useMutation({
    mutationFn: () => api.post(`${base}/assets/${assetId}/lock`),
    onSuccess: () => {
      invalidate();
      toast.push(tr("toast.locked"), "success");
    },
    onError: (e) => toast.push(e.message, "error"),
  });
  const upload = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("role", "ref");
      return apiUpload(`${base}/assets/${assetId}/reference-images`, fd);
    },
    onSuccess: () => {
      invalidate();
      toast.push(tr("toast.refUploaded"), "success");
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });

  if (assetQuery.isError)
    return (
      <div role="alert" className="studio-empty">
        <p>{assetQuery.error.message}</p>
        <Button variant="outline" onClick={() => void assetQuery.refetch()}>
          {zh ? "重试" : "Retry"}
        </Button>
        <Link className="studio-link" to={returnTo}>
          {zh ? "返回" : "Back"}
        </Link>
      </div>
    );
  if (!asset) return <p className="p-6 text-muted-foreground">{tr("common.loading")}</p>;
  const locked = asset.status === "locked";
  const readOnly = locked || !canEdit;
  const cover = asset.representative_blob_hash
    ? blobUrl(projectId!, asset.representative_blob_hash, "thumbnail")
    : null;
  const typeName = tr(`asset.${asset.type}`);
  const appearancesLabel =
    asset.type === "character"
      ? zh
        ? "出场与镜头"
        : "Appearances & shots"
      : zh
        ? "场次与镜头"
        : "Scenes & shots";
  const editLabel = readOnly ? (zh ? "查看资料" : "View details") : zh ? "编辑资料" : "Edit details";
  const secondaryTabs: Record<string, string> = {
    info: editLabel,
    ai: tr("detail.ai"),
    ver: zh ? "版本历史" : "Version history",
  };

  return (
    <div className="studio-page material-workspace unified-materials">
      <Link className="studio-link mb-2 inline-flex min-h-11 items-center gap-1.5 text-sm" to={returnTo}>
        <ArrowLeft size={15} />
        {returnTo.split("?")[0] === base + "/assets"
          ? zh
            ? "返回角色与场景"
            : "Back to characters & scenes"
          : zh
            ? "返回来源页"
            : "Back to source"}
      </Link>
      <div className="max-w-[960px]">
        <header className="mb-5 flex flex-wrap items-start gap-x-5 gap-y-3">
          {cover && asset.representative_blob_hash ? (
            <ZoomableImage
              src={blobUrl(projectId, asset.representative_blob_hash)}
              previewSrc={cover}
              alt={zh ? `放大${asset.name}的参考图` : `Enlarge reference image for ${asset.name}`}
              filename={asset.name}
              className="h-24 w-20 shrink-0 rounded-lg bg-elevated object-contain sm:h-32 sm:w-24"
            />
          ) : (
            <Avatar name={asset.name} size={56} className="shrink-0" />
          )}
          <div className="min-w-0 flex-1 basis-40">
            <p className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
              {typeName}
              {locked && (
                <span className="inline-flex items-center gap-1">
                  <Lock size={12} />
                  {zh ? "已锁定" : "Locked"}
                </span>
              )}
            </p>
            <h1 className="break-words text-2xl font-semibold">{asset.name}</h1>
            {asset.summary && tab !== "info" && (
              <p className="mt-2 whitespace-pre-line break-words text-sm leading-6">{asset.summary}</p>
            )}
            {!!asset.tags?.length && tab !== "info" && (
              <p className="mt-2 break-words text-xs text-muted-foreground">{asset.tags.join(" · ")}</p>
            )}
          </div>
          <div className="flex items-center gap-1">
            {tab !== "info" && (
              <Button size="sm" variant="outline" onClick={() => setTab("info")}>
                <Pencil size={14} />
                {editLabel}
              </Button>
            )}
            <AssetActions
              projectId={projectId}
              asset={asset}
              returnTo={returnTo}
              canTrash={canEdit && !trash.isPending}
              onTrash={() => trash.mutate([asset])}
            >
              <ActionMenuItem onSelect={() => setTab("ai")}>
                <Sparkles />
                {tr("detail.ai")}
              </ActionMenuItem>
              <ActionMenuItem onSelect={() => setTab("ver")}>
                <History />
                {secondaryTabs.ver}
              </ActionMenuItem>
              {asset.type === "character" && (
                <ActionMenuItem asChild>
                  <Link to={`${base}/evidence?asset=${asset.id}&return=${encodeURIComponent(here)}`}>
                    <Film />
                    {zh ? "查看关联视频片段" : "View linked video clips"}
                  </Link>
                </ActionMenuItem>
              )}
            </AssetActions>
          </div>
        </header>
        <Tabs.Root value={tab} onValueChange={setTab}>
          {secondaryTabs[tab] ? (
            <div className="flex items-center justify-between gap-2 border-b pb-2">
              <h2 className="font-semibold">{secondaryTabs[tab]}</h2>
              <Button size="sm" variant="ghost" onClick={() => setTab("usages")}>
                <ArrowLeft size={14} />
                {zh ? `返回${typeName}主页` : "Back to overview"}
              </Button>
            </div>
          ) : (
            <Tabs.List
              aria-label={zh ? "素材详情" : "Asset details"}
              className="flex max-w-full gap-2 overflow-x-auto border-b"
            >
              {[
                ["usages", appearancesLabel],
                ["ref", tr("detail.ref")],
                ["gen", tr("detail.gen")],
              ].map(([key, label]) => (
                <Tabs.Trigger
                  key={key}
                  value={key}
                  className="min-h-11 shrink-0 border-b-2 border-transparent px-2 text-sm text-muted-foreground data-[state=active]:border-primary data-[state=active]:font-semibold data-[state=active]:text-foreground"
                >
                  {label}
                </Tabs.Trigger>
              ))}
            </Tabs.List>
          )}
          <div className="pt-5">
            <Tabs.Content value="usages">
              <AssetUsageList
                projectId={projectId}
                asset={asset}
                canEdit={canEdit}
                returnTo={here}
                initialScene={local.searchParams.get("scene_id")}
              />
            </Tabs.Content>
            {tab === "info" && (
              <div className="space-y-3 max-w-lg">
                <div>
                  <label htmlFor="asset-name" className="text-xs text-muted-foreground">
                    {tr("detail.name")}
                  </label>
                  <Input
                    id="asset-name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    disabled={readOnly}
                  />
                </div>
                <div>
                  <label htmlFor="asset-summary" className="text-xs text-muted-foreground">
                    {tr("detail.summary")}
                  </label>
                  <Textarea
                    id="asset-summary"
                    value={summary}
                    onChange={(e) => setSummary(e.target.value)}
                    disabled={readOnly}
                    rows={5}
                    placeholder={tr("detail.summaryPh")}
                  />
                </div>
                <div>
                  <label htmlFor="asset-tag" className="text-xs text-muted-foreground">
                    {tr("detail.tags")}
                  </label>
                  <div className="flex flex-wrap items-center gap-1.5 mt-1">
                    {tags.map((tg) => (
                      <span
                        key={tg}
                        className="inline-flex items-center gap-1 rounded-full bg-primary/15 text-primary px-2 py-0.5 text-xs"
                      >
                        {tg}
                        {!readOnly && (
                          <Button
                            type="button"
                            size="icon"
                            variant="ghost"
                            className="size-6"
                            aria-label={zh ? `移除标签 ${tg}` : `Remove tag ${tg}`}
                            onClick={() => setTags(tags.filter((tag) => tag !== tg))}
                          >
                            <X size={12} />
                          </Button>
                        )}
                      </span>
                    ))}
                    {!readOnly && (
                      <Input
                        id="asset-tag"
                        value={tagInput}
                        onChange={(e) => setTagInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
                          e.preventDefault();
                          setTags(draftTags);
                          setTagInput("");
                        }}
                        placeholder={tr("detail.tagsPh")}
                        className="h-8 w-40 text-sm"
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
                    disabled={readOnly || !draft.name || previewImpact.isPending || save.isPending}
                  >
                    {tr("detail.save")}
                  </Button>
                  <Button
                    variant="outline"
                    disabled={previewImpact.isPending}
                    onClick={() => previewImpact.mutate(false)}
                  >
                    {zh ? "查看影响范围" : "View impact"}
                  </Button>
                </div>
              </div>
            )}

            <Tabs.Content value="ref">
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                hidden
                onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])}
              />
              <Button
                size="sm"
                variant="outline"
                className="mb-4"
                disabled={readOnly || upload.isPending}
                onClick={() => fileRef.current?.click()}
              >
                <Upload className="h-4 w-4 mr-1" /> {tr("detail.uploadRef")}
              </Button>
              {refQuery.isError ? (
                <div role="alert" className="flex items-center gap-2 text-sm text-danger">
                  {tr("common.loadFailed")}
                  <Button size="sm" variant="outline" onClick={() => void refQuery.refetch()}>
                    {zh ? "重试" : "Retry"}
                  </Button>
                </div>
              ) : refQuery.isPending ? (
                <p className="text-sm text-muted-foreground">{tr("common.loading")}</p>
              ) : refImages && refImages.length > 0 ? (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-3">
                  {refImages.map((r, i) => (
                    <figure key={r.id} className="min-w-0">
                      <ZoomableImage
                        src={blobUrl(projectId!, r.blob_hash)}
                        previewSrc={blobUrl(projectId!, r.blob_hash, "thumbnail")}
                        alt={r.note || `${asset.name} · ${tr("detail.ref")} ${i + 1}`}
                        filename={`${asset.name}-ref-${i + 1}`}
                        className="aspect-square w-full object-contain rounded-lg border bg-elevated"
                      />
                      {r.note && (
                        <figcaption className="mt-1 break-words text-sm text-muted-foreground">
                          {r.note}
                        </figcaption>
                      )}
                    </figure>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">{tr("detail.noRef")}</p>
              )}
            </Tabs.Content>

            <Tabs.Content value="gen">
              <GenerationPanel projectId={projectId!} targetType="asset" targetId={asset.id} />
            </Tabs.Content>

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
                {!readOnly && (
                  <div className="mb-3 flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={commit.isPending}
                      onClick={() => commit.mutate()}
                    >
                      {tr("detail.commit")}
                    </Button>
                    {canLock && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={lock.isPending}
                        onClick={async () => {
                          if (
                            await confirm({
                              title: tr("detail.lock"),
                              message: tr("detail.lockConfirm").replace("{name}", asset.name),
                              confirmText: tr("detail.lock"),
                            })
                          )
                            lock.mutate();
                        }}
                      >
                        {tr("detail.lock")}
                      </Button>
                    )}
                  </div>
                )}
                {versionQuery.isError && (
                  <div role="alert" className="flex items-center gap-2 text-sm text-danger">
                    {tr("common.loadFailed")}
                    <Button size="sm" variant="outline" onClick={() => void versionQuery.refetch()}>
                      {zh ? "重试" : "Retry"}
                    </Button>
                  </div>
                )}
                {versionQuery.isPending && (
                  <p className="text-sm text-muted-foreground">{tr("common.loading")}</p>
                )}
                {versions?.map((v) => (
                  <div
                    key={v.id}
                    className="flex items-center justify-between rounded-md border border-border bg-card px-3 py-2 text-sm"
                  >
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
                        if (
                          await confirm({
                            title: tr("detail.rollback"),
                            message: tr("detail.rollbackConfirm").replace("{n}", String(v.version_no)),
                            confirmText: tr("detail.rollback"),
                          })
                        ) {
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
        </Tabs.Root>
      </div>
    </div>
  );
}
