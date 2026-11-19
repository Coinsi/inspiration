import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { useAuth } from "./auth";
import {
  editableContent,
  type History,
  type TimelineContent,
  type TimelineDocument,
} from "./timeline-edit";

type Backup = { revision: number; content: TimelineContent };
type Write = {
  revision: number;
  submitted: TimelineContent;
  result: Promise<TimelineDocument>;
};
const writes = new Map<string, Write>();

function readBackup(key: string): Backup | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "null");
    return value &&
      Number.isInteger(value.revision) &&
      ["items", "visuals", "audio", "subtitles"].every((track) =>
        Array.isArray(value.content?.[track]),
      )
      ? value
      : null;
  } catch {
    return null;
  }
}

export function useTimelineDraft(
  project: string,
  timeline: string,
  initial: TimelineDocument,
) {
  const qc = useQueryClient();
  const { me } = useAuth();
  const key = `timeline.draft.${me?.user.id}.${project}.${timeline}`;
  const path = `/projects/${project}/timelines/${timeline}/document`;
  const queryKey = ["timeline-document", project, timeline];
  const receiptKey = ["timeline-write", key];
  const [backup] = useState(() => readBackup(key));
  const [server, setServer] = useState(() => ({
    revision: initial.revision,
    content: editableContent(initial),
  }));
  const [baseRevision, setBaseRevision] = useState(
    backup?.revision ?? initial.revision,
  );
  const [history, setHistory] = useState<History<TimelineContent>>(() => ({
    past: [],
    present: backup?.content || server.content,
    future: [],
  }));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [protectedLocally, setProtectedLocally] = useState(false);
  const content = history.present;
  const signature = useMemo(() => JSON.stringify(content), [content]);
  const savedSignature = useMemo(
    () => JSON.stringify(server.content),
    [server.content],
  );
  const saved = signature === savedSignature;
  const conflict = !saved && baseRevision !== server.revision;
  const latest = useRef({ content, saved, server, conflict });
  latest.current = { content, saved, server, conflict };
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useLayoutEffect(() => {
    try {
      if (saved) sessionStorage.removeItem(key);
      else
        sessionStorage.setItem(
          key,
          JSON.stringify({ revision: baseRevision, content }),
        );
      setProtectedLocally(!saved);
    } catch {
      setProtectedLocally(false);
    }
  }, [key, baseRevision, content, saved]);

  function accept(doc: TimelineDocument) {
    const next = editableContent(doc);
    setServer({ revision: doc.revision, content: next });
    setBaseRevision(doc.revision);
    setHistory({ past: [], present: next, future: [] });
    setError("");
    qc.setQueryData(queryKey, doc);
  }
  function acknowledge(doc: TimelineDocument, submitted: TimelineContent) {
    const next = { revision: doc.revision, content: editableContent(doc) };
    const unchanged =
      latest.current.content === submitted ||
      JSON.stringify(latest.current.content) === JSON.stringify(submitted);
    latest.current = {
      content: unchanged ? next.content : latest.current.content,
      saved: unchanged,
      server: next,
      conflict: false,
    };
    if (mounted.current) {
      setServer(next);
      setBaseRevision(doc.revision);
      if (unchanged) setHistory((h) => ({ ...h, present: next.content }));
      setError("");
    }
  }
  useEffect(() => {
    if (initial.revision <= server.revision) return;
    const receipt = qc.getQueryData<{
      revision: number;
      from: number;
      submitted: TimelineContent;
    }>(receiptKey);
    if (receipt?.revision === initial.revision && receipt.from === baseRevision)
      acknowledge(initial, receipt.submitted);
    else if (saved) accept(initial);
    else
      setServer({
        revision: initial.revision,
        content: editableContent(initial),
      });
  }, [initial, server.revision, saved]);

  async function persist(): Promise<number> {
    const ongoing = writes.get(key);
    if (ongoing) {
      const doc = await ongoing.result;
      if (latest.current.server.revision === ongoing.revision)
        acknowledge(doc, ongoing.submitted);
      return persist();
    }
    const current = latest.current;
    if (current.conflict) throw new Error("时间线已有更新，请先处理版本冲突。");
    if (current.saved) return current.server.revision;
    const submitted = current.content;
    if (mounted.current) {
      setSaving(true);
      setError("");
    }
    const write = api.put<TimelineDocument>(path, {
      ...submitted,
      revision: current.server.revision,
    });
    writes.set(key, {
      revision: current.server.revision,
      submitted,
      result: write,
    });
    try {
      const doc = await write;
      qc.setQueryData(receiptKey, {
        revision: doc.revision,
        from: current.server.revision,
        submitted,
      });
      acknowledge(doc, submitted);
      if (!mounted.current) {
        const local = readBackup(key);
        if (local?.revision === current.server.revision) {
          try {
            if (JSON.stringify(local.content) === JSON.stringify(submitted))
              sessionStorage.removeItem(key);
            else
              sessionStorage.setItem(
                key,
                JSON.stringify({ ...local, revision: doc.revision }),
              );
          } catch {
            /* The server already has the submitted revision. */
          }
        }
      }
      qc.setQueryData(queryKey, doc);
      void qc.invalidateQueries({
        queryKey: ["timeline-history", project, timeline],
      });
      return doc.revision;
    } catch (cause) {
      if (mounted.current)
        setError(cause instanceof Error ? cause.message : "保存失败，请重试");
      void qc.invalidateQueries({ queryKey });
      throw cause;
    } finally {
      writes.delete(key);
      if (mounted.current) setSaving(false);
    }
  }
  async function loadLatest() {
    const doc = await api.get<TimelineDocument>(path);
    accept(doc);
  }
  return {
    content,
    history,
    setHistory,
    revision: server.revision,
    saved,
    saving,
    error,
    conflict,
    protectedLocally,
    persist,
    accept,
    loadLatest,
  };
}

export function useAutoSaveTimeline(
  content: TimelineContent,
  enabled: boolean,
  persist: () => Promise<number>,
) {
  const save = useRef(persist);
  save.current = persist;
  const flush = useRef(enabled);
  flush.current = enabled;
  useEffect(
    () => () => {
      // A navigation inside the app can happen before the debounce finishes.
      if (flush.current) void save.current().catch(() => {});
    },
    [],
  );
  useEffect(() => {
    if (!enabled) return;
    const timer = window.setTimeout(() => {
      void save.current().catch(() => {});
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [content, enabled]);
}
