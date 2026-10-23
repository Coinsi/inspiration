import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { ResponseMarkdown } from "../ui/response-markdown";

export function ConversationText({
  content,
  user,
}: {
  content: string;
  user: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const collapsible =
    user && (content.length > 600 || content.split("\n").length > 10);
  return (
    <>
      <div
        className={`conversation-markdown ${collapsible && !expanded ? "is-collapsed" : ""}`}
      >
        {user ? <p>{content}</p> : <ResponseMarkdown>{content}</ResponseMarkdown>}
      </div>
      {collapsible && (
        <button
          className="conversation-expand-text"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "收起" : "展开完整描述"}
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      )}
    </>
  );
}
