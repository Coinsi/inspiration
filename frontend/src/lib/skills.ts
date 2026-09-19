export type SkillFile = {
  path: string;
  content: string;
  encoding: "utf-8" | "base64";
};
export type SkillUse = { id: string; revision: number; name?: string };
export type Skill = {
  id?: string;
  revision?: number;
  archived?: boolean;
  name: string;
  description: string;
  instructions: string;
  required_tools: string[];
  source: string;
  category?: string;
  files?: SkillFile[];
  source_metadata?: {
    kind: string;
    url?: string;
    ref?: string;
    directory?: string;
    commit?: string;
    origin_id?: string;
  };
};
export type SkillCard = Skill & {
  project_id: string;
  file_count: number;
  favorite?: boolean;
};
export const categories: Record<string, string> = {
  general: "通用方法",
  story: "故事与剧本",
  character: "角色与设定",
  shot: "镜头与画面",
  edit: "剪辑与交付",
};
