import { blobUrl, type RefImage } from "@/lib/api";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { SelectField } from "@/components/ui/select-field";

export const aspectLabels: Record<string, string> = {
  face: "面部",
  wardrobe: "衣着",
  layout: "空间布局",
  light: "光线",
  appearance: "外观细节",
};
export interface VisualMetadata extends Record<string, unknown> {
  aliases?: string[];
  look_images?: Record<string, string>;
  visual_traits?: Record<string, string>;
  looks?: Record<string, Record<string, string>>;
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
function textFields(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter(
      (pair): pair is [string, string] => typeof pair[1] === "string",
    ),
  );
}
export function visualMetadata(raw: unknown): VisualMetadata {
  if (!isRecord(raw)) return {};
  return {
    ...raw,
    aliases: Array.isArray(raw.aliases)
      ? raw.aliases.filter((a): a is string => typeof a === "string")
      : [],
    visual_traits: textFields(raw.visual_traits),
    look_images: textFields(raw.look_images),
    looks: isRecord(raw.looks)
      ? Object.fromEntries(
          Object.entries(raw.looks)
            .filter(([, v]) => isRecord(v))
            .map(([k, v]) => [k, textFields(v)]),
        )
      : {},
  };
}
export interface ImpactResult {
  untracked_outputs: number;
  shots: {
    id: string;
    scene_id: string;
    title: string;
    state: string;
    reason: string;
    evidence: string;
    look?: string;
  }[];
  outputs: {
    id: string;
    type: string;
    target_type: string;
    target_id: string;
    blob_hash?: string;
    created_at: string;
    adopted: boolean;
    direct: boolean;
  }[];
  clips: {
    id: string;
    timeline_id: string;
    timeline: string;
    ordinal: number;
  }[];
}

export function AssetVisualFields({
  type,
  value: original,
  onChange,
  disabled,
  images,
}: {
  type: string;
  images: RefImage[];
  value: VisualMetadata;
  onChange: (v: VisualMetadata) => void;
  disabled: boolean;
}) {
  const value = visualMetadata(original);
  const [look, setLook] = useState("");
  const [newLook, setNewLook] = useState("");
  const keys =
    type === "character"
      ? ["face", "wardrobe", "appearance"]
      : type === "location"
        ? ["layout", "light", "appearance"]
        : ["appearance"];
  const traits = (look ? value.looks?.[look] : value.visual_traits) || {};
  function update(field: string, text: string) {
    const next = { ...traits, [field]: text };
    onChange(
      look
        ? { ...original, looks: { ...value.looks, [look]: next } }
        : { ...original, visual_traits: next },
    );
  }
  return (
    <details className="rounded-lg border p-3">
      <summary>
        {type === "character" ? "别名与分期造型" : "别名与视觉设定"}
      </summary>
      <fieldset disabled={disabled} className="mt-3 space-y-3">
        <label className="block text-sm">
          已确认的别名
          <Input
            key={(value.aliases || []).join(",")}
            defaultValue={(value.aliases || []).join("、")}
            onBlur={(e) => {
              const aliases = e.target.value
                .split(/[,，、]/)
                .map((s) => s.trim())
                .filter(Boolean);
              if (aliases.join("、") !== value.aliases?.join("、"))
                onChange({ ...original, aliases });
            }}
            placeholder="多个称呼用顿号分隔"
          />
        </label>
        <SelectField
          label="适用造型"
          value={look}
          onChange={setLook}
          options={[
            { value: "", label: "通用设定" },
            ...Object.keys(value.looks || {}).map((n) => ({
              value: n,
              label: n,
            })),
          ]}
        />
        <div className="flex gap-2">
          <Input
            value={newLook}
            onChange={(e) => setNewLook(e.target.value)}
            aria-label="新造型名称"
            placeholder="例如，青年时期"
          />
          <Button
            size="sm"
            variant="outline"
            disabled={disabled || !newLook.trim()}
            onClick={() => {
              const n = newLook.trim();
              onChange({
                ...original,
                looks: { ...value.looks, [n]: value.looks?.[n] || {} },
              });
              setLook(n);
              setNewLook("");
            }}
          >
            添加造型
          </Button>
        </div>
        {look && (
          <SelectField
            label="此造型的参考图"
            value={value.look_images?.[look] || ""}
            onChange={(hash) =>
              onChange({
                ...original,
                look_images: { ...value.look_images, [look]: hash },
              })
            }
            options={[
              { value: "", label: "仅采用文字设定" },
              ...images.map((img, i) => ({
                value: img.blob_hash,
                label: img.note || "参考图 " + (i + 1),
              })),
            ]}
          />
        )}
        {keys.map((k) => (
          <label className="block text-sm" key={k}>
            {aspectLabels[k]}
            <Input
              value={traits[k] || ""}
              onChange={(e) => update(k, e.target.value)}
            />
          </label>
        ))}
      </fieldset>
    </details>
  );
}

export function ImpactDialog({
  base,
  result,
  saving,
  onSave,
  onClose,
}: {
  base: string;
  result: ImpactResult;
  saving: boolean;
  onSave?: () => void;
  onClose: () => void;
}) {
  return (
    <Modal open onClose={onClose} title="设定影响范围" width={640}>
      <div className="space-y-4">
        {!result.shots.length && !result.outputs.length && (
          <p>没有找到与这次改动相关的镜头或已记录的生成结果。</p>
        )}
        {(["affected", "possible", "pinned"] as const).map((state) => {
          const rows = result.shots.filter((s) => s.state === state);
          if (!rows.length) return null;
          const label = {
            affected: "使用这项设定",
            possible: "待确认是否入镜",
            pinned: "已锁定旧版",
          }[state];
          return (
            <section key={state}>
              <h3 className="font-medium">
                {label} · {rows.length}
              </h3>
              <ul className="mt-2 space-y-2">
                {rows.map((s) => (
                  <li key={s.id} className="text-sm">
                    <Link
                      to={`${base}/shots?shot=${s.id}&tab=gen`}
                      className="underline"
                    >
                      {s.title}
                    </Link>
                    <span className="ml-2 text-muted-foreground">
                      {[s.look, s.reason, s.evidence]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
        <AffectedMedia
          base={base}
          outputs={result.outputs}
          clips={result.clips}
          untracked={result.untracked_outputs}
        />
        {onSave && (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              保存后，已采用的画面与剪辑继续沿用。可以到分镜页选择需要重新生成的镜头。
            </p>
            <Button disabled={saving} onClick={onSave}>
              保存设定
            </Button>{" "}
            <Button variant="ghost" onClick={onClose}>
              返回编辑
            </Button>
          </div>
        )}
      </div>
    </Modal>
  );
}

export function AffectedMedia({
  base,
  outputs,
  clips,
  untracked,
}: {
  base: string;
  outputs: ImpactResult["outputs"];
  clips: ImpactResult["clips"];
  untracked: number;
}) {
  const project = base.split("/").filter(Boolean).pop()!;
  return (
    <>
      {untracked > 0 && (
        <p className="text-sm text-muted-foreground">
          另有 {untracked} 个历史结果缺少当时的输入记录，需人工核对。
        </p>
      )}
      {!!outputs.length && (
        <details>
          <summary>关联的画面与视频 · {outputs.length}</summary>
          <ul className="mt-2 space-y-2">
            {outputs.map((g) => (
              <li key={g.id} className="flex items-baseline gap-2 text-sm">
                {g.blob_hash ? (
                  <a
                    className="underline"
                    href={blobUrl(project, g.blob_hash)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {g.type === "image" ? "查看画面" : "查看视频"}
                  </a>
                ) : (
                  <span>{g.type === "image" ? "画面" : "视频"}</span>
                )}
                <time>{new Date(g.created_at).toLocaleString()}</time>
                <span>
                  {g.adopted
                    ? "已采用"
                    : g.direct
                      ? "直接使用设定"
                      : "使用了上游画面"}
                </span>
                {g.target_type === "shot" && (
                  <Link
                    className="underline"
                    to={base + "/shots?shot=" + g.target_id + "&tab=gen"}
                  >
                    所属镜头
                  </Link>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      {!!clips.length && (
        <ul>
          {clips.map((c) => (
            <li key={c.id}>
              <Link
                className="underline"
                to={base + "/cuts?timeline=" + c.timeline_id}
              >
                《{c.timeline}》· 剪辑片段 {c.ordinal}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
