import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { api, type SessionInfo } from '../api/client';

/**
 * Anonymous temporary-workspace bootstrap.
 *
 * The backend has no login: a browser is bound to exactly one private,
 * ephemeral project through an HttpOnly session cookie. This provider calls
 * `POST /api/session/start` once on load (the backend reuses the existing
 * cookie, so reloads are cheap) and exposes the workspace's project id to the
 * whole app. `session = project` is the isolation boundary, so every scoped
 * screen derives its project from here instead of picking one.
 */
interface WorkspaceCtx {
  projectId: number | null;
  session: SessionInfo | null;
  loading: boolean;
  error: string | null;
  retry: () => void;
  finish: () => Promise<void>;
}

const Ctx = createContext<WorkspaceCtx>({
  projectId: null,
  session: null,
  loading: true,
  error: null,
  retry: () => {},
  finish: async () => {},
});

// React StrictMode double-invokes effects in development. Sharing one in-flight
// promise guarantees both invocations resolve to the *same* session instead of
// racing two `POST /session/start` calls and creating two workspaces.
let startPromise: Promise<SessionInfo> | null = null;

function beginSession(): Promise<SessionInfo> {
  if (!startPromise) startPromise = api.startSession();
  return startPromise;
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    beginSession()
      .then((s) => {
        if (active) setSession(s);
      })
      .catch((e: any) => {
        if (active) setError(e?.message || 'Could not start a workspace session.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [tick]);

  // Keep the idle TTL alive while the tab is open. The closing signal that ends
  // the session early is handled separately.
  useEffect(() => {
    if (!session) return;
    const id = window.setInterval(() => {
      api.sessionHeartbeat().catch(() => {});
    }, 5 * 60 * 1000);
    return () => window.clearInterval(id);
  }, [session]);

  // Best-effort browser-close signal. `pagehide` with persisted === false means
  // the page is really going away (not entering the bfcache), so we ask the
  // backend to arm a short grace purge. A reload re-hits /session/start inside
  // that window and cancels it, so refreshing never loses work.
  useEffect(() => {
    const onPageHide = (event: PageTransitionEvent) => {
      if (event.persisted) return;
      try {
        navigator.sendBeacon('/api/session/close');
      } catch {
        /* unload context: nothing actionable */
      }
    };
    window.addEventListener('pagehide', onPageHide);
    return () => window.removeEventListener('pagehide', onPageHide);
  }, []);

  const retry = useCallback(() => {
    startPromise = null;
    setTick((t) => t + 1);
  }, []);

  // Explicit "Finish & Clear Session": the backend deletes everything now, then
  // we hard-reload so a brand-new, empty workspace is started and no stale
  // component state survives.
  const finish = useCallback(async () => {
    try {
      await api.finishSession();
    } catch {
      /* even if the call fails the backend TTL sweeper will reclaim the data */
    }
    startPromise = null;
    window.location.assign('/');
  }, []);

  const value = useMemo<WorkspaceCtx>(
    () => ({ projectId: session?.project_id ?? null, session, loading, error, retry, finish }),
    [session, loading, error, retry, finish],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkspace() {
  return useContext(Ctx);
}
