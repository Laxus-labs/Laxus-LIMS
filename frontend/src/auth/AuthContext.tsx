import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, tokens } from "../api/client";
import type { Me } from "../api/types";

interface AuthContextValue {
  me: Me | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  async function loadMe() {
    try {
      setMe(await api.me());
    } catch {
      tokens.clear();
      setMe(null);
    }
  }

  useEffect(() => {
    // apiFetch transparently refreshes from the stored refresh token when
    // the (memory-only) access token is missing, so this is enough to
    // restore a session after a page reload.
    if (tokens.getRefresh()) {
      loadMe().finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function login(username: string, password: string) {
    const { access, refresh } = await api.login(username, password);
    tokens.setAccess(access);
    tokens.setRefresh(refresh);
    await loadMe();
  }

  function logout() {
    tokens.clear();
    setMe(null);
  }

  return (
    <AuthContext.Provider value={{ me, loading, login, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
