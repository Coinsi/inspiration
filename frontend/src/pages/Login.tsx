import {
  ArrowRight,
  Clapperboard,
  Eye,
  EyeOff,
  Languages,
  Loader2,
  LockKeyhole,
  Moon,
  Sun,
  UserRound,
} from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
import { LoginBackground } from "@/components/LoginBackground";

export default function Login() {
  const { login } = useAuth();
  const { t, lang, setLang } = useI18n(),
    zh = lang === "zh";
  const { theme, toggle } = useTheme();
  const navigate = useNavigate();
  const [username, setUsername] = useState("demo");
  const [password, setPassword] = useState("demo1234");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !username.trim() || !password) return;
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
      navigate("/projects");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("login.failed"));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-scene">
      <section
        className="login-art"
        aria-label={zh ? "创作灵感" : "Creative inspiration"}
      >
        <LoginBackground />
        <div className="login-brand">
          <span className="studio-brand-mark">
            <Clapperboard size={23} />
          </span>
          <span>
            Inspiration
            <span className="block text-[10px] font-normal tracking-[.24em] text-white/60">
              CREATIVE STUDIO
            </span>
          </span>
        </div>
        <div className="login-story">
          <p className="text-[11px] tracking-[.24em] text-white/60">
            FROM IMAGINATION TO FRAME
          </p>
          <h1>
            {zh ? (
              <>
                让脑海中的画面，
                <br />
                成为你的作品。
              </>
            ) : (
              <>
                Bring your imagination
                <br />
                into the frame.
              </>
            )}
          </h1>
          <p>
            {zh
              ? "从一个想法，到一个完整的故事。"
              : "From the first idea to a story of your own."}
            <br />
            {zh
              ? "在这里，开始你的下一场创作。"
              : "Your next creative chapter starts here."}
          </p>
          <div className="login-frame-line">
            <span>INSPIRATION</span>
            <span />
            <span>{zh ? "让灵感开始流动" : "LET YOUR IDEAS FLOW"}</span>
          </div>
        </div>
      </section>
      <section className="login-pane">
        <div className="login-appearance">
          <button
            onClick={() => setLang(zh ? "en" : "zh")}
            className="studio-icon-button"
            aria-label={t("shell.language")}
          >
            <Languages size={18} />
          </button>
          <button
            onClick={toggle}
            className="studio-icon-button"
            aria-label={t("shell.theme")}
          >
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </div>
        <div className="login-form-wrap">
          <p className="text-[11px] tracking-[.2em] text-faint">
            WELCOME TO INSPIRATION
          </p>
          <h2>{zh ? "回到创作现场" : "Back to your studio"}</h2>
          <p className="mt-3 text-sm text-muted-foreground">
            {zh
              ? "你的故事、素材与灵感，都在这里。"
              : "Your stories, references and ideas are waiting."}
          </p>
          <form onSubmit={onSubmit} className="mt-9 space-y-5">
            <div>
              <label htmlFor="login-username" className="login-label">
                {t("login.username")}
              </label>
              <div className="login-field">
                <UserRound size={17} />
                <input
                  id="login-username"
                  autoComplete="username"
                  required
                  disabled={busy}
                  placeholder={t("login.username")}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </div>
            </div>
            <div>
              <label htmlFor="login-password" className="login-label">
                {t("login.password")}
              </label>
              <div className="login-field">
                <LockKeyhole size={17} />
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  disabled={busy}
                  placeholder={t("login.password")}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={
                    zh
                      ? showPassword
                        ? "隐藏密码"
                        : "显示密码"
                      : showPassword
                        ? "Hide password"
                        : "Show password"
                  }
                  aria-pressed={showPassword}
                  className="studio-icon-button"
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>
            {error && (
              <p
                role="alert"
                className="rounded-lg border border-danger/20 bg-danger/10 p-3 text-sm text-danger"
              >
                {error}
              </p>
            )}
            <Button
              type="submit"
              className="login-submit w-full"
              disabled={busy || !username.trim() || !password}
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {busy
                ? t("login.submitting")
                : zh
                  ? "进入工作台"
                  : "Enter your studio"}
              {!busy && <ArrowRight size={17} />}
            </Button>
          </form>
          <div className="login-demo">
            <span>{zh ? "体验账号" : "DEMO ACCOUNT"}</span>
            <p>{t("login.demo")}</p>
          </div>
        </div>
        <p className="login-footer">
          INSPIRATION <span>·</span>{" "}
          {zh ? "把灵感，变成作品" : "MAKE YOUR IDEAS REAL"}
        </p>
      </section>
    </main>
  );
}
