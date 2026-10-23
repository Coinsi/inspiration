import type { ReactNode } from "react";
import { ConversationText } from "./ConversationText";
import { GenerativeMessage, type UIBlock } from "./GenerativeMessage";

/** Legacy prose introduces the answer; a present tree keeps its own narrative order. */
export function ConversationAnswer({ content, blocks = [], disabled, onReply, children }: {
  content?: string;
  blocks?: UIBlock[];
  disabled: boolean;
  onReply: (text: string) => void;
  children?: ReactNode;
}) {
  return <div className="conversation-answer">
    {content && <ConversationText content={content} user={false} />}
    {blocks.map(block => <GenerativeMessage key={block.id} block={block} disabled={disabled} onReply={onReply} />)}
    {children}
  </div>;
}
