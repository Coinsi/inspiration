import { Clapperboard, Languages, Moon, Sun } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";

export default function Login() {
  const { login } = useAuth();
  const { t, lang, setLang } = useI18n();
  const { theme, toggle } = useTheme();
  const navigate = useNavigate();
  const [username, setUsername] = useState("demo");
  const [password, setPassword] = useState("demo1234");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      navigate("/projects");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("login.failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-transparent relative overflow-hidden">
      {/* 语言 / 主题切换 */}
      <div className="absolute right-5 top-5 flex items-center gap-1.5">
        <button onClick={() => setLang(lang === "zh" ? "en" : "zh")} className="grid h-8 w-8 place-items-center rounded-md border border-border bg-card text-muted-foreground  hover:text-foreground" title={t("shell.language")}>
          <Languages className="h-4 w-4" />
        </button>
        <button onClick={toggle} className="grid h-8 w-8 place-items-center rounded-md border border-border bg-card text-muted-foreground  hover:text-foreground" title={t("shell.theme")}>
          {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>

      <div className="relative w-[380px] max-w-[calc(100%-2rem)] rounded-2xl border border-border glass  p-8 shadow-panel">
        <div className="flex items-center gap-2.5 mb-1">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-primary text-primary-foreground ">
            <Clapperboard className="h-5 w-5" />
          </span>
          <span className="text-lg font-semibold tracking-tight">Inspiration</span>
        </div>
        <p className="text-sm text-muted-foreground mb-6">{t("app.tagline")}</p>

        <form onSubmit={onSubmit} className="space-y-3">
          <Input placeholder={t("login.username")} value={username} onChange={(e) => setUsername(e.target.value)} />
          <Input
            type="password"
            placeholder={t("login.password")}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {error && <p className="text-sm text-danger">{error}</p>}
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? t("login.submitting") : t("login.submit")}
          </Button>
        </form>
        <p className="text-xs text-muted-foreground text-center mt-5">{t("login.demo")}</p>
      </div>
    </div>
  );
}
