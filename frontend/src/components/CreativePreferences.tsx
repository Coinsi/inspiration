import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";

export type CreativeDefaults = {
  revision: number;
  image_count: number;
  shot_count: number;
  use_references: boolean;
  generation_guidance: string;
  assistant_guidance: string;
};
export function useCreativeDefaults(projectId: string) {
  return useQuery({
    queryKey: ["creative-preferences", projectId],
    queryFn: () =>
      api.get<CreativeDefaults>(`/projects/${projectId}/creative-preferences`),
  });
}
const field =
  "w-full rounded-lg border border-border bg-background p-3 text-sm";
export default function CreativePreferences({
  projectId,
  admin,
}: {
  projectId: string;
  admin: boolean;
}) {
  const query = useCreativeDefaults(projectId),
    qc = useQueryClient(),
    confirm = useConfirm(),
    userId = useAuth().me?.user.id;
  const key = `creative-preferences-draft:${userId}:${projectId}`;
  const [local] = useState(() => {
    try {
      const v = JSON.parse(localStorage.getItem(key) ?? "null");
      return v &&
        typeof v.generation_guidance === "string" &&
        typeof v.assistant_guidance === "string" &&
        Number.isInteger(v.revision)
        ? (v as CreativeDefaults)
        : null;
    } catch {
      return null;
    }
  });
  const [draft, setDraft] = useState<CreativeDefaults | null>(local),
    [baseline, setBaseline] = useState<CreativeDefaults | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState(
      local ? "已恢复本机未保存偏好，请核对后保存。" : "",
    );
  const saving = useRef(false),
    dirty = !!draft && JSON.stringify(draft) !== JSON.stringify(baseline);
  useEffect(() => {
    if (query.data && !draft) {
      setDraft(query.data);
      setBaseline(query.data);
    }
  }, [query.data, draft]);
  useEffect(() => {
    try {
      if (dirty) localStorage.setItem(key, JSON.stringify(draft));
      else localStorage.removeItem(key);
    } catch {
      setError("本机草稿无法保存，请及时保存到项目。");
    }
  }, [key, dirty, draft]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  async function save() {
    if (!draft || saving.current) return;
    saving.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await api.put<CreativeDefaults>(
        `/projects/${projectId}/creative-preferences`,
        draft,
      );
      setDraft(result);
      setBaseline(result);
      qc.setQueryData(["creative-preferences", projectId], result);
      setMessage("项目创作偏好已保存，下次打开创作面板时使用默认项。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      saving.current = false;
    }
  }
  return (
    <div className="settings-pane">
      <div className="settings-pane-heading">
        <h2>创作偏好</h2>
        <p>当前项目共享的默认项与创作要求，保存到项目后在其他设备也可使用。</p>
      </div>
      {query.error && (
        <p role="alert" className="text-danger">
          {query.error.message}
          <button onClick={() => void query.refetch()}>重试</button>
        </p>
      )}
      {!draft ? (
        <p>正在读取偏好…</p>
      ) : (
        <fieldset className="space-y-6" disabled={busy || !admin}>
          <div className="settings-row">
            <div>
              <h3>默认候选数量</h3>
              <p>模型有限制时，以模型支持数量为准。</p>
            </div>
            <div className="flex flex-wrap gap-3">
              {(
                [
                  ["image_count", "资产图片"],
                  ["shot_count", "镜头作品"],
                ] as const
              ).map(([k, label]) => (
                <label key={k} className="text-sm">
                  {label}
                  <select
                    aria-label={label + "默认数量"}
                    className={field}
                    value={draft[k]}
                    onChange={(e) =>
                      setDraft({ ...draft, [k]: Number(e.target.value) })
                    }
                  >
                    {[1, 2, 3, 4].map((n) => (
                      <option key={n} value={n}>
                        {n} 个
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
          </div>
          <label className="flex items-center gap-3 text-sm">
            <input
              type="checkbox"
              checked={draft.use_references}
              onChange={(e) =>
                setDraft({ ...draft, use_references: e.target.checked })
              }
            />
            默认带入关联参考图（仅支持参考图的模型）
          </label>
          <label className="block space-y-2 text-sm">
            <span>图片与视频的项目要求</span>
            <textarea
              aria-label="图片与视频的项目要求"
              className={`${field} min-h-32`}
              maxLength={4000}
              placeholder="例如：统一使用柔和自然光，保持人物服装连续性。"
              value={draft.generation_guidance}
              onChange={(e) =>
                setDraft({ ...draft, generation_guidance: e.target.value })
              }
            />
            <span className="text-xs text-muted-foreground">
              生成与画布执行时附加到本次要求中，已启动任务保留原要求。
            </span>
          </label>
          <label className="block space-y-2 text-sm">
            <span>对象助理的改稿偏好</span>
            <textarea
              aria-label="对象助理的改稿偏好"
              className={`${field} min-h-32`}
              maxLength={4000}
              placeholder="例如：保留人物说话习惯，优先提出小范围修改。"
              value={draft.assistant_guidance}
              onChange={(e) =>
                setDraft({ ...draft, assistant_guidance: e.target.value })
              }
            />
            <span className="text-xs text-muted-foreground">
              用于剧本、镜头等对象侧栏助理的新消息。
            </span>
          </label>
          <div className="flex gap-3">
            <Button disabled={busy || !dirty} onClick={() => void save()}>
              {busy ? "正在保存…" : "保存创作偏好"}
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={async () => {
                if (
                  dirty &&
                  !(await confirm({
                    message: "重新载入会替换未保存的偏好，继续？",
                  }))
                )
                  return;
                setError("");
                const r = await query.refetch();
                if (r.data) {
                  setDraft(r.data);
                  setBaseline(r.data);
                  setMessage("已载入项目保存版本");
                }
              }}
            >
              重新载入
            </Button>
          </div>
        </fieldset>
      )}
      {!admin && (
        <p className="settings-note">仅项目管理员可以修改共享偏好。</p>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="text-sm text-success">
          {message}
        </p>
      )}
      <p className="settings-note">
        默认模型在渠道中设置。创作面板会优先保留你在本机选择的模型，首次进入时使用启用的默认图片模型。
      </p>
      <Link className="studio-link" to={`?section=channels`}>
        管理渠道与默认模型 ↗
      </Link>
    </div>
  );
}
