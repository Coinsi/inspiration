import { useCallback, useLayoutEffect, useRef, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type AppendMessage,
  type ThreadMessageLike,
  type AssistantRuntime,
} from "@assistant-ui/react";

export type AssistantMessageSource = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: string;
  created_at: string;
};

export function toAssistantMessage(message: AssistantMessageSource): ThreadMessageLike {
  return {
    id: message.id,
    role: message.role,
    createdAt: new Date(message.created_at),
    content: [{ type: "text", text: message.content }],
    ...(message.role === "assistant" ? {
      status: message.status === "queued" || message.status === "running"
        ? { type: "running" as const }
        : message.status === "failed"
          ? { type: "incomplete" as const, reason: "error" as const }
          : message.status === "cancelled" || message.status === "paused" || message.status === "needs_review"
            ? { type: "incomplete" as const, reason: "cancelled" as const }
            : { type: "complete" as const, reason: "stop" as const },
    } : {}),
  };
}

/** Key this boundary by project + conversation, so runtimes cannot share drafts or branches. */
export function AssistantConversation({
  messages, draft, disabled, sendDisabled, running, loading, onSend, children,
}: {
  messages: readonly AssistantMessageSource[];
  draft: string;
  disabled: boolean;
  sendDisabled: boolean;
  running: boolean;
  loading: boolean;
  onSend: (text: string) => Promise<unknown>;
  children: ReactNode;
}) {
  const runtimeRef = useRef<AssistantRuntime | null>(null);
  const onNew = useCallback(async (message: AppendMessage): Promise<void> => {
    const text = message.content
      .filter(part => part.type === "text")
      .map(part => part.text).join("\n");
    if (!text.trim() || sendDisabled) return;
    try {
      await onSend(text);
    } catch (error) {
      // The existing mutation displays the error and retains its idempotency key.
      // assistant-ui clears its input before dispatch; restore that copy on failure.
      runtimeRef.current?.thread.composer.setText(text);
      throw error;
    }
  }, [onSend, sendDisabled]);
  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: toAssistantMessage,
    isRunning: running,
    isLoading: loading,
    isDisabled: disabled,
    isSendDisabled: sendDisabled,
    onNew,
  });
  runtimeRef.current = runtime;
  useLayoutEffect(() => {
    if (runtime.thread.composer.getState().text !== draft) {
      runtime.thread.composer.setText(draft);
    }
  }, [runtime, draft]);
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
