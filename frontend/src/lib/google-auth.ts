import { api, ApiError } from "./api";

export interface AuthProviders {
  registration_enabled: boolean;
  providers: { id: string; name: string; registration_enabled: boolean }[];
}

export interface GoogleAuthResult {
  access_token: string;
  next: string;
  linked: boolean;
}

type GoogleAuthMode = "login" | "link";
const ATTEMPT_KEY = "inspiration_google_attempt";
let exchangePromise: Promise<GoogleAuthResult> | undefined;

/** Keep login destinations on the existing protected routes, even after URL normalization. */
export function safeAuthNext(requested: string | null | undefined): string {
  if (!requested || !requested.startsWith("/") || /[\\\u0000-\u001f]/.test(requested))
    return "/projects";
  try {
    const url = new URL(requested, "https://inspiration.invalid");
    if (
      url.origin !== "https://inspiration.invalid" ||
      !/^\/(?:projects(?:\/|$)|admin\/(?:website$|blog(?:\/|$)))/.test(url.pathname)
    )
      return "/projects";
    return url.pathname + url.search + url.hash;
  } catch {
    return "/projects";
  }
}

export function getGoogleAttempt(): { next: string; mode: GoogleAuthMode } {
  try {
    const attempt = JSON.parse(sessionStorage.getItem(ATTEMPT_KEY) || "null");
    return {
      next: safeAuthNext(attempt?.next),
      mode: attempt?.mode === "link" ? "link" : "login",
    };
  } catch {
    return { next: "/projects", mode: "login" };
  }
}

export function clearGoogleAttempt() {
  try {
    sessionStorage.removeItem(ATTEMPT_KEY);
  } catch {
    // Storage is optional; the server holds the authenticated return destination.
  }
}

export async function startGoogleAuth(next: string, mode: GoogleAuthMode = "login") {
  const destination = safeAuthNext(next);
  const result = await api.post<{ authorization_url: string }>("/auth/google/start", {
    next: destination,
    mode,
  });
  const url = new URL(result.authorization_url);
  if (url.origin !== "https://accounts.google.com" || url.username || url.password)
    throw new ApiError("google_unavailable", "Google sign-in is unavailable");
  try {
    sessionStorage.setItem(ATTEMPT_KEY, JSON.stringify({ next: destination, mode }));
  } catch {
    // Do not prevent sign-in when the browser disables session storage.
  }
  exchangePromise = undefined;
  window.location.assign(url.href);
}

/** One exchange per document load: StrictMode and remounts share the single-use cookie result. */
export function exchangeGoogleSession(): Promise<GoogleAuthResult> {
  exchangePromise ??= api.post<GoogleAuthResult>("/auth/google/exchange");
  return exchangePromise;
}

export function googleAuthError(error: unknown, zh: boolean): string {
  const code = error instanceof ApiError ? error.code : typeof error === "string" ? error : "";
  const messages: Record<string, [string, string]> = {
    google_cancelled: ["你已取消 Google 授权，可以重试或使用其他方式登录。", "Google sign-in was cancelled. Try again or use another sign-in method."],
    google_unavailable: ["Google 登录暂不可用，请稍后重试或使用账号密码登录。", "Google sign-in is unavailable. Try again later or use your password."],
    google_state_invalid: ["这次授权已过期或无法验证，请重新开始 Google 登录。", "This authorization expired or could not be verified. Start Google sign-in again."],
    google_token_invalid: ["无法验证 Google 身份，请重新登录。", "We could not verify your Google identity. Please sign in again."],
    google_network_error: ["暂时无法连接 Google，请检查网络后重试。", "We could not reach Google. Check your connection and try again."],
    google_registration_disabled: ["暂未开放 Google 注册。已有账号请先用密码登录，再到个人设置中绑定 Google。", "Google registration is closed. If you have an account, sign in with your password and connect Google in settings."],
    google_email_unverified: ["请先在 Google 验证邮箱，然后重新登录。", "Verify your email with Google, then sign in again."],
    google_email_conflict: ["这个邮箱已有账号。请先用原账号登录，再到个人设置 → 账户安全中绑定 Google。", "An account already uses this email. Sign in to that account first, then connect Google in Personal settings → Security."],
    google_identity_conflict: ["这个 Google 账号已绑定其他用户，请换一个 Google 账号。", "This Google account is already connected to another user. Choose a different Google account."],
    google_account_disabled: ["当前账号已停用，请联系管理员。", "This account is disabled. Please contact an administrator."],
    google_session_expired: ["登录状态已过期，请先重新登录，再绑定 Google。", "Your session expired. Sign in again before connecting Google."],
    google_exchange_invalid: ["这次登录已过期或已完成，请重新开始 Google 登录。", "This sign-in has expired or was already completed. Start Google sign-in again."],
    TOO_MANY_ATTEMPTS: ["尝试次数较多，请稍后重试。", "Too many attempts. Please try again later."],
    UNAUTHORIZED: ["登录状态已过期，请先重新登录。", "Your session expired. Please sign in again."],
    FORBIDDEN: ["当前无法完成 Google 登录，请重新登录后再试。", "Google sign-in could not be completed. Please sign in and try again."],
  };
  return messages[code]?.[zh ? 0 : 1] ?? (zh
    ? "Google 登录未完成，请重试或使用账号密码登录。"
    : "Google sign-in did not finish. Try again or sign in with your password.");
}
