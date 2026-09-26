import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api, clearToken, getToken, setToken, type Me } from "./api";
import { useQueryClient } from "@tanstack/react-query";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
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

  async function refresh() {
    if (!getToken()) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      setMe(await api.get<Me>("/me"));
    } catch {
      clearToken();
      setMe(null);
    } finally {
      setLoading(false);
    }
  }

  async function login(username: string, password: string) {
    const { access_token } = await api.post<{ access_token: string }>(
      "/auth/login",
      {
        username,
        password,
      },
    );
    setToken(access_token);
    queryClient.clear();
    await refresh();
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AuthContext.Provider
      value={{ me, loading, login, logout, logoutError, refresh }}
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
