import { ComposerPrimitive, ThreadPrimitive, MessagePrimitive } from "@assistant-ui/react";
import { AssistantConversation } from "./AssistantConversation";
import { GenerativeMessage, type UIBlock } from "./GenerativeMessage";
import { SelectField } from "@/components/ui/select-field";
import {
  ConversationMediaControls,
  ConversationMediaResults,
  useConversationMedia,
  type CreationMode,
  type MediaOutput,
} from "./ConversationMedia";
import type { GeneratedMaterial } from "@/pages/GeneratedLibrary";
import { SearchField } from "@/components/ui/search-field";
import {
  lazy,
  Suspense,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useContext,
} from "react";
import { createPortal } from "react-dom";
import { ConversationNavigationHost } from "@/lib/conversation-navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArrowUp,
  Check,
  FolderOpen,
  Loader2,
  PanelLeft,
  Clapperboard,
  Type,
  Image,
  Video,
  Paperclip,
  Sparkles,
  ChevronDown,
  Plus,
  Square,
  X,
} from "lucide-react";
import {
  Link,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import { ConversationText } from "./ConversationText";
import { api, type ProviderConfig } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { usePersistentState } from "@/lib/usePersistentState";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { WorkPicker, type Work } from "@/components/WorkPicker";
import {
  ConversationObjectDialog,
  ConversationSkillDialog,
  useConversationObjects,
} from "./ConversationContext";
import type { SkillUse } from "@/lib/skills";
import { ComposerModelPicker } from "./ComposerModelPicker";
import { ConversationTaskState } from "./ConversationTaskState";
import { ConversationListItem } from "./ConversationListItem";
import "./composer.css";
import "./conversations.css";
import "./conversation-layout.css";

const TaskInspector = lazy(() =>
  import("@/pages/Agent").then((m) => ({ default: m.AgentWorkspace })),
);
type Target = { target_type: string; target_id: string };
type Message = {
  id: string;
  ordinal: number;
  role: "user" | "assistant";
  content: string;
  status: string;
  error?: string;
  task_id?: string;
  execution_kind?: string;
  created_at: string;
  context: {
    ui_blocks?: UIBlock[];
    mode?: string;
    scope?: Target[];
    skills?: SkillUse[];
    outputs?: MediaOutput[];
    media?: { request_type: string };
  };
  progress: { id: string; tool: string; message: string; status: string }[];
};
type Conversation = {
  work_id: string | null;
  id: string;
  title: string;
  archived: boolean;
  updated_at: string;
};
type RetryInput = {
  content: string;
  model_provider: string | null;
  scope: Target[];
  skills: SkillUse[];
  media: {
    provider: string;
    request_type: "image" | "video";
    provider_params: Record<string, string | number>;
    references: {
      generation_id: string;
      role: "reference" | "first_frame" | "last_frame";
    }[];
  } | null;
};
type Detail = Conversation & { messages: Message[]; has_older: boolean };
const active = new Set(["queued", "running", "needs_review", "paused"]);
const statuses: Record<string, string> = {
  queued: "正在准备",
  running: "正在处理",
  needs_review: "等待你审阅",
  paused: "已暂停",
  completed: "已完成",
  failed: "执行失败",
  cancelled: "已停止",
};

export function ConversationWorkspace({ projectId }: { projectId: string }) {
  const navigationHost = useContext(ConversationNavigationHost);
  const { me } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const root = `/projects/${projectId}`;
  const base = `${root}/conversations`;
  const [params, setParams] = useSearchParams();
  const selected = params.get("conversation") || "";
  const [archived, setArchived] = useState(false);
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [inspect, setInspect] = useState<string | null>(null);
  const [older, setOlder] = useState<Message[]>([]);
  const [hasOlder, setHasOlder] = useState(false);
  const scopeKey = `conversation.${me?.user.id}.${projectId}.${selected || "new"}`;
  const [draft, setDraft] = usePersistentState(`${scopeKey}.draft`, "");
  const [mode, setMode] = usePersistentState<CreationMode>(
    `${scopeKey}.mode`,
    params.get("view") === "generate" ? "image" : "text",
  );
  const [draftWork, setDraftWork] = usePersistentState(
    `${scopeKey}.work`,
    params.get("work") || "",
  );
  const [provider, setProvider] = usePersistentState(
    `${scopeKey}.provider`,
    "",
  );
  const [targets, setTargets] = usePersistentState<Target[]>(
    `${scopeKey}.targets`,
    [],
  );
  const [skills, setSkills] = usePersistentState<SkillUse[]>(
    `${scopeKey}.skills`,
    [],
  );
  const [contextOpen, setContextOpen] = useState(false);
  const [skillOpen, setSkillOpen] = useState(false);
  const [workOpen, setWorkOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [pending, setPending] = usePersistentState<{
    fingerprint: string;
    key: string;
  } | null>(`${scopeKey}.pending`, null);
  const [newId, setNewId] = usePersistentState(
    `conversation.${me?.user.id}.${projectId}.newId`,
    crypto.randomUUID(),
  );
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const messageList = useRef<HTMLDivElement>(null);
  const restoredPosition = useRef<string | null>(null);
  const skipInitialAutoScroll = useRef(false);
  const returnPosition = useMemo(() => {
    if (params.get("message"))
      return {
        id: params.get("message")!,
        ordinal: Number(params.get("ordinal")),
        offset: 16,
      };
    try {
      return JSON.parse(
        sessionStorage.getItem(`${scopeKey}.position`) || "null",
      ) as { id: string; ordinal: number; offset: number } | null;
    } catch {
      return null;
    }
  }, [selected, location.key, scopeKey]);
  const returnLink = (message?: Message) => {
    const next = new URLSearchParams(params);
    if (message) {
      next.set("message", message.id);
      next.set("ordinal", String(message.ordinal));
    } else {
      next.delete("message");
      next.delete("ordinal");
    }
    return `${location.pathname}?${next}`;
  };
  const [atBottom, setAtBottom] = useState(true);
  const list = useQuery({
    queryKey: ["conversations", projectId, archived, query, offset],
    queryFn: () =>
      api.get<Conversation[]>(
        `${base}?archived=${archived}&offset=${offset}&q=${encodeURIComponent(query)}`,
      ),
  });
  const recent = useQuery({
    queryKey: ["conversations", projectId, false, "", 0],
    queryFn: () => api.get<Conversation[]>(`${base}?archived=false&offset=0&q=`),
  });
  const detail = useQuery({
    queryKey: ["conversation", projectId, selected],
    queryFn: () => api.get<Detail>(`${base}/${selected}`),
    enabled: !!selected,
    refetchInterval: (q) =>
      q.state.data?.messages.some((m) => active.has(m.status)) ? 2000 : false,
  });
  const providers = useQuery({
    queryKey: ["providers", projectId],
    queryFn: () => api.get<ProviderConfig[]>(`${root}/providers`),
  });
  const media = useConversationMedia(
    projectId,
    scopeKey,
    mode,
    providers.data || [],
  );
  const displayedModels = mode === "text" ? null : media.models;
  const workId = selected ? detail.data?.work_id || "" : draftWork;
  const models = (providers.data || []).filter(
    (p) => p.kind === "llm" && p.enabled && !p.config.hidden_from_catalog,
  );
  const selectedProvider = models.some((m) => m.provider_name === provider)
    ? provider
    : models.find((m) => m.config.is_default)?.provider_name ||
      models[0]?.provider_name ||
      "";
  const objects = useConversationObjects(
    projectId,
    contextOpen || targets.length > 0,
  );
  const works = useQuery({
    queryKey: ["works", projectId],
    queryFn: () => api.get<Work[]>(`${root}/works`),
  });
  const workName = works.data?.find((w) => w.id === workId)?.title;
  const returnPage = useQuery({
    queryKey: ["conversation-return", projectId, selected, returnPosition?.id],
    queryFn: async () => {
      let page = detail.data!;
      const rows: Message[] = [];
      while (
        page.has_older &&
        page.messages[0]?.ordinal > returnPosition!.ordinal
      ) {
        page = await api.get<Detail>(
          `${base}/${selected}?before=${page.messages[0].ordinal}`,
        );
        rows.unshift(...page.messages);
      }
      return { ...page, messages: rows };
    },
    enabled:
      !!selected &&
      detail.isSuccess &&
      !!returnPosition &&
      Number.isInteger(returnPosition.ordinal) &&
      returnPosition.ordinal > 0 &&
      !(detail.data?.messages || []).some((m) => m.id === returnPosition.id),
  });
  const messages = [
    ...older,
    ...(returnPage.data?.messages || []),
    ...(detail.data?.messages || []),
  ].filter((m, i, a) => a.findIndex((x) => x.id === m.id) === i);
  const latest = messages[messages.length - 1];
  const blocked = !!latest && active.has(latest.status);
  const select = (id: string) => {
    const next = new URLSearchParams(params);
    if (id) next.set("conversation", id);
    else next.delete("conversation");
    next.set("view", "chat");
    next.delete("message");
    next.delete("ordinal");
    setParams(next);
    setSidebarOpen(false);
  };
  useLayoutEffect(() => {
    setOlder([]);
    setHasOlder(false);
    restoredPosition.current = null;
    setAtBottom(!returnPosition);
  }, [selected]);
  useLayoutEffect(() => {
    const list = messageList.current;
    if (!list || !detail.isSuccess || restoredPosition.current === selected)
      return;
    const target = returnPosition
      ? document.getElementById(`conversation-message-${returnPosition.id}`)
      : null;
    if (returnPosition && !target && returnPage.isFetching) return;
    if (target)
      list.scrollTop +=
        target.getBoundingClientRect().top -
        list.getBoundingClientRect().top -
        returnPosition!.offset;
    else list.scrollTop = list.scrollHeight;
    restoredPosition.current = selected;
    skipInitialAutoScroll.current = !!returnPosition;
    setAtBottom(list.scrollHeight - list.scrollTop - list.clientHeight < 80);
  }, [
    selected,
    detail.isSuccess,
    messages.length,
    returnPage.isFetching,
    returnPosition,
  ]);
  useEffect(() => {
    if (skipInitialAutoScroll.current) {
      skipInitialAutoScroll.current = false;
      return;
    }
    if (atBottom && restoredPosition.current === selected)
      bottom.current?.scrollIntoView({ block: "nearest" });
  }, [selected, latest?.id, latest?.content, latest?.status, atBottom]);
  const refresh = async (id = selected) => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["conversation", projectId, id] }),
      qc.invalidateQueries({ queryKey: ["conversations", projectId] }),
    ]);
  };
  const openDraft = () => {
    setSidebarOpen(false);
    setArchived(false);
    setQuery("");
    setOffset(0);
    // Reuse the current empty conversation or the one persistent local draft.
    if (
      !selected ||
      (detail.data && !detail.data.archived && !detail.data.messages.length)
    ) {
      setSidebarOpen(false);
      input.current?.focus();
      return;
    }
    select("");
  };
  const update = useMutation({
    mutationFn: (data: {
      title?: string;
      archived?: boolean;
      work_id?: string | null;
    }) => api.patch<Conversation>(`${base}/${selected}`, data),
    onSuccess: async () => {
      await refresh();
    },
  });
  const send = useMutation({
    mutationFn: async (text: string) => {
      const body = {
        content: text.trim(),
        model_provider: mode === "text" ? selectedProvider : null,
        mode: "standard",
        scope: mode === "text" ? targets : [],
        skills,
        media: mode === "text" ? null : media.request,
      };
      const fingerprint = JSON.stringify(body);
      const request =
        pending?.fingerprint === fingerprint
          ? pending
          : { fingerprint, key: crypto.randomUUID() };
      setPending(request);
      let id = selected;
      if (!id) {
        const c = await api.post<Conversation>(base, {
          id: newId,
          work_id: workId || null,
        });
        id = c.id;
      }
      return {
        id,
        data: await api.post<Detail>(`${base}/${id}/messages`, {
          ...body,
          request_key: request.key,
        }),
      };
    },
    onSuccess: async ({ id, data }) => {
      if (!selected) {
        const prefix = `conversation.${me?.user.id}.${projectId}.${id}`;
        for (const [key, value] of Object.entries({
          provider: selectedProvider,
          mode,
          mediaModels: media.choices,
          mediaParameters: media.parameters,
          references: media.refs,
          targets,
          skills,
        })) {
          try {
            localStorage.setItem(`${prefix}.${key}`, JSON.stringify(value));
          } catch {
            /* draft persistence is best effort */
          }
        }
      }
      // Persist before changing the URL: the new scope can render in the same batch.
      try {
        localStorage.setItem(`${scopeKey}.draft`, JSON.stringify(""));
        localStorage.setItem(`${scopeKey}.pending`, "null");
      } catch {
        /* local draft persistence is best effort */
      }
      setDraft("");
      setPending(null);
      setNewId(crypto.randomUUID());
      qc.setQueryData(["conversation", projectId, id], data);
      setAtBottom(true);
      setArchived(false);
      setQuery("");
      setOffset(0);
      select(id);
      await refresh(id);
    },
  });
  const [retryKeys, setRetryKeys] = usePersistentState<Record<string, string>>(
    `${scopeKey}.retries`,
    {},
  );
  const retryTurn = useMutation({
    mutationFn: async (message: Message) => {
      const key = retryKeys[message.id] || crypto.randomUUID();
      setRetryKeys((v) => ({ ...v, [message.id]: key }));
      return api.post<Detail>(
        `${base}/${selected}/messages/${message.id}/retry`,
        { request_key: key },
      );
    },
    onSuccess: async (data, message) => {
      setRetryKeys((v) => {
        const next = { ...v };
        delete next[message.id];
        return next;
      });
      qc.setQueryData(["conversation", projectId, selected], data);
      setAtBottom(true);
      await refresh();
    },
  });
  const editRetry = useMutation({
    mutationFn: async (message: Message) => {
      if (draft.trim())
        throw new Error(
          "输入框里还有草稿，请先发送或清空，再载入原来的创作要求。",
        );
      const original = await api.get<RetryInput>(
        `${base}/${selected}/messages/${message.id}/retry-input`,
      );
      const refs = await Promise.all(
        (original.media?.references || []).map(async (r) => {
          const item = await api.get<GeneratedMaterial>(
            `${root}/library/generated/${r.generation_id}`,
          );
          if (!item.reference_available)
            throw new Error("原参考素材暂不可用，请先在素材库检查。");
          return {
            id: item.id,
            title: item.title,
            blob_hash: item.blob_hash,
            role: r.role,
          };
        }),
      );
      return { original, refs };
    },
    onSuccess: ({ original, refs }) => {
      setDraft(original.content);
      setTargets(original.scope);
      setSkills(original.skills);
      setPending(null);
      if (original.media) {
        setMode(original.media.request_type);
        media.restore(
          original.media.request_type,
          original.media.provider,
          original.media.provider_params,
        );
        media.setRefs(refs);
      } else {
        setMode("text");
        setProvider(original.model_provider || "");
      }
      input.current?.focus();
    },
  });
  const control = useMutation({
    mutationFn: ({ message, action }: { message: Message; action: string }) =>
      api.post(`${base}/${selected}/messages/${message.id}/control`, {
        action,
      }),
    onSuccess: () => refresh(),
  });
  const loadOlder = useMutation({
    mutationFn: () =>
      api.get<Detail>(`${base}/${selected}?before=${messages[0]?.ordinal}`),
    onSuccess: (page) => {
      setOlder((current) => [...page.messages, ...current]);
      setHasOlder(page.has_older);
    },
  });
  useEffect(() => {
    send.reset();
    retryTurn.reset();
    editRetry.reset();
    update.reset();
    control.reset();
    loadOlder.reset();
    setInspect(null);
    setContextOpen(false);
    setSkillOpen(false);
    setWorkOpen(false);
  }, [selected]);
  useEffect(() => {
    const incoming = location.state?.creativeGoal;
    if (typeof incoming !== "string" || !incoming.trim()) return;
    setDraft(incoming.slice(0, 8000));
    navigate(location.pathname + location.search, {
      replace: true,
      state: null,
    });
  }, [location.state]);
  const error =
    send.error ||
    retryTurn.error ||
    editRetry.error ||
    update.error ||
    control.error ||
    loadOlder.error;
  const busy = send.isPending || retryTurn.isPending || editRetry.isPending;
  const ready =
    !busy &&
    list.isSuccess &&
    !blocked &&
    !!draft.trim() &&
    !detail.data?.archived &&
    (!selected || detail.isSuccess) &&
    !providers.isFetching &&
    !providers.error &&
    (mode === "text" ? !!selectedProvider : media.ready);
  const importId = params.get("reference") || params.get("reuse");
  const imported = useQuery({
    queryKey: ["generated-material", projectId, importId],
    queryFn: () =>
      api.get<GeneratedMaterial>(`${root}/library/generated/${importId}`),
    enabled: !!importId,
    retry: false,
  });
  useEffect(() => {
    if (!imported.data || !importId) return;
    const item = imported.data;
    if (params.has("reference")) {
      if (!item.reference_available) return;
      media.setRefs([
        {
          id: item.id,
          title: item.title,
          blob_hash: item.blob_hash,
          role: "reference",
        },
      ]);
      setMode("image");
    } else {
      setDraft(item.prompt);
      setMode(item.output_type);
    }
    const next = new URLSearchParams(params);
    next.delete("reference");
    next.delete("reuse");
    next.set("view", "chat");
    setParams(next, { replace: true });
  }, [importId, imported.data]);
  const useOutput = (output: MediaOutput, nextMode: CreationMode) => {
    media.setRefs([
      {
        id: output.id,
        title: "对话生成的图片",
        blob_hash: output.blob_hash,
        role: "reference",
      },
    ]);
    setMode(nextMode);
    input.current?.focus();
  };

  const messageById = new Map(messages.map(message => [message.id, message]));

  return (
    <AssistantConversation
      key={scopeKey}
      messages={messages}
      draft={draft}
      disabled={busy || !!detail.data?.archived}
      sendDisabled={!ready}
      running={busy || blocked}
      loading={!!selected && detail.isPending}
      onSend={send.mutateAsync}
    >
    <ThreadPrimitive.Root asChild>
    <section
      className={`conversation-workspace integrated-history ${!messages.length ? "is-empty" : ""}`}
      aria-label="创作对话"
    >
      {navigationHost && createPortal(
        <div className="shell-conversation-list">
          <button onClick={openDraft} disabled={busy || (!!selected && !detail.isSuccess)} aria-current={!selected ? "page" : undefined}>
            <Plus size={15} />新建对话
          </button>
          {recent.isPending && <p role="status">正在读取对话…</p>}
          {recent.isError && <button onClick={() => recent.refetch()}>对话加载失败，重试</button>}
          {recent.data?.slice(0, 3).map(c => (
            <button key={c.id} title={c.title} aria-current={selected === c.id ? "page" : undefined} disabled={busy} onClick={() => select(c.id)}>
              <span>{c.title}</span>
            </button>
          ))}
          <button onClick={() => setSidebarOpen(true)}>全部对话</button>
        </div>, navigationHost
      )}
      <Modal open={sidebarOpen} onClose={() => setSidebarOpen(false)} title="全部对话" width={640}>
      <div
        id="conversation-sidebar"
        className="conversation-history-panel"
        aria-label="我的对话"
      >
        <Button
          variant="outline"
          onClick={openDraft}
          disabled={busy || (!!selected && !detail.isSuccess)}
        >
          <Plus size={16} />
          新的对话
        </Button>
        <SearchField
          label="搜索对话"
          placeholder="搜索对话"
          value={query}
          onValueChange={(value) => {
            setQuery(value);
            setOffset(0);
          }}
        />
        <div className="conversation-list-tools">
          <span>我的对话</span>
          <button
            aria-pressed={archived}
            onClick={() => {
              setArchived(!archived);
              setOffset(0);
            }}
          >
            <Archive size={14} />
            {archived ? "返回对话" : "已归档"}
          </button>
        </div>
        <nav
          className="conversation-list"
          aria-label={archived ? "已归档对话" : "对话列表"}
        >
          {list.isPending && <p role="status">正在读取对话…</p>}
          {list.isError && (
            <p role="alert">
              暂时无法连接对话服务，草稿会保留。{" "}
              <button onClick={() => list.refetch()}>重试</button>
            </p>
          )}
          {list.data?.map((c) => (
            <ConversationListItem
              key={c.id}
              conversation={c}
              selected={selected === c.id}
              disabled={busy}
              base={base}
              onSelect={() => select(c.id)}
              onSaved={async () => {
                await refresh(c.id);
              }}
            />
          ))}
          {list.data?.length === 0 && (
            <p>
              {archived
                ? "没有归档的对话"
                : "从一个想法开始，讨论会保存在这里。"}
            </p>
          )}
        </nav>
        {(offset > 0 || list.data?.length === 30) && (
          <div className="conversation-list-tools">
            <button disabled={!offset} onClick={() => setOffset(offset - 30)}>
              较新
            </button>
            <button
              disabled={list.data?.length !== 30}
              onClick={() => setOffset(offset + 30)}
            >
              较早
            </button>
          </div>
        )}
        <Link
          to={`${root}/materials?return=${encodeURIComponent(returnLink())}`}
          className="conversation-history"
        >
          浏览素材库 ↗
        </Link>
      </div>
      </Modal>
      <div className="conversation-main">
        <header className="conversation-heading">
          <div className="conversation-heading-title">
            <button
              className="conversation-sidebar-toggle"
              aria-label="全部对话"
              aria-controls="conversation-sidebar"
              aria-expanded={sidebarOpen}
              onClick={() => setSidebarOpen(true)}
            >
              <PanelLeft size={18} />
            </button>
            <h2>{detail.data?.title || "新的对话"}</h2>
            {detail.data?.archived && (
              <span className="conversation-archive-label">已归档</span>
            )}
          </div>
          <div className="conversation-header-tools">
            <button
              className="conversation-work-trigger"
              onClick={() => setWorkOpen(true)}
              disabled={
                busy ||
                blocked ||
                update.isPending ||
                (!!selected && !detail.isSuccess)
              }
              title={workName || "可选：将讨论关联到一份作品"}
            >
              <FolderOpen size={15} />
              <span>{workName || (workId ? "已关联作品" : "关联作品")}</span>
              <ChevronDown size={13} />
            </button>
          </div>
        </header>
        <Modal
          open={workOpen}
          onClose={() => setWorkOpen(false)}
          title="这段对话属于哪份作品？"
          width={560}
        >
          <p className="conversation-picker-intro">
            可以先自由讨论，有了明确方向再关联作品。同一作品可以有多段对话。
          </p>
          <WorkPicker
            projectId={projectId}
            value={workId}
            disabled={
              busy ||
              blocked ||
              update.isPending ||
              (!!selected && !detail.isSuccess)
            }
            onChange={(id) =>
              selected
                ? update.mutate({ work_id: id || null })
                : setDraftWork(id)
            }
          />
        </Modal>
        <div
          ref={messageList}
          className="conversation-messages"
          role="log"
          aria-label="对话内容"
          aria-live="polite"
          onScroll={(e) => {
            const el = e.currentTarget;
            setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
            if (restoredPosition.current === selected) {
              const top = el.getBoundingClientRect().top;
              const visible = [
                ...el.querySelectorAll<HTMLElement>("article[data-ordinal]"),
              ].find((item) => item.getBoundingClientRect().bottom > top + 12);
              if (visible)
                try {
                  sessionStorage.setItem(
                    `${scopeKey}.position`,
                    JSON.stringify({
                      id: visible.dataset.message,
                      ordinal: Number(visible.dataset.ordinal),
                      offset: visible.getBoundingClientRect().top - top,
                    }),
                  );
                } catch {
                  /* Browsing still works without session storage. */
                }
            }
          }}
        >
          {selected && detail.isPending && <p role="status">正在打开对话…</p>}
          {detail.isError && (
            <p role="alert">
              {detail.error.message}{" "}
              <button onClick={() => detail.refetch()}>重新加载</button>
            </p>
          )}
          {(older.length
            ? hasOlder
            : (returnPage.data?.has_older ?? detail.data?.has_older)) && (
            <button
              className="conversation-older"
              onClick={() => loadOlder.mutate()}
              disabled={loadOlder.isPending}
            >
              加载更早的消息
            </button>
          )}
          {!messages.length &&
            (!selected || (detail.isSuccess && !detail.isPending)) && (
              <div className="conversation-empty">
                <h3><Clapperboard aria-hidden="true" />今天，想创作些什么？</h3>

              </div>
            )}
          <ThreadPrimitive.Messages>{({ message }) => {
            const m = messageById.get(message.id);
            if (!m) return null;
            return <MessagePrimitive.Root asChild><article
              key={m.id}
              id={`conversation-message-${m.id}`}
              data-message={m.id}
              data-ordinal={m.ordinal}
              className={`conversation-message conversation-message-${m.role}`}
            >
              <div className="conversation-message-label">
                {m.role === "assistant" && (
                  <span className="conversation-assistant-mark">
                    <Sparkles size={14} />
                  </span>
                )}
                {m.role === "user" ? "你" : "创作助理"}
                {m.context.mode === "test" && <span>离线机制测试</span>}
              </div>
              {m.context.ui_blocks?.map(block => <GenerativeMessage
                key={block.id} block={block} disabled={busy || blocked || !!detail.data?.archived}
                onReply={text => {
                  const next = draft.trim() ? `${draft}\n\n${text}` : text;
                  if (next.length > 8000) throw new Error("输入框内容较长，请整理后再加入这份选择。");
                  setMode("text"); setDraft(next); input.current?.focus();
                }}
              />)}
              {m.content &&
                !(
                  m.execution_kind === "generation" &&
                  m.context.outputs?.length &&
                  /^(模拟测试：)?已生成(图片|视频)，可以预览、继续讨论或作为下一次创作的参考。$/.test(
                    m.content,
                  )
                ) && (
                  <ConversationText
                    content={m.content}
                    user={m.role === "user"}
                  />
                )}
              {m.role === "assistant" && (
                <>
                  {!!m.context.outputs?.length && (
                    <ConversationMediaResults
                      projectId={projectId}
                      outputs={m.context.outputs}
                      returnTo={returnLink(m)}
                      taskHref={
                        m.task_id
                          ? `${root}/tasks?kind=generation&task=${m.task_id}&return=${encodeURIComponent(returnLink(m))}`
                          : undefined
                      }
                      simulated={m.content.startsWith("模拟测试：")}
                      disabled={busy || !!detail.data?.archived}
                      reuse={useOutput}
                    />
                  )}
                  <ConversationTaskState
                    status={m.status}
                    kind={
                      m.execution_kind === "generation"
                        ? m.context.media?.request_type === "video"
                          ? "视频"
                          : "图片"
                        : "创作"
                    }
                    createdAt={m.created_at}
                    progress={m.progress[m.progress.length - 1]?.message}
                    error={m.error}
                    disabled={busy || blocked || !!detail.data?.archived}
                    retrying={
                      retryTurn.isPending && retryTurn.variables?.id === m.id
                    }
                    retry={() => retryTurn.mutate(m)}
                    edit={() => editRetry.mutate(m)}
                  />
                  {!(
                    m.execution_kind === "generation" &&
                    m.status === "completed" &&
                    m.context.outputs?.length
                  ) && (
                    <div
                      className="conversation-execution"
                      data-state={m.status}
                    >
                      <span>
                        {m.status === "completed" ? <Check size={13} /> : null}
                        {statuses[m.status] || m.status}
                      </span>
                      {m.task_id &&
                        (m.status === "needs_review" ? (
                          <button onClick={() => setInspect(m.task_id!)}>
                            审阅修改
                          </button>
                        ) : (
                          <Link
                            to={`${root}/tasks?kind=${m.execution_kind === "generation" ? "generation" : "agent"}&task=${m.task_id}&return=${encodeURIComponent(returnLink(m))}`}
                          >
                            任务详情
                          </Link>
                        ))}
                      {m.execution_kind !== "generation" &&
                        m.id === latest?.id &&
                        ["queued", "running"].includes(m.status) && (
                          <button
                            disabled={control.isPending}
                            onClick={() =>
                              control.mutate({ message: m, action: "pause" })
                            }
                          >
                            暂停
                          </button>
                        )}
                      {m.execution_kind !== "generation" &&
                        m.id === latest?.id &&
                        m.status === "paused" &&
                        !detail.data?.archived && (
                          <button
                            disabled={control.isPending}
                            onClick={() =>
                              control.mutate({ message: m, action: "resume" })
                            }
                          >
                            继续执行
                          </button>
                        )}
                      {active.has(m.status) && (
                        <button
                          disabled={control.isPending}
                          onClick={() =>
                            control.mutate({ message: m, action: "cancel" })
                          }
                        >
                          <Square size={12} />
                          停止任务
                        </button>
                      )}
                    </div>
                  )}
                </>
              )}
            </article></MessagePrimitive.Root>;
          }}</ThreadPrimitive.Messages>
          <div ref={bottom} />
        </div>
        {error && (
          <p className="conversation-error px-5" role="alert">
            {error.message}
          </p>
        )}
        <ComposerPrimitive.Root asChild><div className="conversation-compose">
          {((mode === "text" && targets.length > 0) || skills.length > 0) && (
            <div className="conversation-attachments" aria-label="本轮已选内容">
              {(mode === "text" ? targets : []).map((t) => {
                const item = objects.items.find(
                  (i) => i.type === t.target_type && i.id === t.target_id,
                );
                return (
                  <span
                    key={`${t.target_type}:${t.target_id}`}
                    className="conversation-attachment"
                    title={
                      item
                        ? `${item.source} · ${item.name}`
                        : "正在读取内容名称"
                    }
                  >
                    <Paperclip size={13} />
                    <span>
                      {item
                        ? `${item.name}${item.type === "shot" ? " · " + item.source : ""}`
                        : "已选内容"}
                    </span>
                    <button
                      disabled={busy}
                      aria-label={`移除内容 ${item?.name || "已选内容"}`}
                      onClick={() =>
                        setTargets(
                          targets.filter(
                            (x) =>
                              !(
                                x.target_type === t.target_type &&
                                x.target_id === t.target_id
                              ),
                          ),
                        )
                      }
                    >
                      <X size={12} />
                    </button>
                  </span>
                );
              })}
              {skills.map((s) => (
                <span key={s.id} className="conversation-attachment">
                  <Sparkles size={13} />
                  <span>{s.name || "创作技能"}</span>
                  <button
                    disabled={busy}
                    aria-label={`移除技能 ${s.name || "创作技能"}`}
                    onClick={() =>
                      setSkills(skills.filter((x) => x.id !== s.id))
                    }
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
          {mode !== "text" && media.refs.length > 0 && (
            <div className="conversation-attachments">
              {media.refs.map((r) => (
                <span key={r.id} className="conversation-attachment">
                  <Paperclip size={13} />
                  <span>{r.title}</span>
                  <button
                    disabled={busy}
                    aria-label={`移除参考 ${r.title}`}
                    onClick={() =>
                      media.setRefs((v) => v.filter((x) => x.id !== r.id))
                    }
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
          <ComposerPrimitive.Input
            ref={input}
            minRows={2}
            maxRows={6}
            cancelOnEscape={false}
            autoFocus={false}
            aria-label="对话消息"
            placeholder={
              detail.data?.archived
                ? "恢复对话后可继续"
                : blocked
                  ? "可以先写下想法，当前任务结束后继续发送…"
                  : mode === "image"
                    ? "描述想生成的画面，也可以引用上面的图片继续修改…"
                    : mode === "video"
                      ? "描述镜头、动作和氛围，直接生成一段视频…"
                      : "聊聊想法，或一起完善故事和镜头…"
            }
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={mode === "text" ? 8000 : 6000}
            disabled={!!detail.data?.archived || busy}
          />
          <div className="conversation-compose-tools">
            <SelectField
              label="本轮创作方式"
              className="composer-control composer-mode-select"
              value={mode}
              disabled={busy}
              onChange={(value) => setMode(value as CreationMode)}
              options={[
                { value: "text", label: "文字", icon: <Type size={17} aria-hidden="true" /> },
                { value: "image", label: "图片", icon: <Image size={17} aria-hidden="true" /> },
                { value: "video", label: "视频", icon: <Video size={17} aria-hidden="true" /> },
              ]}
            />
            <ComposerModelPicker
              models={displayedModels || models}
              value={mode === "text" ? selectedProvider : media.provider}
              onChange={mode === "text" ? setProvider : media.choose}
              disabled={busy}
              loading={providers.isPending}
              error={providers.error?.message}
              retry={() => {
                void providers.refetch();
              }}
            />
            {mode !== "text" && (
              <ConversationMediaControls
                projectId={projectId}
                media={media}
                mode={mode}
                disabled={busy || !!detail.data?.archived}
              />
            )}
            {mode === "text" && (
              <button
                className="conversation-tool"
                type="button"
                disabled={busy}
                onClick={() => setContextOpen(true)}
                title="选择要一起修改的剧本、镜头或设定"
              >
                <Paperclip size={17} />
                <span>添加内容</span>
              </button>
            )}
            <button
              className="conversation-tool"
              type="button"
              disabled={busy}
              onClick={() => setSkillOpen(true)}
              title="选用已有的创作方法"
            >
              <Sparkles size={17} />
              <span>技能</span>
            </button>
            <ComposerPrimitive.Send
              className="conversation-send"
              type="button"
              aria-label="发送消息"
              disabled={!ready}
            >
              {send.isPending ? (
                <Loader2 size={19} className="conversation-spinner" />
              ) : (
                <ArrowUp size={19} />
              )}
            </ComposerPrimitive.Send>
          </div>
          {send.isPending && (
            <p className="conversation-note" role="status">
              正在提交创作要求…
            </p>
          )}
          {mode !== "text" && media.error && (
            <p className="conversation-error" role="alert">
              {media.error} <button onClick={media.retry}>重新检查</button>
            </p>
          )}
          {imported.error && (
            <p className="conversation-error" role="alert">
              {imported.error.message}
            </p>
          )}
          {imported.data &&
            params.has("reference") &&
            !imported.data.reference_available && (
              <p className="conversation-note">
                这项素材不能作为图片参考，请在素材库中选择图片。
              </p>
            )}
          {!(displayedModels || models).length && !providers.isPending && (
            <p className="conversation-note">
              请先配置当前类型的可用模型。
              <Link to={`${root}/settings?section=channels`}>配置模型</Link>
            </p>
          )}
          {blocked && (
            <p className="conversation-note">
              {statuses[latest!.status]}
              ：处理完当前任务后可继续发送，也可以新开一段对话。
            </p>
          )}
        </div></ComposerPrimitive.Root>
        {!messages.length && (!selected || detail.isSuccess) && (
                <div className="conversation-starters">
                  {[
                    [
                      "梳理一个想法",
                      "我有一个创作想法，请先和我讨论故事方向。",
                    ],
                    [
                      "打磨故事与人物",
                      "我想打磨故事和人物，请先帮我梳理值得改进的地方。",
                    ],
                    [
                      "构思镜头画面",
                      "我想构思一组镜头，请先和我讨论画面、情绪与节奏。",
                    ],
                  ].map(([label, text]) => (
                    <button
                      key={label}
                      disabled={busy || !!detail.data?.archived}
                      onClick={() => {
                        setDraft(text);
                        setMode("text");
                        input.current?.focus();
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
        )}
      </div>
      {contextOpen && (
        <ConversationObjectDialog
          projectId={projectId}
          catalog={objects}
          value={targets}
          onChange={setTargets}
          onClose={() => setContextOpen(false)}
        />
      )}
      {skillOpen && (
        <ConversationSkillDialog
          projectId={projectId}
          value={skills}
          onChange={setSkills}
          onClose={() => setSkillOpen(false)}
        />
      )}
      <Modal
        open={!!inspect}
        onClose={() => {
          setInspect(null);
          void refresh();
        }}
        title="这条消息的执行过程"
        width={1050}
      >
        <Suspense fallback={<p>正在读取执行过程…</p>}>
          {inspect && (
            <TaskInspector
              key={inspect}
              projectId={projectId}
              initialRunId={inspect}
              embedded
              taskOnly
            />
          )}
        </Suspense>
      </Modal>
    </section>
    </ThreadPrimitive.Root>
    </AssistantConversation>
  );
}
