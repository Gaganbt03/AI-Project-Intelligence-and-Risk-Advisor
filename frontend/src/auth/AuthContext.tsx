import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { api, setToken } from '../api/client';

export interface SessionUser {
  id: number;
  email: string;
  name: string;
  is_active: boolean;
}

interface AuthCtx {
  user: SessionUser | null;
  loading: boolean;
  needsSetup: boolean | null;
  login: (email: string, password: string) => Promise<SessionUser>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const Ctx = createContext<AuthCtx>({
  user: null,
  loading: true,
  needsSetup: null,
  login: async () => ({ id: 0, email: '', name: '', is_active: true }),
  logout: async () => {},
  refresh: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const [st, me] = await Promise.all([api.authStatus(), api.me()]);
      setNeedsSetup(st.needs_setup);
      setUser(me);
    } catch {
      setUser(null);
      try {
        const st = await api.authStatus();
        setNeedsSetup(st.needs_setup);
      } catch {
        setNeedsSetup(true);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, tick]);

  const login = useCallback(async (email: string, password: string) => {
    const res = await api.login(email, password);
    setToken(res.access_token);
    const me = res.user;
    setUser(me);
    setNeedsSetup(false);
    return me as SessionUser;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      /* ignore */
    }
    setToken(null);
    setUser(null);
    setTick((t) => t + 1);
  }, []);

  const value = useMemo(
    () => ({ user, loading, needsSetup, login, logout, refresh }),
    [user, loading, needsSetup, login, logout, refresh],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  return useContext(Ctx);
}