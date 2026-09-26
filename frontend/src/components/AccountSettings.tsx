import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Camera,
  Check,
  Languages,
  LockKeyhole,
  Moon,
  Sun,
  UserRound,
} from "lucide-react";
import { api, apiUpload, setToken } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
import { Avatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { useConfirm } from "@/components/ui/confirm";
import "./account-settings.css";

export function AccountSettings(props: {
  panel: string | null;
  onSelect: (v: string) => void;
  onClose: () => void;
}) {
  if (!["profile", "security", "preferences"].includes(props.panel || ""))
    return null;
  return <SettingsContent {...props} />;
}

function SettingsContent({
  panel,
  onSelect,
  onClose,
}: {
  panel: string | null;
  onSelect: (v: string) => void;
  onClose: () => void;
}) {
  const { me, refresh } = useAuth(),
    { lang, setLang } = useI18n(),
    { theme, toggle } = useTheme();
  const zh = lang === "zh",
    confirm = useConfirm();
  const [name, setName] = useState(me?.user.display_name || "");
  const [bio, setBio] = useState(me?.user.bio || "");
  const [current, setCurrent] = useState(""),
    [password, setPassword] = useState(""),
    [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const security = useQuery({
    queryKey: ["account-security", me?.user.id],
    queryFn: () =>
      api.get<{
        has_password: boolean;
        identities: { id: string; provider: string }[];
      }>("/me/security"),
    enabled: panel === "security",
  });
  const dirty =
    name !== me?.user.display_name ||
    bio !== (me?.user.bio || "") ||
    !!current ||
    !!password ||
    !!repeat;
  async function close() {
    if (busy) return;
    if (
      dirty &&
      !(await confirm({
        title: zh ? "放弃未保存的修改？" : "Discard unsaved changes?",
        message: zh
          ? "你的个人设置还没有保存。"
          : "Your account changes have not been saved.",
        confirmText: zh ? "放弃修改" : "Discard",
      }))
    )
      return;
    onClose();
  }
  async function perform(action: () => Promise<void>, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(message);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const tabs = [
    { key: "profile", label: zh ? "个人资料" : "Profile", icon: UserRound },
    { key: "security", label: zh ? "账户安全" : "Security", icon: LockKeyhole },
    {
      key: "preferences",
      label: zh ? "外观与语言" : "Appearance",
      icon: Languages,
    },
  ];
  return (
    <Modal
      open
      onClose={() => void close()}
      title={zh ? "个人设置" : "Personal settings"}
      width={880}
    >
      <div className="account-settings">
        <nav
          className="account-settings-nav"
          aria-label={zh ? "个人设置分类" : "Settings sections"}
        >
          <div className="account-settings-person">
            <Avatar
              src={me?.user.avatar_data}
              name={me?.user.display_name}
              size={44}
            />
            <div>
              <strong>{me?.user.display_name}</strong>
              <span>@{me?.user.username}</span>
            </div>
          </div>
          {tabs.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              disabled={busy}
              aria-current={panel === key ? "page" : undefined}
              onClick={() => {
                onSelect(key);
                setError("");
                setNotice("");
              }}
            >
              <Icon size={17} />
              {label}
            </button>
          ))}
        </nav>
        <section className="account-settings-content">
          {error && (
            <p role="alert" className="account-message error">
              {error}
            </p>
          )}
          {notice && (
            <p role="status" className="account-message">
              <Check size={16} />
              {notice}
            </p>
          )}
          {panel === "profile" && (
            <>
              <h2>{zh ? "让大家认识你" : "Make yourself at home"}</h2>
              <p className="account-description">
                {zh
                  ? "头像和昵称会展示在你参与的项目中。"
                  : "Your avatar and name appear in your projects."}
              </p>
              <div className="account-avatar-row">
                <Avatar
                  src={me?.user.avatar_data}
                  name={me?.user.display_name}
                  size={76}
                />
                <div>
                  <label
                    className={`account-upload ${busy ? "opacity-50" : ""}`}
                  >
                    <Camera size={16} />
                    {zh ? "更换头像" : "Change avatar"}
                    <input
                      aria-label={zh ? "上传头像" : "Upload avatar"}
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      disabled={busy}
                      className="sr-only"
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        e.target.value = "";
                        if (!file) return;
                        if (file.size > 5 * 1024 * 1024) {
                          setError(
                            zh
                              ? "头像大小不能超过5 MB"
                              : "Maximum avatar size is 5 MB",
                          );
                          return;
                        }
                        void perform(
                          async () => {
                            const body = new FormData();
                            body.append("file", file);
                            await apiUpload("/me/avatar", body);
                            await refresh();
                          },
                          zh ? "头像已更新" : "Avatar updated",
                        );
                      }}
                    />
                  </label>
                  <p className="account-hint">
                    JPG / PNG / WebP ·{" "}
                    {zh
                      ? "最大5 MB，居中裁剪为正方形"
                      : "Up to 5 MB, cropped to square"}
                  </p>
                  {me?.user.avatar_data && (
                    <button
                      disabled={busy}
                      className="text-xs text-muted-foreground underline"
                      onClick={() =>
                        void perform(
                          async () => {
                            await api.del("/me/avatar");
                            await refresh();
                          },
                          zh ? "已移除头像" : "Avatar removed",
                        )
                      }
                    >
                      {zh ? "移除头像" : "Remove avatar"}
                    </button>
                  )}
                </div>
              </div>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void perform(
                    async () => {
                      await api.put("/me/profile", {
                        display_name: name.trim(),
                        bio: bio.trim(),
                      });
                      setName(name.trim());
                      setBio(bio.trim());
                      await refresh();
                    },
                    zh ? "个人资料已保存" : "Profile saved",
                  );
                }}
                className="account-form"
              >
                <label>
                  {zh ? "昵称" : "Display name"}
                  <input
                    required
                    maxLength={128}
                    value={name}
                    disabled={busy}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <label>
                  {zh ? "个人简介" : "About you"}
                  <textarea
                    rows={3}
                    maxLength={500}
                    value={bio}
                    disabled={busy}
                    onChange={(e) => setBio(e.target.value)}
                    placeholder={
                      zh
                        ? "你的创作方向、擅长的领域…"
                        : "What do you love creating?"
                    }
                  />
                </label>
                <div className="account-readonly">
                  <div>
                    <span>{zh ? "用户名" : "Username"}</span>
                    <strong>@{me?.user.username}</strong>
                  </div>
                  <div>
                    <span>{zh ? "注册邮箱" : "Account email"}</span>
                    <strong>{me?.user.email}</strong>
                  </div>
                </div>
                <p className="account-hint">
                  {zh
                    ? "用户名用于登录和加入项目。邮箱验证与变更暂未开放。"
                    : "Use your username to sign in and join projects. Email verification and changes are not yet available."}
                </p>
                <div className="account-footer">
                  <Button
                    type="submit"
                    disabled={
                      busy ||
                      !name.trim() ||
                      (name === me?.user.display_name &&
                        bio === (me?.user.bio || ""))
                    }
                  >
                    {busy
                      ? zh
                        ? "保存中…"
                        : "Saving…"
                      : zh
                        ? "保存修改"
                        : "Save changes"}
                  </Button>
                </div>
              </form>
            </>
          )}
          {panel === "security" && (
            <>
              <h2>{zh ? "账户安全" : "Account security"}</h2>
              <p className="account-description">
                {zh
                  ? "管理登录密码与已绑定的登录方式。"
                  : "Manage your password and connected sign-in methods."}
              </p>
              {security.isLoading ? (
                <p>{zh ? "正在加载…" : "Loading…"}</p>
              ) : security.error ? (
                <p role="alert">{security.error.message}</p>
              ) : (
                <>
                  {security.data?.has_password && (
                    <form
                      className="account-form"
                      onSubmit={(e) => {
                        e.preventDefault();
                        if (password !== repeat) {
                          setError(
                            zh
                              ? "两次输入的新密码不一致"
                              : "Passwords do not match",
                          );
                          return;
                        }
                        void perform(
                          async () => {
                            const result = await api.post<{
                              access_token: string;
                            }>("/me/password", {
                              current_password: current,
                              new_password: password,
                            });
                            setToken(result.access_token);
                            setCurrent("");
                            setPassword("");
                            setRepeat("");
                            await refresh();
                          },
                          zh
                            ? "密码已更新，其他设备需要重新登录"
                            : "Password updated. Other sessions have been signed out.",
                        );
                      }}
                    >
                      <label>
                        {zh ? "当前密码" : "Current password"}
                        <input
                          type="password"
                          autoComplete="current-password"
                          required
                          value={current}
                          disabled={busy}
                          onChange={(e) => setCurrent(e.target.value)}
                        />
                      </label>
                      <label>
                        {zh ? "新密码" : "New password"}
                        <input
                          type="password"
                          autoComplete="new-password"
                          required
                          minLength={8}
                          maxLength={72}
                          value={password}
                          disabled={busy}
                          onChange={(e) => setPassword(e.target.value)}
                        />
                      </label>
                      <label>
                        {zh ? "确认新密码" : "Confirm new password"}
                        <input
                          type="password"
                          autoComplete="new-password"
                          required
                          value={repeat}
                          disabled={busy}
                          onChange={(e) => setRepeat(e.target.value)}
                        />
                      </label>
                      <p className="account-hint">
                        {zh
                          ? "至少8位。修改后，其他设备的登录会失效。"
                          : "At least 8 characters. Other devices will be signed out."}
                      </p>
                      <div className="account-footer">
                        <Button
                          type="submit"
                          disabled={busy || !current || !password || !repeat}
                        >
                          {zh ? "更新密码" : "Update password"}
                        </Button>
                      </div>
                    </form>
                  )}
                  <div className="account-connections">
                    <h3>{zh ? "第三方登录" : "Connected accounts"}</h3>
                    {security.data?.identities.length ? (
                      security.data.identities.map((i) => (
                        <p key={i.id}>
                          {i.provider} · {zh ? "已绑定" : "Connected"}
                        </p>
                      ))
                    ) : (
                      <p className="account-description">
                        {zh
                          ? "暂未开通第三方登录，当前使用用户名和密码登录。"
                          : "Third-party sign-in is not enabled. Use your username and password."}
                      </p>
                    )}
                  </div>
                </>
              )}
            </>
          )}
          {panel === "preferences" && (
            <>
              <h2>{zh ? "熟悉的创作环境" : "Your creative environment"}</h2>
              <p className="account-description">
                {zh
                  ? "自动保存到当前浏览器，在不同项目间共用。"
                  : "Saved in this browser and shared across projects."}
              </p>
              <h3>{zh ? "界面主题" : "Theme"}</h3>
              <div className="account-theme-options">
                {(["dark", "light"] as const).map((value) => (
                  <button
                    key={value}
                    aria-pressed={theme === value}
                    onClick={() => {
                      if (theme !== value) toggle();
                    }}
                  >
                    {value === "dark" ? <Moon size={22} /> : <Sun size={22} />}
                    <span>
                      {value === "dark"
                        ? zh
                          ? "深色"
                          : "Dark"
                        : zh
                          ? "浅色"
                          : "Light"}
                    </span>
                    {theme === value && <Check size={16} />}
                  </button>
                ))}
              </div>
              <label className="account-language">
                {zh ? "界面语言" : "Language"}
                <select
                  value={lang}
                  onChange={(e) => setLang(e.target.value as "zh" | "en")}
                >
                  <option value="zh">简体中文</option>
                  <option value="en">English</option>
                </select>
              </label>
            </>
          )}
        </section>
      </div>
    </Modal>
  );
}
