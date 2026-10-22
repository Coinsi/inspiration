import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, clearToken, getToken, setToken, type Me } from "./api";
import { useQueryClient } from "@tanstack/react-query";
import { exchangeGoogleSession, type GoogleAuthResult } from "./google-auth";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  completeGoogleLogin: () => Promise<Pick<GoogleAuthResult, "next" | "linked">>;
  logout: () => Promise<void>;
  logoutError: string;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [logoutError, setLogoutError] = useState("");
  const googleCompletion = useRef<Promise<Pick<GoogleAuthResult, "next" | "linked">> | null>(null);

  const refresh = useCallback(async () => {
    const requestedToken = getToken();
    if (!requestedToken) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      const result = await api.get<Me>("/me");
      if (getToken() === requestedToken) setMe(result);
    } catch {
      if (!getToken() || getToken() === requestedToken) {
        clearToken();
        setMe(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const acceptSession = useCallback(async (accessToken: string) => {
    setToken(accessToken);
    setMe(null);
    queryClient.clear();
    try {
      const result = await api.get<Me>("/me");
      if (getToken() !== accessToken) throw new Error("Session changed while signing in");
      setMe(result);
      setLogoutError("");
    } catch (error) {
      if (getToken() === accessToken) {
        clearToken();
        setMe(null);
      }
      throw error;
    } finally {
      setLoading(false);
    }
  }, [queryClient]);

  const completeGoogleLogin = useCallback(() => {
    googleCompletion.current ??= exchangeGoogleSession().then(async (result) => {
      await acceptSession(result.access_token);
      return { next: result.next, linked: result.linked };
    });
    return googleCompletion.current;
  }, [acceptSession]);

  async function login(email: string, password: string) {
    const { access_token } = await api.post<{ access_token: string }>(
      "/auth/login",
      {
        email: email.trim(),
        password,
      },
    );
    await acceptSession(access_token);
  }

  async function logout() {
    setLogoutError("");
    try {
      await api.post("/auth/logout");
      clearToken();
      setMe(null);
      queryClient.clear();
    } catch {
      if (!getToken()) setMe(null);
      else
        setLogoutError(
          "退出未完成，请检查网络后重试 / Sign out failed. Please retry.",
        );
    }
  }

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <AuthContext.Provider
      value={{ me, loading, login, completeGoogleLogin, logout, logoutError, refresh }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
