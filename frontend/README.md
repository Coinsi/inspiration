# Inspiration 前端

> Inspiration 的 Web 端：**Vite + React 18 + TypeScript + Tailwind CSS + TanStack Query + React Router**。包含浅色官网与博客、支持明暗主题的创作工作区，以及独立的平台内容管理页。

[项目介绍](../README.md) · [安装指南](../docs/GETTING_STARTED.md) · [官网管理](../docs/61-产品官网与内容管理.md) · [博客管理](../docs/62-博客与创作手记.md)

---

## 一、技术栈

| 关注点 | 选型 |
|---|---|
| 构建 | Vite 5 |
| UI | React 18 + TypeScript |
| 样式 | Tailwind CSS + shadcn/ui 风格自写组件(`components/ui/`) |
| 数据 | TanStack Query(React Query):查询缓存、失效刷新、轮询 |
| 路由 | React Router 6(受保护路由 + 项目级 AppShell) |
| 图标 | lucide-react |
| 国际化 | 自写轻量 i18n(中 / 英,`lib/i18n.tsx`) |

## 二、目录结构

```
src/
├─ main.tsx              # 入口:QueryClient / Router / Auth / Theme / i18n Provider
├─ App.tsx               # 路由表 + 受保护路由
├─ lib/
│  ├─ api.ts             # API 客户端(JWT、统一错误、类型定义、blobUrl)
│  ├─ auth.tsx           # 认证上下文
│  ├─ i18n.tsx           # 中英双语
│  ├─ theme.tsx          # 主题切换
│  └─ usePersistentState # localStorage 持久化 state
├─ components/
│  ├─ AppShell.tsx       # 项目内主框架(侧边栏导航 + 内容区)
│  ├─ AssistPanel.tsx    # 贯穿式 AI 对话助手侧栏(聊天 + 修改提议 + 应用)
│  ├─ AssetGraph.tsx     # 资产关系/出场气泡图
│  ├─ GenerationPanel.tsx# 生成面板(供应商选择 / 参考图 / 变体钦定)
│  ├─ ImageViewer.tsx    # 图片灯箱(点开看大图 + 下载)
│  ├─ BackgroundTasks.tsx# 全局后台任务(AI 拆解等)角标
│  └─ ui/                # shadcn 风格基础组件(button/input/card/modal/tabs…)
└─ pages/                # 见下
```

## 三、主要页面(`src/pages/`)

| 页面 | 说明 |
|---|---|
| `Login` / `Projects` / `ProjectMembers` | 登录、项目列表/新建、成员授权 |
| `Workbench` | **工作台**:一站式概览(脚本/角色/分镜/成片预览),支持**按小说+章节筛选** |
| `Narrative` | 拆解工作台:小说导入、章节阅读、AI 生成剧本入口、剧本列表 |
| `Scripts` / `ScriptEditor` | **剧本库** + **剧本编辑器**(分块编辑 / 块工具栏 / 大纲 / 数据统计 / Fountain 导出 / 侧栏 AI 助手 / 派生场次镜头与资产) |
| `StoryBible` | **设定库(故事圣经)**:AI 通读提取(并发 + 章节范围)、分类导航、搜索/章节筛选、转资产 |
| `Assets` / `AssetDetail` / `Trash` | 资产库(**卡片 / 图谱 / 列表** 三视图 + 批量删)、资产详情(信息/参考图/AI 概念图/AI 助手/版本)、回收站 |
| `Prompts` | 提示词与片段库 |
| `Shots` / `Storyboard` | 镜头看板、分镜工作台 |
| `Cuts` | 成片 / 基线 |
| `Settings` | 生成供应商(即梦 / GPT Image)、AI 拆解 LLM、配额配置 |

## 四、运行

```bash
npm install
npm run dev          # http://127.0.0.1:5173(/api 已代理到后端 :8000)
npm run build        # 类型检查 + 生产构建(tsc -b && vite build)
npm run lint
```

> **访问用 `127.0.0.1` 而非 `localhost`**:dev server 绑定 IPv4,`localhost` 可能解析到 IPv6 ::1 连不上。
> 守护运行见 `run-daemon.ps1`(vite 退出自动重启,由计划任务托管脱离会话)。
> 默认账号 **demo / demo1234**。

## 五、约定与要点

- **API 客户端**(`lib/api.ts`):`api.get/post/put/patch/del` 自动带 JWT、解析后端统一错误;`apiUpload` 走 multipart;`blobUrl(projectId, hash)` 生成带 token 的媒体地址。
- **国际化**:所有文案走 `t("key")`,中英两份字典都要补;`tsc -b` 必须零错误。
- **图片**:画廊类图片用 `<ZoomableImage>`(点开看大图 + 下载),见 `components/ImageViewer.tsx`。
- **AI 助手**:`<AssistPanel target_type target_id>` 可挂在任意受支持对象上,对话产出结构化修改提议,确认后应用并产生版本快照。
- **持久化偏好**:视图选择、筛选、折叠状态等用 `usePersistentState` 存 localStorage。
