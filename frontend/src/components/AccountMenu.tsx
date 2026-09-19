import {
  Languages,
  LogOut,
  Moon,
  Sun,
  UserRound,
  ChevronDown,
} from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
import { Avatar } from "@/components/ui/avatar";
import { Modal } from "@/components/ui/modal";
import { HeaderMenu, headerMenuItem } from "@/components/ui/header-menu";

export function AccountMenu() {
  const { me, logout } = useAuth(),
    { lang, setLang } = useI18n(),
    { theme, toggle } = useTheme();
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
      <HeaderMenu
        label={zh ? "用户菜单" : "Account menu"}
        trigger={
          <>
            <Avatar name={me?.user.display_name} size={28} />
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
          {zh ? "账户信息" : "Account information"}
        </button>
        <button
          role="menuitem"
          className={headerMenuItem}
          onClick={() => show("preferences")}
        >
          <Languages size={16} />
          {zh ? "外观与语言" : "Appearance & language"}
        </button>
        <div className="border-t mt-1 pt-1">
          <button role="menuitem" className={headerMenuItem} onClick={logout}>
            <LogOut size={16} />
            {zh ? "退出登录" : "Sign out"}
          </button>
        </div>
      </HeaderMenu>
      <Modal
        open={panel === "profile" || panel === "preferences"}
        onClose={() => show(null)}
        title={zh ? "账户与偏好" : "Account & preferences"}
        width={560}
      >
        <div
          className="flex gap-2 mb-6"
          aria-label={zh ? "账户设置分类" : "Account sections"}
        >
          {[
            ["profile", zh ? "账户信息" : "Account information"],
            ["preferences", zh ? "外观与语言" : "Appearance & language"],
          ].map(([key, name]) => (
            <button
              key={key}
              className={`rounded-lg px-3 py-2 text-sm ${panel === key ? "bg-primary/10 text-primary" : "hover:bg-elevated text-muted-foreground"}`}
              aria-pressed={panel === key}
              onClick={() => show(key)}
            >
              {name}
            </button>
          ))}
        </div>
        {panel === "profile" ? (
          <div className="space-y-6">
            <div className="flex items-center gap-4">
              <Avatar name={me?.user.display_name} size={48} />
              <div className="min-w-0">
                <h2 className="text-lg font-semibold break-words">
                  {me?.user.display_name}
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  {zh ? "当前登录账户" : "Signed-in account"}
                </p>
              </div>
            </div>
            <dl className="rounded-xl border divide-y text-sm">
              <div className="flex gap-4 justify-between p-4">
                <dt className="shrink-0 text-muted-foreground">
                  {zh ? "用户名" : "Username"}
                </dt>
                <dd className="break-all">{me?.user.username}</dd>
              </div>
              <div className="flex gap-4 justify-between p-4">
                <dt className="shrink-0 text-muted-foreground">
                  {zh ? "显示名称" : "Display name"}
                </dt>
                <dd className="break-all">{me?.user.display_name}</dd>
              </div>
            </dl>
          </div>
        ) : (
          <div className="space-y-6">
            <p className="text-sm leading-6 text-muted-foreground">
              {zh
                ? "自动保存到当前浏览器，在不同项目间共用。"
                : "Saved automatically in this browser and shared across projects."}
            </p>
            <div>
              <h2 className="text-sm font-medium mb-3">
                {zh ? "界面主题" : "Theme"}
              </h2>
              <div className="grid grid-cols-2 gap-3">
                {(["dark", "light"] as const).map((value) => (
                  <button
                    key={value}
                    className={`flex gap-2 justify-center items-center rounded-xl border p-4 text-sm ${theme === value ? "border-primary bg-primary/10 text-primary" : "hover:bg-elevated"}`}
                    aria-pressed={theme === value}
                    onClick={() => {
                      if (theme !== value) toggle();
                    }}
                  >
                    {value === "dark" ? <Moon size={18} /> : <Sun size={18} />}
                    {zh
                      ? value === "dark"
                        ? "深色"
                        : "浅色"
                      : value === "dark"
                        ? "Dark"
                        : "Light"}
                  </button>
                ))}
              </div>
            </div>
            <label className="block text-sm font-medium">
              {zh ? "界面语言" : "Language"}
              <select
                aria-label={zh ? "界面语言" : "Language"}
                className="mt-3 block w-full rounded-lg border bg-bg p-3 font-normal"
                value={lang}
                onChange={(e) => setLang(e.target.value as "zh" | "en")}
              >
                <option value="zh">简体中文</option>
                <option value="en">English</option>
              </select>
            </label>
          </div>
        )}
      </Modal>
    </>
  );
}
