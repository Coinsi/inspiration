import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, blobUrl, type Asset, type RefImage } from "@/lib/api";
import { Modal } from "@/components/ui/modal";
import { MediaImage } from "@/components/MediaImage";
import { useI18n } from "@/lib/i18n";

export function AssetPreview({
  projectId,
  asset,
  onClose,
}: {
  projectId: string;
  asset: Asset;
  onClose: () => void;
}) {
  const { lang, t } = useI18n(),
    zh = lang === "zh";
  const base = `/projects/${projectId}/assets/${asset.id}`;
  const refs = useQuery({
    queryKey: ["asset-refimg", asset.id],
    queryFn: () => api.get<RefImage[]>(`${base}/reference-images`),
  });
  return (
    <Modal open title={asset.name} onClose={onClose} width={940}>
      <div className="grid gap-5 md:grid-cols-[1.3fr_1fr]">
        <div className="relative aspect-[4/3] overflow-hidden rounded-lg border bg-elevated">
          <MediaImage
            src={
              asset.representative_blob_hash
                ? blobUrl(projectId, asset.representative_blob_hash)
                : null
            }
            alt={asset.name}
            className="[&_img]:object-contain"
          />
        </div>
        <div className="min-w-0 space-y-4">
          <p className="text-xs text-muted-foreground">
            {t(`asset.${asset.type}`)} · {asset.code} ·{" "}
            {asset.status === "locked"
              ? zh
                ? "已锁定"
                : "Locked"
              : zh
                ? "可编辑"
                : "Editable"}
          </p>
          <p className="whitespace-pre-wrap break-words text-sm leading-7">
            {asset.summary || (zh ? "尚未填写描述" : "No description yet")}
          </p>
          <div className="flex flex-wrap gap-2">
            {asset.tags.map((tag) => (
              <span
                className="rounded-full bg-muted px-2 py-1 text-xs"
                key={tag}
              >
                #{tag}
              </span>
            ))}
          </div>
          <div className="grid grid-cols-3 gap-2 rounded-lg border p-3 text-center text-xs text-muted-foreground">
            {[
              [t("detail.ref"), asset.ref_count],
              [t("detail.gen"), asset.gen_count],
              [t("assets.colShots"), asset.shot_count],
            ].map(([label, value]) => (
              <div key={label}>
                <strong className="mb-1 block text-lg text-foreground">
                  {value ?? 0}
                </strong>
                {label}
              </div>
            ))}
          </div>
          <div className="flex flex-wrap gap-3 text-sm">
            <Link className="studio-link" to={base}>
              {zh ? "打开完整详情" : "Open details"} →
            </Link>
            <Link className="studio-link" to={`${base}?tab=gen`}>
              {zh ? "生成与版本" : "Generate & versions"} →
            </Link>
          </div>
        </div>
      </div>
      <div className="mt-5 border-t pt-4">
        <h3 className="mb-3 text-sm font-medium">{t("detail.ref")}</h3>
        {refs.isPending ? (
          <p className="text-xs text-muted-foreground">{t("common.loading")}</p>
        ) : refs.isError ? (
          <p role="alert" className="text-xs text-danger">
            {t("common.loadFailed")}{" "}
            <button className="underline" onClick={() => void refs.refetch()}>
              {zh ? "重试" : "Retry"}
            </button>
          </p>
        ) : refs.data.length ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {refs.data.map((r, i) => (
              <div key={r.id}>
                <div className="relative aspect-video overflow-hidden rounded-md border">
                  <MediaImage
                    src={blobUrl(projectId, r.blob_hash)}
                    className="[&_img]:object-contain"
                    alt={r.note || `${t("detail.ref")} ${i + 1}`}
                  />
                </div>
                <p className="mt-1 truncate text-xs text-muted-foreground">
                  {r.note || r.role}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            {zh
              ? "暂无参考图，可在完整详情中上传。"
              : "No reference images. Upload them in the full details."}
          </p>
        )}
      </div>
    </Modal>
  );
}
