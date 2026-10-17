import { useState } from "react";
import { useShotContext } from "@/lib/useShotContext";
import { Check, ChevronRight, Film } from "lucide-react";
import { type Shot } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { SearchField } from "@/components/ui/search-field";
import { SelectField } from "@/components/ui/select-field";
import { ShotMedia } from "@/components/ShotMedia";
import "./material-shot-picker.css";

export function MaterialShotPicker({
  projectId,
  shots,
  selected,
  loading,
  error,
  onRetry,
  onSelect,
}: {
  projectId: string;
  shots: Shot[];
  selected: string;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  onSelect: (id: string) => void;
}) {
  const zh = useI18n().lang === "zh";
  const [open, setOpen] = useState(false);
  const [candidate, setCandidate] = useState(selected);
  const [query, setQuery] = useState("");
  const [scriptId, setScriptId] = useState("");
  const [limit, setLimit] = useState(30);
  const context = useShotContext(projectId, open || Boolean(selected));
  const sceneName = (s: Shot) =>
    s.scene_title ||
    context.data?.scenes[s.scene_id]?.scene.title ||
    s.scene_code ||
    (zh ? "未命名场景" : "Untitled scene");
  const scriptName = (s: Shot) =>
    context.data?.scenes[s.scene_id]?.script.title ||
    (context.isPending
      ? zh
        ? "正在读取所属剧本…"
        : "Loading script…"
      : zh
        ? "所属剧本当前不可用"
        : "Source script unavailable");
  const sourceName = (s: Shot) =>
    context.data?.scenes[s.scene_id]?.script.title ||
    (s.novel_title
      ? `${zh ? "故事" : "Story"} · ${s.novel_title}`
      : scriptName(s));
  const shotName = (s: Shot) =>
    s.title || (zh ? `镜头 ${s.ordinal}` : `Shot ${s.ordinal}`);
  const matches = shots.filter(
    (s) =>
      (!scriptId ||
        (scriptId === "unlinked"
          ? !context.data?.scenes[s.scene_id]
          : context.data?.scenes[s.scene_id]?.script.id === scriptId)) &&
      [
        shotName(s),
        s.description,
        s.code,
        sceneName(s),
        scriptName(s),
        s.chapter_title,
        s.novel_title,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  const chosen = shots.find((s) => s.id === candidate);
  const saved = shots.find((s) => s.id === selected);
  return (
    <>
      <button
        className="material-shot-trigger"
        onClick={() => {
          setCandidate(selected);
          setOpen(true);
        }}
      >
        <Film size={17} />
        <span>
          <strong>
            {saved
              ? shotName(saved)
              : zh
                ? "选择用在哪个镜头"
                : "Choose a shot"}
          </strong>
          <small>
            {saved
              ? `${sourceName(saved)} / ${sceneName(saved)}`
              : zh
                ? "按剧本、场景和画面内容查找"
                : "Find by script, scene or description"}
          </small>
        </span>
        <ChevronRight size={15} />
      </button>
      {saved && (
        <p className="material-shot-selection">
          {saved.description ||
            (zh
              ? "这个镜头还没有内容描述"
              : "This shot has no description yet")}
        </p>
      )}
      <Modal
        open={open}
        title={
          zh ? "把素材用到哪个镜头？" : "Which shot should use this material?"
        }
        width={1040}
        onClose={() => setOpen(false)}
        bodyClassName="material-shot-dialog"
      >
        <div className="material-shot-toolbar">
          <SearchField
            label={zh ? "搜索镜头内容" : "Search shots"}
            placeholder={
              zh
                ? "搜索画面内容、人物、场景或章节"
                : "Search descriptions, people, scenes or chapters"
            }
            value={query}
            onValueChange={(value) => {
              setQuery(value);
              setLimit(30);
            }}
          />
          <SelectField
            label={zh ? "按剧本筛选" : "Filter by script"}
            value={scriptId}
            disabled={!context.data}
            options={[
              { value: "", label: zh ? "全部剧本" : "All scripts" },
              {
                value: "unlinked",
                label: zh ? "其他来源的镜头" : "Shots from other sources",
              },
              ...(context.data?.scripts || []).map((s) => ({
                value: s.id,
                label: s.title,
              })),
            ]}
            onChange={(value) => {
              setScriptId(value);
              setLimit(30);
            }}
          />
        </div>
        {context.isError && (
          <div className="material-shot-context-error" role="alert">
            {zh
              ? "剧本信息读取失败，仍可按场景与镜头内容选择。"
              : "Script context unavailable; scene and shot details remain available."}
            <button onClick={() => void context.refetch()}>
              {zh ? "重试" : "Retry"}
            </button>
          </div>
        )}
        <div className="material-shot-layout">
          <div className="material-shot-results">
            <p className="material-shot-count">
              {loading
                ? zh
                  ? "正在读取镜头…"
                  : "Loading shots…"
                : zh
                  ? `${matches.length} 个镜头`
                  : `${matches.length} shots`}
            </p>
            {error ? (
              <Button variant="ghost" onClick={onRetry}>
                {zh ? "读取失败，重试" : "Retry shots"}
              </Button>
            ) : (
              <div
                role="group"
                aria-label={zh ? "可选镜头" : "Available shots"}
              >
                {matches.slice(0, limit).map((s) => (
                  <button
                    key={s.id}
                    className="material-shot-option"
                    aria-pressed={candidate === s.id}
                    aria-label={`${sourceName(s)} / ${sceneName(s)} / ${shotName(s)} · ${s.code}`}
                    onClick={() => setCandidate(s.id)}
                  >
                    <span className="material-shot-thumb">
                      {s.selected_generation_id ? (
                        <ShotMedia projectId={projectId} shot={s} thumbnail />
                      ) : (
                        <>
                          <Film size={20} />
                          <small>{zh ? "暂无画面" : "No image"}</small>
                        </>
                      )}
                    </span>
                    <span className="material-shot-option-text">
                      <small>
                        {sourceName(s)} / {sceneName(s)}
                      </small>
                      <strong>{shotName(s)}</strong>
                      <span>
                        {s.description ||
                          (zh
                            ? "暂无镜头描述，选择后核对所属场景"
                            : "No description. Check the scene before confirming.")}
                      </span>
                    </span>
                    {candidate === s.id && (
                      <Check size={16} className="material-shot-check" />
                    )}
                  </button>
                ))}
                {!loading && !matches.length && (
                  <p className="material-shot-empty">
                    {shots.length
                      ? zh
                        ? "没有找到匹配镜头，试试其他描述。"
                        : "No matching shots. Try another description."
                      : zh
                        ? "还没有镜头，请先在镜头创作中创建。"
                        : "Create a shot in Storyboard first."}
                  </p>
                )}
                {matches.length > limit && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setLimit(limit + 30)}
                  >
                    {zh ? "加载更多镜头" : "Load more shots"}
                  </Button>
                )}
              </div>
            )}
          </div>
          <aside
            className="material-shot-inspect"
            aria-label={zh ? "镜头详情" : "Shot details"}
          >
            {chosen ? (
              <>
                <div
                  className={`material-shot-cover ${chosen.selected_generation_id ? "" : "is-empty"}`}
                >
                  <ShotMedia projectId={projectId} shot={chosen} thumbnail />
                </div>
                {!chosen.selected_generation_id && (
                  <p className="material-shot-muted">
                    {zh
                      ? "尚未选定画面，请根据下方内容确认镜头。"
                      : "No selected image. Check the description below."}
                  </p>
                )}
                <div className="material-shot-inspect-title">
                  <h3>{shotName(chosen)}</h3>
                  <span>{chosen.code}</span>
                </div>
                <dl>
                  <div>
                    <dt>{zh ? "剧本" : "Script"}</dt>
                    <dd>{scriptName(chosen)}</dd>
                  </div>
                  <div>
                    <dt>{zh ? "场景" : "Scene"}</dt>
                    <dd>{sceneName(chosen)}</dd>
                  </div>
                  {chosen.chapter_title && (
                    <div>
                      <dt>{zh ? "章节" : "Chapter"}</dt>
                      <dd>
                        {[chosen.novel_title, chosen.chapter_title]
                          .filter(Boolean)
                          .join(" / ")}
                      </dd>
                    </div>
                  )}
                </dl>
                <h4>{zh ? "镜头内容" : "Shot description"}</h4>
                <p className="material-shot-description">
                  {chosen.description || (zh ? "暂无描述" : "No description")}
                </p>
                {context.data?.scenes[chosen.scene_id]?.scene.summary && (
                  <>
                    <h4>{zh ? "场景背景" : "Scene summary"}</h4>
                    <p className="material-shot-description">
                      {context.data.scenes[chosen.scene_id].scene.summary}
                    </p>
                  </>
                )}
              </>
            ) : (
              <div className="material-shot-empty">
                <Film size={28} />
                <p>{zh ? "先选择一个镜头" : "Select a shot"}</p>
                <span>
                  {zh
                    ? "在这里核对画面、所属剧本和内容，再确认使用。"
                    : "Check its image, script and description before confirming."}
                </span>
              </div>
            )}
          </aside>
        </div>
        <footer className="material-shot-footer">
          <span>
            {zh
              ? "确认选择后，再保存素材引用。"
              : "Confirm a shot, then save the material reference."}
          </span>
          <Button
            disabled={!chosen || loading || error}
            onClick={() => {
              if (chosen) {
                onSelect(chosen.id);
                setOpen(false);
              }
            }}
          >
            {zh ? "使用这个镜头" : "Use this shot"}
          </Button>
        </footer>
      </Modal>
    </>
  );
}
