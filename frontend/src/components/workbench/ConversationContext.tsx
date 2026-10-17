import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, FileText, Image, Sparkles } from "lucide-react";
import { api, blobUrl, type Asset, type Shot } from "@/lib/api";
import { useShotContext } from "@/lib/useShotContext";
import type { SkillCard, SkillUse } from "@/lib/skills";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { SearchField } from "@/components/ui/search-field";
import { SelectField } from "@/components/ui/select-field";
import { ShotMedia } from "@/components/ShotMedia";

export type ConversationTarget = { target_type: string; target_id: string };
export function useConversationObjects(projectId: string, enabled: boolean) {
  const context = useShotContext(projectId, enabled);
  const query = useQuery({
    queryKey: ["conversation-object-catalog", projectId],
    enabled,
    queryFn: async () => {
      const [shots, assets] = await Promise.all([
        api.get<Shot[]>(`/projects/${projectId}/shots`),
        api.get<Asset[]>(`/projects/${projectId}/assets`),
      ]);
      return { shots, assets };
    },
  });
  const items = [
    ...(context.data?.scripts || []).map((s) => ({
      id: s.id,
      type: "script",
      name: s.title || "未命名剧本",
      source: "剧本",
      description: `${s.scene_count ?? 0} 场戏 · ${s.block_count ?? 0} 段正文`,
      scriptId: s.id,
      shot: undefined as Shot | undefined,
      image: null as string | null,
    })),
    ...(query.data?.shots || []).map((s) => {
      const c = context.data?.scenes[s.scene_id];
      return {
        id: s.id,
        type: "shot",
        name: s.title || `镜头 ${s.ordinal}`,
        source: [
          c?.script.title || s.novel_title || "其他来源",
          c?.scene.title || s.scene_title || "未命名场景",
          s.chapter_title,
        ]
          .filter(Boolean)
          .join(" / "),
        description: s.description || "尚未填写画面描述",
        scriptId:
          c?.script.id || (s.novel_id ? `story:${s.novel_id}` : "unlinked"),
        shot: s,
        image: null as string | null,
      };
    }),
    ...(query.data?.assets || []).map((a) => ({
      id: a.id,
      type: "asset",
      name: a.name,
      source:
        (
          { character: "角色", scene: "场景", prop: "道具" } as Record<
            string,
            string
          >
        )[a.type] || "角色与设定",
      description: a.summary || "尚未填写设定描述",
      scriptId: "",
      shot: undefined as Shot | undefined,
      image: a.representative_blob_hash,
    })),
  ];
  return {
    items,
    scripts: context.data?.scripts || [],
    stories: [
      ...new Map(
        (query.data?.shots || [])
          .filter((s) => s.novel_id && !context.data?.scenes[s.scene_id])
          .map((s) => [
            s.novel_id!,
            { id: `story:${s.novel_id}`, title: s.novel_title || "未命名故事" },
          ]),
      ).values(),
    ],
    loading: query.isPending || context.isPending,
    error: query.isError || context.isError,
    retry: () => {
      void query.refetch();
      void context.refetch();
    },
  };
}
export function ConversationObjectDialog({
  projectId,
  catalog,
  value,
  onChange,
  onClose,
}: {
  projectId: string;
  catalog: ReturnType<typeof useConversationObjects>;
  value: ConversationTarget[];
  onChange: (value: ConversationTarget[]) => void;
  onClose: () => void;
}) {
  const [selection, setSelection] = useState(value);
  const [kind, setKind] = useState("script");
  const [query, setQuery] = useState("");
  const [script, setScript] = useState("");
  const [limit, setLimit] = useState(24);
  const matches = catalog.items.filter(
    (i) =>
      i.type === kind &&
      (!script || i.scriptId === script) &&
      `${i.name} ${i.source} ${i.description}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  return (
    <Modal
      open
      title="这次想一起修改什么？"
      width={920}
      onClose={onClose}
      bodyClassName="conversation-picker"
    >
      <p className="conversation-picker-intro">
        按内容找到剧本、镜头或设定，加入这轮对话。修改会先交给你审阅；只聊想法时无需选择。
      </p>
      <div
        className="conversation-picker-tabs"
        role="tablist"
        aria-label="内容类型"
      >
        {[
          ["script", "剧本"],
          ["shot", "镜头"],
          ["asset", "角色与设定"],
        ].map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={kind === id}
            onClick={() => {
              setKind(id);
              setScript("");
              setLimit(24);
            }}
          >
            {label}
            <small>{catalog.items.filter((i) => i.type === id).length}</small>
          </button>
        ))}
      </div>
      <div className="conversation-picker-search">
        <SearchField
          label="搜索创作内容"
          placeholder="搜索名称、画面描述、人物或场景"
          value={query}
          onValueChange={(v) => {
            setQuery(v);
            setLimit(24);
          }}
        />
        {kind === "shot" && (
          <SelectField
            label="剧本或故事来源"
            value={script}
            onChange={(v) => {
              setScript(v);
              setLimit(24);
            }}
            options={[
              { value: "", label: "全部剧本与来源" },
              ...catalog.scripts.map((s) => ({
                value: s.id,
                label: `剧本 · ${s.title}`,
              })),
              ...catalog.stories.map((s) => ({
                value: s.id,
                label: `故事 · ${s.title}`,
              })),
              { value: "unlinked", label: "其他来源" },
            ]}
          />
        )}
      </div>
      <div className="conversation-picker-results">
        {catalog.loading ? (
          <p role="status">正在读取创作内容…</p>
        ) : catalog.error ? (
          <p role="alert">
            内容读取失败。<button onClick={catalog.retry}>重新加载</button>
          </p>
        ) : matches.length ? (
          matches.slice(0, limit).map((i) => {
            const checked = selection.some(
              (t) => t.target_type === i.type && t.target_id === i.id,
            );
            return (
              <button
                key={`${i.type}:${i.id}`}
                className="conversation-object-row"
                aria-pressed={checked}
                disabled={!checked && selection.length >= 12}
                onClick={() =>
                  setSelection(
                    checked
                      ? selection.filter(
                          (t) =>
                            !(t.target_type === i.type && t.target_id === i.id),
                        )
                      : [
                          ...selection,
                          { target_type: i.type, target_id: i.id },
                        ],
                  )
                }
              >
                <span className="conversation-object-cover">
                  {i.shot ? (
                    <ShotMedia projectId={projectId} shot={i.shot} thumbnail />
                  ) : i.image ? (
                    <img
                      src={blobUrl(projectId, i.image)}
                      alt=""
                      loading="lazy"
                    />
                  ) : i.type === "script" ? (
                    <FileText size={23} />
                  ) : (
                    <Image size={23} />
                  )}
                </span>
                <span className="conversation-object-copy">
                  <small>{i.source}</small>
                  <strong>{i.name}</strong>
                  <span>{i.description}</span>
                </span>
                <span className="conversation-choice-check">
                  {checked && <Check size={14} />}
                </span>
              </button>
            );
          })
        ) : (
          <p className="conversation-picker-empty">
            {query || script
              ? "没有找到匹配内容，试试其他关键词或来源。"
              : "这里还没有内容，可以先回到对话讨论创作方向。"}
          </p>
        )}
        {matches.length > limit && (
          <Button variant="ghost" onClick={() => setLimit(limit + 24)}>
            查看更多（{limit} / {matches.length}）
          </Button>
        )}
      </div>
      <footer className="conversation-picker-footer">
        <span>
          已选 {selection.length} / 12 项
          {selection.length > 0 && (
            <button onClick={() => setSelection([])}>清空</button>
          )}
        </span>
        <div>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button
            onClick={() => {
              onChange(selection);
              onClose();
            }}
          >
            确认选择
          </Button>
        </div>
      </footer>
    </Modal>
  );
}

export function ConversationSkillDialog({
  projectId,
  value,
  onChange,
  onClose,
}: {
  projectId: string;
  value: SkillUse[];
  onChange: (value: SkillUse[]) => void;
  onClose: () => void;
}) {
  const [selection, setSelection] = useState(value);
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const list = useQuery({
    queryKey: ["skills", projectId, "conversation-picker", query, offset],
    queryFn: () =>
      api.get<{ items: SkillCard[]; total: number }>(
        `/projects/${projectId}/skills/catalog?q=${encodeURIComponent(query)}&offset=${offset}`,
      ),
  });
  return (
    <Modal
      open
      title="为这次创作添加技能"
      width={720}
      onClose={onClose}
      bodyClassName="conversation-picker"
    >
      <p className="conversation-picker-intro">
        选用已有的创作方法，帮助助理处理分镜、人物或叙事。不添加也可以直接开始对话。
      </p>
      <div className="conversation-picker-search">
        <SearchField
          label="搜索技能"
          placeholder="搜索技能名称或用途"
          value={query}
          onValueChange={(v) => {
            setQuery(v);
            setOffset(0);
          }}
        />
      </div>
      <div className="conversation-picker-results">
        {list.isPending && <p role="status">正在读取技能…</p>}
        {list.isError && (
          <p role="alert">
            技能读取失败。
            <button onClick={() => list.refetch()}>重新加载</button>
          </p>
        )}
        {list.data?.items.map((s) => {
          const checked = selection.some((v) => v.id === s.id);
          return (
            <button
              key={s.id}
              className="conversation-object-row"
              aria-pressed={checked}
              disabled={
                !s.id || !s.revision || (!checked && selection.length >= 3)
              }
              onClick={() =>
                setSelection(
                  checked
                    ? selection.filter((v) => v.id !== s.id)
                    : [
                        ...selection,
                        { id: s.id!, revision: s.revision!, name: s.name },
                      ],
                )
              }
            >
              <span className="conversation-object-cover skill">
                <Sparkles size={23} />
              </span>
              <span className="conversation-object-copy">
                <strong>{s.name}</strong>
                <span>{s.description || "暂无使用说明"}</span>
              </span>
              <span className="conversation-choice-check">
                {checked && <Check size={14} />}
              </span>
            </button>
          );
        })}
        {list.data?.total === 0 && (
          <p className="conversation-picker-empty">
            {query
              ? "没有找到匹配技能。"
              : "还没有创作技能。你可以在「创作工具」中安装或创建，之后再来选用。"}
          </p>
        )}
        {!!list.data && list.data.total > 24 && (
          <div className="conversation-picker-pagination">
            <Button
              variant="ghost"
              disabled={!offset}
              onClick={() => setOffset(offset - 24)}
            >
              上一页
            </Button>
            <Button
              variant="ghost"
              disabled={offset + 24 >= list.data.total}
              onClick={() => setOffset(offset + 24)}
            >
              下一页
            </Button>
          </div>
        )}
      </div>
      <footer className="conversation-picker-footer">
        <span>已选 {selection.length} / 3 项</span>
        <div>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button
            onClick={() => {
              onChange(selection);
              onClose();
            }}
          >
            确认选择
          </Button>
        </div>
      </footer>
    </Modal>
  );
}
