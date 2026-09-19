import CreativePreferences from "@/components/CreativePreferences";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  Cable,
  ChevronRight,
  FolderOpen,
  Server,
  Wallet,
  Settings2,
  Users,
} from "lucide-react";
import { ModelChannels } from "@/components/ModelChannels";
import { ProjectDialog } from "@/components/ProjectDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, blobUrl, type Project, type Quota } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import "./settings-workspace.css";

const sections = [
  {
    id: "creative",
    label: "创作偏好",
    description: "默认项与提示词",
    icon: Settings2,
    group: "当前项目",
  },
  {
    id: "project",
    label: "项目资料",
    description: "封面与成员",
    icon: FolderOpen,
    group: "当前项目",
  },
  {
    id: "channels",
    label: "渠道与模型",
    description: "连接创作服务",
    icon: Server,
    group: "当前项目",
  },
  {
    id: "quota",
    label: "项目配额",
    description: "用量与内部额度",
    icon: Wallet,
    group: "当前项目",
  },
  {
    id: "connections",
    label: "外部 AI 连接",
    description: "连接桌面助手",
    icon: Cable,
    group: "连接",
  },
] as const;
type Section = (typeof sections)[number]["id"];
export default function Settings() {
  const { projectId } = useParams();
  const { me } = useAuth();
  return (
    <SettingsContent
      key={`${projectId}:${me?.user.id}`}
      projectId={projectId!}
    />
  );
}
function SettingsContent({ projectId }: { projectId: string }) {
  const [params, setParams] = useSearchParams();
  const requested = params.get("section");
  useEffect(() => {
    if (requested !== "appearance") return;
    const next = new URLSearchParams(params);
    next.set("section", "project");
    next.set("account", "preferences");
    setParams(next, { replace: true });
  }, [requested, params, setParams]);
  const active: Section =
    sections.find((s) => s.id === requested)?.id ?? "channels";
  const [visited, setVisited] = useState<Section[]>([active]);
  const navigation = useRef<HTMLElement>(null);
  useEffect(() => {
    const nav = navigation.current;
    const selected = nav?.querySelector<HTMLElement>('[aria-current="page"]');
    if (nav && selected && nav.scrollWidth > nav.clientWidth) {
      nav.scrollLeft +=
        selected.getBoundingClientRect().left -
        nav.getBoundingClientRect().left -
        (nav.clientWidth - selected.clientWidth) / 2;
    }
  }, [active]);

  useEffect(() => {
    setVisited((old) => (old.includes(active) ? old : [...old, active]));
  }, [active]);
  const role = useAuth().me?.memberships.find(
    (m) => m.project_id === projectId,
  )?.role;
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.get<Project>(`/projects/${projectId}`),
  });
  const entry = sections.find((s) => s.id === active)!;
  return (
    <div className="settings-workspace">
      <header className="settings-hero">
        <div className="settings-heading-icon">
          <Settings2 size={22} />
        </div>
        <div>
          <h1>项目设置</h1>
          <p>管理当前项目的资料、模型与创作默认项。</p>
        </div>
        <span className="settings-project-badge">
          {project.data?.name || "当前项目"}
        </span>
      </header>
      <div className="settings-frame">
        <nav
          ref={navigation}
          className="settings-sidebar"
          aria-label="设置分类"
        >
          {sections.map((s, i) => (
            <div key={s.id}>
              {(i === 0 || sections[i - 1].group !== s.group) && (
                <p className="settings-group">{s.group}</p>
              )}
              <button
                aria-current={active === s.id ? "page" : undefined}
                onClick={() => {
                  const next = new URLSearchParams(params);
                  next.set("section", s.id);
                  setParams(next);
                }}
              >
                <s.icon size={18} />
                <span>
                  <strong>{s.label}</strong>
                  <small>{s.description}</small>
                </span>
                <ChevronRight size={13} />
              </button>
            </div>
          ))}
        </nav>
        <div className="settings-content" aria-label={`${entry.label}设置内容`}>
          {(visited.includes("creative") || active === "creative") && (
            <section hidden={active !== "creative"}>
              <CreativePreferences
                projectId={projectId}
                admin={role === "admin"}
              />
            </section>
          )}
          {(visited.includes("channels") || active === "channels") && (
            <section hidden={active !== "channels"}>
              {role === "admin" ? (
                <ModelChannels projectId={projectId} />
              ) : (
                <div className="settings-pane">
                  <h2>渠道与模型</h2>
                  <p>
                    渠道、密钥和模型由项目管理员管理。已启用的模型可在创作页面选择。
                  </p>
                  <Link
                    className="studio-link"
                    to={`/projects/${projectId}/members`}
                  >
                    查看项目成员
                  </Link>
                </div>
              )}
            </section>
          )}
          {(visited.includes("project") || active === "project") && (
            <section hidden={active !== "project"}>
              <ProjectPane projectId={projectId} admin={role === "admin"} />
            </section>
          )}
          {(visited.includes("quota") || active === "quota") && (
            <section hidden={active !== "quota"}>
              <QuotaPane projectId={projectId} admin={role === "admin"} />
            </section>
          )}
          {(visited.includes("connections") || active === "connections") && (
            <section hidden={active !== "connections"}>
              <ConnectionPane projectId={projectId} />
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
function ProjectPane({
  projectId,
  admin,
}: {
  projectId: string;
  admin: boolean;
}) {
  const qc = useQueryClient();
  const [cover, setCover] = useState(false);
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.get<Project>(`/projects/${projectId}`),
  });
  return (
    <div className="settings-pane">
      <div className="settings-pane-heading">
        <span>当前项目</span>
        <h2>项目资料</h2>
        <p>封面和项目资料供项目成员共同使用。</p>
      </div>
      {project.isPending ? (
        <p role="status">正在读取项目资料…</p>
      ) : project.error ? (
        <div role="alert">
          <p>{project.error.message}</p>
          <Button onClick={() => void project.refetch()}>重新读取项目</Button>
        </div>
      ) : (
        <>
          <div className="settings-project-card">
            <div className="settings-project-cover">
              {project.data.cover_blob_hash ? (
                <img
                  src={blobUrl(projectId, project.data.cover_blob_hash)}
                  alt="项目封面"
                />
              ) : (
                <FolderOpen size={38} />
              )}
            </div>
            <div>
              <h3>{project.data.name}</h3>
              <p>{project.data.description || "还没有填写项目简介"}</p>
              {admin && (
                <Button variant="outline" onClick={() => setCover(true)}>
                  管理项目封面
                </Button>
              )}
            </div>
          </div>
          <div className="settings-row">
            <div>
              <h3>项目成员</h3>
              <p>查看成员与角色，按项目权限协作。</p>
            </div>
            <Link className="studio-link" to={`/projects/${projectId}/members`}>
              <Users size={16} />
              查看成员
              <ChevronRight size={14} />
            </Link>
          </div>
          <div className="settings-row">
            <div>
              <h3>项目内容</h3>
              <p>回到工作台继续创作；所有项目与回收站在项目首页管理。</p>
            </div>
            <Link className="studio-link" to="/projects">
              所有项目
              <ChevronRight size={14} />
            </Link>
          </div>
          {cover && (
            <ProjectDialog
              project={project.data}
              onClose={() => setCover(false)}
              onSaved={(p) => {
                qc.setQueryData(["project", projectId], p);
                void qc.invalidateQueries({ queryKey: ["projects"] });
              }}
            />
          )}
        </>
      )}
    </div>
  );
}
function QuotaPane({
  projectId,
  admin,
}: {
  projectId: string;
  admin: boolean;
}) {
  const qc = useQueryClient();
  const base = `/projects/${projectId}`;
  const query = useQuery({
    queryKey: ["quota", projectId],
    queryFn: () => api.get<Quota | null>(`${base}/quota`),
  });
  const [limit, setLimit] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  const pending = useRef(false);
  useEffect(() => {
    if (!dirty && query.isSuccess)
      setLimit(query.data ? String(query.data.limit_cost) : "");
  }, [query.data, query.isSuccess, dirty]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const save = useMutation({
    mutationFn: (value: number) =>
      api.put<Quota>(`${base}/quota`, { scope: "project", limit_cost: value }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["quota", projectId] });
      setDirty(false);
      setSaved(true);
    },
  });
  const valid =
    limit.trim() !== "" && Number.isFinite(Number(limit)) && Number(limit) >= 0;
  return (
    <div className="settings-pane">
      <div className="settings-pane-heading">
        <span>当前项目</span>
        <h2>项目配额</h2>
        <p>为项目设置内部额度，了解已记录的创作用量。</p>
      </div>
      {query.isPending ? (
        <p role="status">正在读取项目配额…</p>
      ) : query.error ? (
        <div role="alert">
          <p>{query.error.message}</p>
          <Button onClick={() => void query.refetch()}>重新读取配额</Button>
        </div>
      ) : (
        <>
          <div className="settings-quota-summary">
            <div>
              <span>已记录用量</span>
              <strong>{query.data?.used_cost ?? "—"}</strong>
            </div>
            <div>
              <span>当前额度</span>
              <strong>{query.data?.limit_cost ?? "未设置"}</strong>
            </div>
          </div>
          <p className="settings-note">
            项目内部额度不代表上游账户余额；未配置模型计价时，以上游实际账单为准。
          </p>
          {admin ? (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                if (!valid || pending.current) return;
                pending.current = true;
                try {
                  await save.mutateAsync(Number(limit));
                } catch {
                  /* Keep the input for retry. */
                } finally {
                  pending.current = false;
                }
              }}
            >
              <div className="settings-row">
                <label htmlFor="project-quota">
                  <h3>项目额度</h3>
                  <p>修改后影响当前项目的新任务。</p>
                </label>
                <Input
                  id="project-quota"
                  aria-label="项目配额"
                  type="number"
                  min={0}
                  step="0.01"
                  required
                  value={limit}
                  disabled={save.isPending}
                  onChange={(e) => {
                    setLimit(e.target.value);
                    setDirty(true);
                    setSaved(false);
                  }}
                  className="max-w-48"
                />
              </div>
              <div className="settings-save-row">
                <span>{dirty ? "有未保存修改" : ""}</span>
                <Button disabled={!valid || save.isPending || !dirty}>
                  {save.isPending ? "保存中…" : "保存配额"}
                </Button>
              </div>
            </form>
          ) : (
            <p className="settings-note">只有项目管理员可以修改额度。</p>
          )}
        </>
      )}
      {save.error && (
        <p role="alert" className="text-danger">
          {save.error.message}
        </p>
      )}
      {saved && (
        <p role="status" className="text-success">
          配额已保存
        </p>
      )}
    </div>
  );
}
function ConnectionPane({ projectId }: { projectId: string }) {
  const [copied, setCopied] = useState(false),
    [error, setError] = useState("");
  const config = JSON.stringify(
    {
      mcpServers: {
        inspiration: {
          command: "/absolute/path/to/python",
          args: ["/absolute/path/to/services/mcp-gateway/server.py"],
          env: {
            INSPIRATION_API_URL: `${window.location.origin}/api/v1`,
            INSPIRATION_PROJECT_ID: projectId,
            INSPIRATION_TOKEN: "<在客户端凭据配置中填写访问令牌>",
          },
        },
      },
    },
    null,
    2,
  );
  return (
    <div className="settings-pane">
      <div className="settings-pane-heading">
        <span>当前项目 · 桌面助手</span>
        <h2>外部 AI 连接</h2>
        <p>让支持 MCP 的外部助手读取项目，并提交可审阅的创作修改。</p>
      </div>
      <div className="settings-connection-card">
        <Cable size={24} />
        <div>
          <h3>MCP 连接程序</h3>
          <p>
            在运行外部助手的设备上安装连接程序。网页里的创作助理直接连接服务器，无需这一步。
          </p>
          <span className="settings-scope">stdio · 手动配置</span>
        </div>
      </div>
      <ol className="settings-steps">
        <li>
          在助手设备准备 Python 3.12 环境，安装项目
          services/mcp-gateway/requirements.txt 中的依赖。
        </li>
        <li>
          将下面示例的 Python 与 server.py
          路径替换为该设备的实际绝对路径，并填写你自己的访问令牌。
        </li>
        <li>在助手中启用连接。修改与生成提议会进入创作助理，审阅后执行。</li>
      </ol>
      <details className="settings-config">
        <summary>查看连接配置示例</summary>
        <pre>{config}</pre>
        <Button
          variant="outline"
          onClick={() => {
            setCopied(false);
            setError("");
            void navigator.clipboard
              .writeText(config)
              .then(() => setCopied(true))
              .catch(() => setError("未能复制，请选择示例文本手动复制。"));
          }}
        >
          复制配置示例
        </Button>
        {copied && <p role="status">已复制示例，请替换路径和令牌</p>}
        {error && <p role="alert">{error}</p>}
      </details>
      <p className="settings-note">
        连接地址需要从助手设备可访问；不同设备不能使用本机的 127.0.0.1
        地址。远程服务使用 HTTPS。示例不包含你的密钥或登录令牌。
      </p>
      <Link className="studio-link" to={`/projects/${projectId}/agent`}>
        前往创作助理查看提议
        <ChevronRight size={14} />
      </Link>
    </div>
  );
}
