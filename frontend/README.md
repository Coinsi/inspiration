# Inspiration 前端

Vite + React + TypeScript + Tailwind + shadcn 风格组件 + React Query + React Router。

## 结构

```
src/
├─ main.tsx              # 入口(挂载 QueryClient / Router / Auth)
├─ App.tsx              # 路由 + 受保护路由
├─ lib/
│  ├─ api.ts            # API 客户端(JWT、统一错误)
│  ├─ auth.tsx          # 认证上下文
│  └─ utils.ts          # cn() 样式合并
├─ components/
│  ├─ Layout.tsx        # 顶栏布局
│  └─ ui/               # shadcn 风格基础组件(button/input/card)
└─ pages/               # Login / Projects / ProjectMembers
```

> shadcn/ui 哲学是"把组件源码复制进仓库"。`components/ui/` 即按此方式手写的基础组件;
> 后续可用 `npx shadcn@latest add <component>` 增量引入更多组件。

## 运行

```bash
npm install
npm run dev          # http://localhost:5173 (已配置 /api 代理到后端 :8000)
```

## M0 页面

- 登录(默认 demo / demo1234)
- 项目列表 / 新建
- 项目成员授权(按角色)
