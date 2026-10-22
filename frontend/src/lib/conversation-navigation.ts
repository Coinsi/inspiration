import { createContext } from "react";

// The workspace owns conversation state; the shell only supplies its display slot.
export const ConversationNavigationHost = createContext<HTMLElement | null>(null);
