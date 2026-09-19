import { WORKSPACE_DESTINATIONS } from "./workspace-navigation";

// Full route catalog stays available to quick search. Sidebar shows stages and only related tools.
export const WORKSPACE_STAGES = [
  { id: "home", zh: "工作台", en: "Home", routes: ["workbench"] },
  {
    id: "story",
    zh: "故事与剧本",
    en: "Story",
    routes: ["narrative", "scripts", "bible"],
  },
  {
    id: "media",
    zh: "素材库",
    en: "Materials",
    routes: ["assets", "library", "evidence", "assets/trash"],
  },
  {
    id: "shots",
    zh: "镜头创作",
    en: "Shots",
    routes: ["storyboard", "shots", "director"],
  },
  { id: "canvas", zh: "自由画布", en: "Canvas", routes: ["canvas"] },
  {
    id: "editing",
    zh: "剪辑成片",
    en: "Editing",
    routes: ["cuts", "transcriptions"],
  },
  { id: "assistant", zh: "创作助理", en: "Assistant", routes: ["agent"] },
  {
    id: "tools",
    zh: "创作工具",
    en: "Creative tools",
    routes: ["skills", "prompts"],
  },
].map((stage) => ({
  ...stage,
  items: stage.routes.map((route) =>
    WORKSPACE_DESTINATIONS.find((item) => item.to === route)!,
  ),
}));
