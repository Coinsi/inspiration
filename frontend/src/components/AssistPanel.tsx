import SkillPicker from "@/components/SkillPicker";
import type { SkillUse } from "@/lib/skills";
// 贯穿式 AI 对话助手侧栏:对当前对象多轮对话 → 修改提议(预览)→ 应用(版本快照)。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Check,
  Loader2,
  PanelRightClose,
  Send,
  Sparkles,
  UserRound,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { useI18n } from "@/lib/i18n";
import { api, type AssistChat, type AssistMessage } from "@/lib/api";
import { cn } from "@/lib/utils";

export default function AssistPanel({
  projectId,
  targetType,
  targetId,
  onApplied,
  onCollapse,
  className,
}: {
  projectId: string;
  targetType: string;
  targetId: string;
  onApplied?: () => void;
  onCollapse?: () => void;
  className?: string;
}) {
  const base = `/projects/${projectId}`;
  const qc = useQueryClient();
  const toast = useToast();
  const { t: tr } = useI18n();
  const [skills, setSkills] = useState<SkillUse[]>([]);
  const [input, setInput] = useState("");
  const [chatId, setChatId] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // 复用该对象最近一次会话;没有则首次发送时创建
  const { data: chats } = useQuery({
    queryKey: ["assist-chats", targetType, targetId],
    queryFn: () =>
      api.get<AssistChat[]>(
        `${base}/assist/chats?target_type=${targetType}&target_id=${targetId}`,
      ),
  });
  useEffect(() => {
    if (!chatId && chats && chats.length > 0) setChatId(chats[0].id);
  }, [chats, chatId]);

  const { data: chat } = useQuery({
    queryKey: ["assist-chat", chatId],
    queryFn: () => api.get<AssistChat>(`${base}/assist/chats/${chatId}`),
    enabled: !!chatId,
  });

  const messages = chat?.messages ?? [];
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages.length]);

  const send = useMutation({
    mutationFn: async (content: string) => {
      let cid = chatId;
      if (!cid) {
        const created = await api.post<AssistChat>(`${base}/assist/chats`, {
          target_type: targetType,
          target_id: targetId,
        });
        cid = created.id;
        setChatId(cid);
      }
      return api.post(`${base}/assist/chats/${cid}/messages`, {
        content,
        skills: skills.map(({ id, revision }) => ({ id, revision })),
      });
    },
    onSuccess: () => {
      setInput("");
      void qc.invalidateQueries({ queryKey: ["assist-chat", chatId] });
      void qc.invalidateQueries({
        queryKey: ["assist-chats", targetType, targetId],
      });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });

  const apply = useMutation({
    mutationFn: (messageId: string) =>
      api.post(`${base}/assist/chats/${chatId}/messages/${messageId}/apply`),
    onSuccess: () => {
      toast.push(tr("assist.appliedToast"), "success");
      void qc.invalidateQueries({ queryKey: ["assist-chat", chatId] });
      onApplied?.();
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });

  const submit = () => {
    const v = input.trim();
    if (v && !send.isPending) send.mutate(v);
  };

  return (
    <div
      className={cn(
        "flex min-h-0 flex-col rounded-lg border border-border bg-card",
        className,
      )}
    >
      <div className="flex h-10 shrink-0 items-center gap-1.5 border-b border-border px-3 text-sm font-medium">
        <Sparkles className="h-4 w-4 text-primary" />
        <span className="flex-1 truncate">{tr("assist.title")}</span>
        {onCollapse && (
          <button
            onClick={onCollapse}
            title={tr("assist.collapse")}
            className="grid h-6 w-6 place-items-center rounded-md text-faint transition hover:bg-muted hover:text-foreground"
          >
            <PanelRightClose className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      <div
        ref={scrollRef}
        className="min-h-0 flex-1 space-y-3 overflow-auto p-3"
      >
        {messages.length === 0 && !send.isPending && (
          <div className="flex flex-col items-center gap-2 px-3 py-10 text-center text-muted-foreground">
            <Bot className="h-7 w-7 opacity-40" />
            <p className="text-xs leading-5">{tr("assist.empty")}</p>
          </div>
        )}
        {messages.map((m) => (
          <Message
            key={m.id}
            m={m}
            onApply={(id) => apply.mutate(id)}
            applying={apply.isPending}
          />
        ))}
        {send.isPending && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />{" "}
            {tr("assist.thinking")}
          </div>
        )}
      </div>

      <div className="shrink-0 border-t border-border p-2 space-y-2">
        <SkillPicker
          projectId={projectId}
          value={skills}
          onChange={setSkills}
          disabled={send.isPending}
        />
        <div className="flex items-end gap-1.5">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={2}
            placeholder={tr("assist.placeholder")}
            className="min-h-[3rem] flex-1 resize-none rounded-md border border-border bg-bg px-2.5 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          />
          <Button
            size="sm"
            className="h-9 w-9 p-0"
            disabled={!input.trim() || send.isPending}
            onClick={submit}
            title={tr("assist.send")}
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function fmtTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function Message({
  m,
  onApply,
  applying,
}: {
  m: AssistMessage;
  onApply: (id: string) => void;
  applying: boolean;
}) {
  const { t: tr } = useI18n();

  if (m.role === "user") {
    return (
      <div className="flex flex-col items-end gap-1">
        {/* 发送者:我 */}
        <div className="flex items-center gap-1.5 text-[11px] text-faint">
          <span>{fmtTime(m.created_at)}</span>
          <span className="font-medium text-muted-foreground">
            {tr("assist.me")}
          </span>
          <span className="grid h-5 w-5 place-items-center rounded-full bg-primary/20 text-primary">
            <UserRound className="h-3 w-3" />
          </span>
        </div>
        <div className="max-w-[88%] rounded-xl rounded-tr-sm bg-primary px-3 py-2 text-sm leading-6 text-primary-foreground shadow-sm">
          {m.content}
        </div>
        {!!m.skills?.length && (
          <p className="text-xs text-muted-foreground">
            参考技能：
            {m.skills.map((s) => `${s.name} v${s.revision}`).join("、")}
          </p>
        )}
      </div>
    );
  }

  const p = m.proposal;
  const hasOps = !!p && p.ops.length > 0;
  return (
    <div className="flex flex-col items-start gap-1">
      {/* 发送者:AI 助手 */}
      <div className="flex items-center gap-1.5 text-[11px] text-faint">
        <span className="grid h-5 w-5 place-items-center rounded-full bg-primary text-primary-foreground">
          <Bot className="h-3 w-3" />
        </span>
        <span className="font-medium text-muted-foreground">
          {tr("assist.title")}
        </span>
        <span>{fmtTime(m.created_at)}</span>
      </div>
      <div className="max-w-[92%] space-y-1.5">
        <div className="rounded-xl rounded-tl-sm border border-border bg-elevated px-3 py-2 text-sm leading-6">
          {m.content ||
            (p?.needs_clarification ? tr("assist.needsClarify") : "")}
        </div>
        {hasOps && (
          <div className="flex items-center justify-between gap-2 rounded-md border border-primary/30 bg-primary/8 px-2.5 py-1.5">
            <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
              <Sparkles className="mr-1 inline h-3 w-3 text-primary" />
              {p.summary || tr("assist.proposal")} · {p.ops.length} ops
            </span>
            {p.applied_at ? (
              <span className="flex shrink-0 items-center gap-1 text-xs font-medium text-success">
                <Check className="h-3.5 w-3.5" /> {tr("assist.applied")}
              </span>
            ) : (
              <Button
                size="sm"
                className="h-6 px-2 text-xs"
                disabled={applying}
                onClick={() => onApply(m.id)}
              >
                {tr("assist.apply")}
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
