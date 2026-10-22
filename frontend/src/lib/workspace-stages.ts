import { WORKSPACE_DESTINATIONS } from "./workspace-navigation";

// Full route catalog stays available to quick search. Sidebar shows stages and only related tools.
export const WORKSPACE_STAGES = [
  { id: "works", zh: "作品", en: "Works", routes: ["works"] },
  { id: "home", zh: "创作", en: "Home", routes: ["workbench"] },
  {
    id: "story",
    zh: "故事",
    en: "Story",
    routes: ["narrative", "scripts", "bible"],
  },
  {
    id: "media",
    zh: "素材",
    en: "Materials",
    routes: ["materials"],
  },
  {
    id: "shots",
    zh: "镜头",
    en: "Shots",
    routes: ["storyboard", "shots", "director"],
  },
  { id: "canvas", zh: "画布", en: "Canvas", routes: ["canvas"] },
  {
    id: "editing",
    zh: "剪辑",
    en: "Editing",
    routes: ["cuts", "transcriptions"],
  },
  {
    id: "tools",
    zh: "工具",
    en: "Creative tools",
    routes: ["skills", "prompts"],
  },
].map((stage) => ({
  ...stage,
  items: stage.routes.map((route) =>
    WORKSPACE_DESTINATIONS.find((item) => item.to === route)!,
  ),
}));
