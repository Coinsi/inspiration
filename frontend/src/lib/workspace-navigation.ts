import {
  Activity,
  Camera,
  Captions,
  Bot,
  Workflow,
  BookMarked,
  BookOpen,
  Clapperboard,
  FileText,
  Film,
  Images,
  LayoutDashboard,
  LayoutGrid,
  ScrollText,
  Settings,
  Trash2,
  Type,
  Users,
} from "lucide-react";

// Sidebar and quick navigation share only routes that are implemented in App.tsx.
export const WORKSPACE_GROUPS = [
  {
    title: "nav.overview",
    items: [
      {
        to: "workbench",
        label: "nav.workbench",
        icon: LayoutDashboard,
        keywords: "工作台 概览 overview home",
      },
    ],
  },
  {
    title: "nav.create",
    items: [
      {
        to: "narrative",
        label: "nav.narrative",
        icon: ScrollText,
        keywords: "故事 小说 章节 导入 novel story import",
      },
      {
        to: "bible",
        label: "nav.bible",
        icon: BookMarked,
        keywords: "世界观 设定 人设 bible settings",
      },
      {
        to: "scripts",
        label: "nav.scripts",
        icon: FileText,
        keywords: "剧本 台词 改编 script dialogue",
      },
    ],
  },
  {
    title: "nav.assets",
    items: [
      {
        to: "assets",
        label: "nav.assetLibrary",
        icon: Images,
        keywords:
          "素材 资产 角色 人物 场景 道具 图片 参考 asset character image reference",
      },
      {
        to: "skills",
        label: "nav.skills",
        icon: BookOpen,
        keywords: "技能 来源 MCP skill source import",
      },
      {
        to: "prompts",
        label: "nav.prompts",
        icon: Type,
        keywords: "提示词 模板 prompt template",
      },
      {
        to: "library",
        label: "nav.videoLibrary",
        icon: Film,
        keywords: "视频 素材 原片 片段 引用 上传 library footage clips upload",
      },
      {
        to: "evidence",
        label: "nav.evidence",
        icon: Users,
        keywords: "身份 证据 角色 关联 同框 evidence identity",
      },
    ],
  },
  {
    title: "nav.production",
    items: [
      {
        to: "storyboard",
        label: "nav.storyboard",
        icon: Clapperboard,
        keywords: "分镜 故事板 storyboard",
      },
      {
        to: "shots",
        label: "nav.shots",
        icon: LayoutGrid,
        keywords: "镜头 生成 视频 shot generate video",
      },
      {
        to: "agent",
        label: "nav.agent",
        icon: Bot,
        keywords: "助理 创作 智能 agent assistant",
      },
      {
        to: "canvas",
        label: "nav.canvas",
        icon: Workflow,
        keywords: "画布 自由 节点 流程 canvas workflow",
      },
      {
        to: "director",
        label: "nav.director",
        icon: Camera,
        keywords: "导演 3D 站位 运镜 相机 预演 director stage camera",
      },
      {
        to: "cuts",
        label: "nav.cuts",
        icon: Film,
        keywords: "剪辑 成片 导出 时间线 cut edit export timeline",
      },
      {
        to: "transcriptions",
        label: "nav.transcriptions",
        icon: Captions,
        keywords: "字幕 转写 语音 识别 ASR SRT transcript",
      },
      {
        to: "tasks",
        label: "nav.tasks",
        icon: Activity,
        keywords: "任务 进度 失败 重试 task job retry",
      },
    ],
  },
  {
    title: "nav.admin",
    items: [
      {
        to: "members",
        label: "nav.members",
        icon: Users,
        keywords: "成员 权限 协作 member permission",
      },
      {
        to: "settings",
        label: "nav.settings",
        icon: Settings,
        keywords: "设置 模型 供应商 配额 settings model provider quota",
      },
    ],
  },
];

export const WORKSPACE_DESTINATIONS = [
  ...WORKSPACE_GROUPS.flatMap((group) => group.items),
  {
    to: "assets/trash",
    label: "nav.trash",
    icon: Trash2,
    keywords: "回收站 删除 恢复 trash restore",
  },
];
