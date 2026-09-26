import { Languages, LogOut, UserRound, ChevronDown } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { Avatar } from "@/components/ui/avatar";
import { AccountSettings } from "@/components/AccountSettings";
import { HeaderMenu, headerMenuItem } from "@/components/ui/header-menu";

export function AccountMenu() {
  const { me, logout, logoutError } = useAuth(),
    { lang } = useI18n();
  const zh = lang === "zh";
  const [params, setParams] = useSearchParams();
  const panel = params.get("account");
  const show = (value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set("account", value);
    else next.delete("account");
    setParams(next, { replace: true });
  };
  return (
    <>
      {logoutError && (
        <p role="alert" className="text-xs text-destructive max-w-64">
          {logoutError}
        </p>
      )}
      <HeaderMenu
        label={zh ? "用户菜单" : "Account menu"}
        trigger={
          <>
            <Avatar
              src={me?.user.avatar_data}
              name={me?.user.display_name}
              size={28}
            />
            <ChevronDown size={12} />
          </>
        }
      >
        <div className="px-3 py-3 border-b mb-1 min-w-0">
          <p className="truncate text-sm font-medium">
            {me?.user.display_name}
          </p>
          <p className="truncate mt-1 text-xs text-muted-foreground">
            @{me?.user.username}
          </p>
        </div>
        <button
          role="menuitem"
          className={headerMenuItem}
          onClick={() => show("profile")}
        >
          <UserRound size={16} />
          {zh ? "个人设置" : "Personal settings"}
        </button>
        <button
          role="menuitem"
          className={headerMenuItem}
          onClick={() => show("preferences")}
        >
          <Languages size={16} />
          {zh ? "外观与语言" : "Appearance & language"}
        </button>
        {me?.user.is_platform_admin && (
          <Link role="menuitem" className={headerMenuItem} to="/admin/website">
            <UserRound size={16} />
            {zh ? "平台管理" : "Platform admin"}
          </Link>
        )}
        <Link role="menuitem" className={headerMenuItem} to="/">
          <UserRound size={16} />
          {zh ? "产品官网" : "Website"}
        </Link>
        <div className="border-t mt-1 pt-1">
          <button role="menuitem" className={headerMenuItem} onClick={logout}>
            <LogOut size={16} />
            {zh ? "退出登录" : "Sign out"}
          </button>
        </div>
      </HeaderMenu>
      <AccountSettings
        panel={panel}
        onSelect={show}
        onClose={() => show(null)}
      />
    </>
  );
}
