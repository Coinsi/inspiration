import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Camera, Pencil, X } from "lucide-react";
import { api, apiUpload } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Modal } from "./ui/modal";
import { useToast } from "./ui/toast";
import "./material-profile.css";

type Props = {
  projectId: string;
  source: "upload" | "generated" | "attachment";
  item: {
    id: string;
    name: string;
    description?: string;
    tags?: string[] | null;
    metadata_revision?: number;
  };
  video?: { src: string; versionId?: string };
  compact?: boolean;
};
export function MaterialProfile(props: Props) {
  const [editing, setEditing] = useState(false);
  const { me } = useAuth();
  const canEdit = me?.memberships.some(
    (m) =>
      m.project_id === props.projectId &&
      ["admin", "director", "artist"].includes(m.role),
  );
  return (
    <>
      {!props.compact && (
        <div className="material-profile-summary">
          {props.item.description && <p>{props.item.description}</p>}
          {!!props.item.tags?.length && (
            <div>
              {props.item.tags.map((tag) => (
                <span key={tag}>{tag}</span>
              ))}
            </div>
          )}
        </div>
      )}
      {canEdit && (
        <button
          type="button"
          className="material-profile-trigger"
          aria-label={`编辑素材资料：${props.item.name}`}
          onClick={() => setEditing(true)}
        >
          <Pencil size={14} />
          编辑资料
        </button>
      )}
      {editing && (
        <MaterialProfileEditor {...props} onClose={() => setEditing(false)} />
      )}
    </>
  );
}
export function MaterialProfileEditor({
  projectId,
  source,
  item,
  video,
  onClose,
}: Props & { onClose: () => void }) {
  const [name, setName] = useState(item.name);
  const [revision] = useState(item.metadata_revision ?? 1);
  const [description, setDescription] = useState(item.description ?? "");
  const [tags, setTags] = useState(item.tags ?? []);
  const [draftTag, setDraftTag] = useState("");
  const [error, setError] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [frame, setFrame] = useState<Blob | null>(null);
  const [framePreview, setFramePreview] = useState("");
  const [showVideo, setShowVideo] = useState(false);
  const [videoReady, setVideoReady] = useState(false);
  const player = useRef<HTMLVideoElement>(null);
  const pending = useRef(false);
  const qc = useQueryClient();
  const toast = useToast();
  function addTags(): string[] | null {
    const added = draftTag
      .split(/[,，、\n]/)
      .map((t) => t.trim())
      .filter(Boolean);
    const result = [...new Set([...tags, ...added])];
    if (result.length > 20 || result.some((t) => t.length > 40)) {
      setError("最多20个标签，每个不超过40字");
      return null;
    }
    setTags(result);
    setDraftTag("");
    setError("");
    return result;
  }
  async function capture() {
    const el = player.current;
    if (!el || el.readyState < 2 || el.seeking || busy || capturing) return;
    el.pause();
    setCapturing(true);
    try {
      const canvas = document.createElement("canvas");
      const ratio = Math.min(1, 1280 / Math.max(el.videoWidth, el.videoHeight));
      canvas.width = Math.max(1, Math.round(el.videoWidth * ratio));
      canvas.height = Math.max(1, Math.round(el.videoHeight * ratio));
      canvas.getContext("2d")!.drawImage(el, 0, 0, canvas.width, canvas.height);
      const selected = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob(resolve, "image/jpeg", 0.92),
      );
      if (!selected) throw new Error();
      setFrame(selected);
      setFramePreview(canvas.toDataURL("image/jpeg", 0.92));
      setError("");
    } catch {
      setError("暂时无法读取这帧画面，请等待视频加载后重试");
    } finally {
      setCapturing(false);
    }
  }
  async function save() {
    if (pending.current || capturing || !name.trim()) return;
    const selectedTags = addTags();
    if (!selectedTags) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const data = new FormData();
      data.append(
        "metadata",
        JSON.stringify({
          name: name.trim(),
          description: description.trim(),
          tags: selectedTags,
          revision,
          version_id: video?.versionId ?? null,
        }),
      );
      if (frame) data.append("file", frame, "video-cover.jpg");
      if (source === "attachment") {
        await api.patch(`/projects/${projectId}/attachments/${item.id}`, {
          title: name.trim(), description: description.trim(), tags: selectedTags,
          expected_revision: revision,
        });
      } else await apiUpload(
        `/projects/${projectId}/library/materials/${source}/${item.id}/profile`,
        data,
      );
      await Promise.all(
        [
          "materials",
          "material-attachment",
          "conversation-attachments",
          "saved-conversation-attachments",
          "workbench-references",
          "library",
          "library-item",
          "generated-library",
          "generated-material",
          "material-generation",
          "library-versions",
          "library-search",
          "library-folders",
        ].map((key) => qc.invalidateQueries({ queryKey: [key, projectId] })),
      );
      toast.push("素材资料已保存", "success");
      onClose();
    } catch (e) {
      setError((e as Error).message);
      for (const key of ["materials", "library-item", "material-generation", "material-attachment"])
        void qc.invalidateQueries({ queryKey: [key, projectId] });
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <Modal
      open
      title="编辑素材资料"
      width={600}
      onClose={() => {
        if (!pending.current && !capturing) onClose();
      }}
    >
      <form
        className="material-profile-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <label>
          素材名称 <span>仅用于展示，无需填写后缀</span>
          <Input
            autoFocus
            data-autofocus
            value={name}
            required
            maxLength={255}
            disabled={capturing || busy}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          说明与备注 <span>选填</span>
          <textarea
            value={description}
            maxLength={4000}
            rows={3}
            disabled={capturing || busy}
            placeholder="这份素材的内容、用途或需要注意的地方…"
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <div className="material-profile-tag-field">
          <label htmlFor="material-profile-tags">
            标签 <span>选填</span>
          </label>
          <div className="material-profile-tags">
            {tags.map((tag) => (
              <span key={tag}>
                {tag}
                <button
                  type="button"
                  aria-label={`移除标签：${tag}`}
                  disabled={capturing || busy}
                  onClick={() => setTags(tags.filter((t) => t !== tag))}
                >
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
          <div className="material-profile-add-tag">
            <Input
              id="material-profile-tags"
              value={draftTag}
              disabled={capturing || busy}
              placeholder="例如：雨夜、城市、空镜；用逗号分隔"
              onChange={(e) => setDraftTag(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  addTags();
                }
              }}
            />
            <Button
              type="button"
              variant="outline"
              disabled={capturing || busy || !draftTag.trim()}
              onClick={() => addTags()}
            >
              添加
            </Button>
          </div>
          <small>
            最多20个标签，每个不超过40字；保存时会一并加入输入中的标签。
          </small>
        </div>
        {video && (
          <section className="material-profile-video">
            <Button
              type="button"
              variant="outline"
              disabled={capturing || busy}
              onClick={() => setShowVideo(!showVideo)}
            >
              <Camera size={15} />
              {showVideo ? "收起视频" : "从视频选封面"}
            </Button>
            {showVideo && (
              <>
                <p>拖动视频进度找到画面，再点击选取。保存资料后生效。</p>
                <video
                  ref={player}
                  src={video.src}
                  controls
                  preload="metadata"
                  playsInline
                  onLoadedData={() => setVideoReady(true)}
                  onSeeking={() => setVideoReady(false)}
                  onSeeked={() => setVideoReady(true)}
                  onError={() => {
                    setVideoReady(false);
                    setError("视频暂时无法加载，可以先保存文字资料");
                  }}
                />
                <Button
                  type="button"
                  variant="outline"
                  disabled={capturing || busy || !videoReady}
                  onClick={() => void capture()}
                >
                  选取当前画面
                </Button>
              </>
            )}
            {frame && (
              <div className="material-profile-frame">
                <img src={framePreview} alt="待保存的视频封面" />
                <span>已选取封面，待保存</span>
                <button
                  type="button"
                  disabled={capturing || busy}
                  onClick={() => {
                    setFrame(null);
                    setFramePreview("");
                  }}
                >
                  取消选取
                </button>
              </div>
            )}
          </section>
        )}
        <p className="material-profile-hint">
          修改资料不影响原文件、生成记录或已有引用。
        </p>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <footer>
          <Button
            type="button"
            variant="ghost"
            disabled={capturing || busy}
            onClick={onClose}
          >
            取消
          </Button>
          <Button disabled={capturing || busy || !name.trim()} type="submit">
            {busy ? "保存中…" : "保存修改"}
          </Button>
        </footer>
      </form>
    </Modal>
  );
}
