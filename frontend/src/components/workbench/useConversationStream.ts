import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getToken } from "@/lib/api";

export function useConversationStream(projectId: string, conversationId: string | null) {
  const client = useQueryClient();
  useEffect(() => {
    if (!conversationId) return;
    const controller = new AbortController();
    let retry: ReturnType<typeof setTimeout> | undefined;
    async function connect() {
      const token = getToken();
      if (!token || controller.signal.aborted) return;
      try {
        const response = await fetch(
          `/api/v1/projects/${projectId}/conversations/${conversationId}/events`,
          { headers: { Authorization: `Bearer ${token}` }, signal: controller.signal },
        );
        if ([401, 403, 404].includes(response.status)) return;
        if (!response.ok || !response.body) throw new Error("Stream unavailable");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        try {
          while (!controller.signal.aborted) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let boundary: number;
            while ((boundary = buffer.indexOf("\n\n")) >= 0) {
              const event = buffer.slice(0, boundary);
              buffer = buffer.slice(boundary + 2);
              if (event.startsWith("event: unavailable")) return;
              if (!event.startsWith("event: snapshot")) continue;
              const data = JSON.parse(event.slice(event.indexOf("data: ") + 6));
              if (!controller.signal.aborted && getToken() === token)
                client.setQueryData(["conversation", projectId, conversationId], data);
            }
          }
        } finally { await reader.cancel().catch(() => {}); }
      } catch { /* Polling remains available during network/proxy interruptions. */ }
      if (!controller.signal.aborted) retry = setTimeout(connect, 2000);
    }
    void connect();
    return () => { controller.abort(); clearTimeout(retry); };
  }, [projectId, conversationId, client]);
}
