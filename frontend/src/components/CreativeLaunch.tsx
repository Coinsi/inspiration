import { ArrowUp, Clapperboard, FolderOpen, Sparkles } from "lucide-react";
import { lazy, Suspense, useState } from "react";
import { Modal } from "@/components/ui/modal";
const EmbeddedAgent = lazy(() =>
  import("@/pages/Agent").then((module) => ({
    default: module.AgentWorkspace,
  })),
);
import { type Project } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePersistentState } from "@/lib/usePersistentState";

export function CreativeLaunch({
  projectId,
  projects,
  onCreate,
}: {
  projectId?: string;
  projects?: Project[];
  onCreate?: () => void;
}) {
  const { lang } = useI18n(),
    zh = lang === "zh";
  const [goal, setGoal] = usePersistentState(
    `creative.goal.${projectId || "home"}`,
    "",
  );
  const [chosen, setChosen] = usePersistentState("creative.project", "");
  const [session, setSession] = useState<{
    project: string;
    goal: string;
  } | null>(null);
  const [open, setOpen] = useState(false);
  const target =
    projectId ||
    (projects?.some((p) => p.id === chosen) ? chosen : projects?.[0]?.id);
  const prompts = zh
    ? [
        "帮我梳理项目，规划下一步创作",
        "为雨夜重逢的故事构思镜头",
        "从素材库里寻找适合开场的片段",
      ]
    : [
        "Review this project and plan the next steps",
        "Plan shots for a reunion on a rainy night",
        "Find an opening scene in my library",
      ];
  const launch = () => {
    if (!target || !goal.trim()) return;
    setSession((current) =>
      current?.project === target && current.goal === goal.trim()
        ? current
        : { project: target, goal: goal.trim() },
    );
    setOpen(true);
  };
  return (
    <div className="creative-launch">
      {session && (
        <Modal
          keepMounted
          open={open}
          onClose={() => setOpen(false)}
          title={zh ? "创作助理" : "Creative assistant"}
          width={1100}
        >
          <Suspense fallback={<p role="status">正在准备创作空间…</p>}>
            <EmbeddedAgent
              key={`${session.project}:${session.goal}`}
              projectId={session.project}
              initialGoal={session.goal}
              embedded
            />
          </Suspense>
        </Modal>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          launch();
        }}
        className="creative-composer"
      >
        <label className="sr-only" htmlFor="creative-goal">
          {zh ? "创作想法" : "Creative idea"}
        </label>
        <textarea
          id="creative-goal"
          value={goal}
          maxLength={6000}
          onChange={(e) => setGoal(e.target.value)}
          placeholder={
            zh
              ? "描述你脑海中的镜头、画面或故事…"
              : "Describe a scene, a frame, or a story you have in mind…"
          }
        />
        <div className="creative-composer-tools">
          <span className="creative-mode">
            <Sparkles size={15} />
            {zh ? "创作助理" : "Creative assistant"}
          </span>
          {!projectId && !!projects?.length && (
            <label className="creative-project">
              <FolderOpen size={14} />
              <select
                aria-label={zh ? "创作所在项目" : "Creative project"}
                value={target}
                onChange={(e) => setChosen(e.target.value)}
              >
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {!projectId && projects?.length === 0 && (
            <button
              type="button"
              onClick={onCreate}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              {zh ? "先创建一个项目" : "Create your first project"}
            </button>
          )}
          <button
            type="submit"
            className="creative-send"
            disabled={!target || !goal.trim()}
            aria-label={zh ? "带入创作助理" : "Open in creative assistant"}
          >
            <ArrowUp size={21} />
          </button>
        </div>
      </form>
      <div className="creative-suggestions">
        {prompts.map((prompt, i) => (
          <button
            key={prompt}
            type="button"
            onClick={() => {
              setGoal(prompt);
              document.getElementById("creative-goal")?.focus();
            }}
          >
            {i === 0 ? <Sparkles size={13} /> : <Clapperboard size={13} />}
            {prompt}
          </button>
        ))}
      </div>
      <p className="creative-note">
        {zh
          ? "在当前工作台选择创作对象并开始，修改会先交给你审阅。"
          : "Review your idea in the assistant before starting."}
      </p>
    </div>
  );
}
