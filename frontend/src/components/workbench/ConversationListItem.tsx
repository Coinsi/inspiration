import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  Archive,
  Check,
  MessageSquare,
  Pencil,
  RotateCcw,
  X,
} from "lucide-react";
import { api } from "@/lib/api";

export function ConversationListItem({
  conversation,
  selected,
  disabled,
  base,
  onSelect,
  onSaved,
}: {
  conversation: { id: string; title: string; archived: boolean };
  selected: boolean;
  disabled: boolean;
  base: string;
  onSelect: () => void;
  onSaved: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(conversation.title);
  const save = useMutation({
    mutationFn: (data: { title?: string; archived?: boolean }) =>
      api.patch(`${base}/${conversation.id}`, data),
    onSuccess: async () => {
      setEditing(false);
      await onSaved();
    },
  });
  const commit = () => {
    if (!title.trim() || save.isPending) return;
    if (title.trim() === conversation.title) {
      setEditing(false);
      return;
    }
    save.mutate({ title: title.trim() });
  };
  return (
    <div
      className="conversation-list-item"
      data-selected={selected || undefined}
    >
      {editing ? (
        <form
          className="conversation-rename"
          onSubmit={(e) => {
            e.preventDefault();
            commit();
          }}
        >
          <input
            autoFocus
            aria-label="对话名称"
            value={title}
            maxLength={120}
            onFocus={(e) => e.currentTarget.select()}
            onChange={(e) => setTitle(e.target.value)}
            disabled={save.isPending}
            onKeyDown={(e) => {
              if (e.key === "Escape" && !save.isPending) {
                e.preventDefault();
                setEditing(false);
                save.reset();
              }
              if (e.key === "Enter" && e.nativeEvent.isComposing)
                e.preventDefault();
            }}
          />
          <div className="conversation-row-actions">
            <button
              type="submit"
              aria-label="保存对话名称"
              title="保存（Enter）"
              disabled={!title.trim() || save.isPending}
            >
              <Check size={14} />
            </button>
            <button
              type="button"
              aria-label="取消重命名"
              title="取消（Esc）"
              disabled={save.isPending}
              onClick={() => {
                setEditing(false);
                save.reset();
              }}
            >
              <X size={14} />
            </button>
          </div>
        </form>
      ) : (
        <>
          <button
            className="conversation-row-select"
            aria-current={selected ? "page" : undefined}
            title={conversation.title}
            onClick={onSelect}
            disabled={disabled}
          >
            <MessageSquare size={15} />
            <span>{conversation.title}</span>
          </button>
          <div className="conversation-row-actions">
            <button
              aria-label={`重命名对话：${conversation.title}`}
              title="重命名"
              disabled={disabled || save.isPending}
              onClick={() => {
                setTitle(conversation.title);
                save.reset();
                setEditing(true);
              }}
            >
              <Pencil size={14} />
            </button>
            <button
              aria-label={`${conversation.archived ? "恢复" : "归档"}对话：${conversation.title}`}
              title={conversation.archived ? "恢复对话" : "归档对话"}
              disabled={disabled || save.isPending}
              onClick={() => save.mutate({ archived: !conversation.archived })}
            >
              {conversation.archived ? (
                <RotateCcw size={14} />
              ) : (
                <Archive size={14} />
              )}
            </button>
          </div>
        </>
      )}
      {save.error && (
        <p className="conversation-row-error" role="alert">
          {save.error.message}
        </p>
      )}
    </div>
  );
}
