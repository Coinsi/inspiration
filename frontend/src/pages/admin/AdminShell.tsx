import { useState, type MouseEvent } from "react";
import { NavLink, Outlet, Link, useNavigate } from "react-router-dom";
import {
  Activity,
  ArrowUpRight,
  BookOpen,
  LayoutDashboard,
  Server,
  Settings2,
  ShieldCheck,
  Users,
  Wallet,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { AccountMenu } from "@/components/AccountMenu";
import { useConfirm } from "@/components/ui/confirm";
import { AdminUnsavedContext } from "./navigation";
import "./admin.css";

const entries = [
  ["/admin", "概览", LayoutDashboard],
  ["/admin/users", "用户管理", Users],
  ["/admin/models", "模型服务", Server],
  ["/admin/tasks", "任务中心", Activity],
  ["/admin/quotas", "用量与额度", Wallet],
  ["/admin/website", "官网内容", BookOpen],
  ["/admin/blog", "博客文章", BookOpen],
  ["/admin/system", "系统状态", Settings2],
  ["/admin/audit", "操作记录", ShieldCheck],
] as const;

export default function AdminShell() {
  const { me } = useAuth();
  const [dirty, setDirty] = useState(false);
  const confirm = useConfirm(),
    navigate = useNavigate();
  const guard = async (e: MouseEvent) => {
    const link = (e.target as Element).closest<HTMLAnchorElement>("a[href]");
    if (
      !dirty ||
      !link ||
      link.target === "_blank" ||
      e.ctrlKey ||
      e.metaKey ||
      e.shiftKey ||
      e.button !== 0
    )
      return;
    const target = new URL(link.href);
    if (target.href === location.href) return;
    e.preventDefault();
    e.stopPropagation();
    if (
      !(await confirm({
        title: "离开当前编辑？",
        message: "当前修改尚未保存，离开后将丢失。",
        confirmText: "放弃修改并离开",
        danger: true,
      }))
    )
      return;
    setDirty(false);
    if (target.origin === location.origin)
      navigate(target.pathname + target.search + target.hash);
    else location.assign(target.href);
  };
  if (!me?.user.is_platform_admin)
    return (
      <main className="admin-denied">
        <ShieldCheck size={36} />
        <h1>此区域仅供平台管理员使用</h1>
        <p>项目管理员身份不包含平台管理权限。</p>
        <Link to="/projects">返回创作空间</Link>
      </main>
    );
  return (
    <AdminUnsavedContext.Provider value={setDirty}>
      <div className="admin-shell" onClickCapture={(e) => void guard(e)}>
        <aside className="admin-sidebar">
          <Link to="/admin" className="admin-brand">
            <ShieldCheck size={25} />
            <span>
              Inspiration<small>管理后台</small>
            </span>
          </Link>
          <nav aria-label="管理后台导航">
            {entries.map(([path, label, Icon], i) => (
              <div key={path}>
                {(i === 0 || i === 5 || i === 7) && (
                  <p className="admin-nav-group">
                    {i === 0 ? "平台运营" : i === 5 ? "内容发布" : "系统管理"}
                  </p>
                )}
                <NavLink to={path} end={path === "/admin"}>
                  <Icon size={17} />
                  <span>{label}</span>
                </NavLink>
              </div>
            ))}
          </nav>
          <div className="admin-sidebar-foot">
            <span className="admin-role">
              {me.user.platform_role === "admin" ? "平台管理员" : "超级管理员"}
            </span>
            <p>平台管理与创作空间独立</p>
          </div>
        </aside>
        <div className="admin-workspace">
          <header className="admin-topbar">
            <span>平台管理</span>
            <div>
              <Link to="/projects">
                创作空间 <ArrowUpRight size={15} />
              </Link>
              <Link to="/">
                查看官网 <ArrowUpRight size={15} />
              </Link>
              <AccountMenu />
            </div>
          </header>
          <div className="admin-content">
            <Outlet />
          </div>
        </div>
      </div>
    </AdminUnsavedContext.Provider>
  );
}
