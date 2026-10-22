import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { GoogleAuthButton } from "@/components/GoogleAuthButton";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { clearGoogleAttempt, getGoogleAttempt, googleAuthError, safeAuthNext, startGoogleAuth } from "@/lib/google-auth";

export default function GoogleAuthComplete() {
  const { completeGoogleLogin, me } = useAuth();
  const { lang } = useI18n();
  const zh = lang === "zh";
  const location = useLocation();
  const navigate = useNavigate();
  const callbackError = new URLSearchParams(location.search).get("error");
  const [error, setError] = useState<unknown>(callbackError || null);
  const [busy, setBusy] = useState(false);
  const [attempt] = useState(getGoogleAttempt);

  useEffect(() => {
    if (callbackError) return;
    let active = true;
    void completeGoogleLogin().then((result) => {
      if (!active) return;
      clearGoogleAttempt();
      navigate(safeAuthNext(result.next), { replace: true });
    }).catch((reason: unknown) => {
      if (active) setError(reason);
    });
    // The shared promise owns the one-time exchange. Cleanup only detaches this view.
    return () => { active = false; };
  }, [callbackError, completeGoogleLogin, navigate]);

  async function retry() {
    if (busy) return;
    setBusy(true);
    try {
      await startGoogleAuth(attempt.next, attempt.mode);
    } catch (reason) {
      setError(reason);
      setBusy(false);
    }
  }

  return (
    <main className="google-auth-complete">
      <section className="google-auth-card" aria-busy={!error || busy}>
        <h1>{error ? (zh ? "Google 登录未完成" : "Google sign-in did not finish") : (zh ? "正在完成 Google 登录" : "Completing Google sign-in")}</h1>
        {error ? (
          <>
            <p role="alert">{googleAuthError(error, zh)}</p>
            <div className="google-auth-actions">
              <GoogleAuthButton loading={busy} onClick={() => void retry()}>
                {zh ? "重试 Google 登录" : "Try Google again"}
              </GoogleAuthButton>
              <Link className="underline underline-offset-4" to={attempt.mode === "link" && me ? attempt.next : `/login?next=${encodeURIComponent(attempt.next)}`} replace>
                {attempt.mode === "link" && me ? (zh ? "返回账户设置" : "Back to account settings") : (zh ? "返回登录" : "Back to sign in")}
              </Link>
            </div>
          </>
        ) : (
          <p role="status" className="flex items-center gap-3 text-muted-foreground">
            <Loader2 size={20} className="animate-spin" />
            {zh ? "正在确认身份，请稍候…" : "Confirming your identity. Please wait…"}
          </p>
        )}
      </section>
    </main>
  );
}
