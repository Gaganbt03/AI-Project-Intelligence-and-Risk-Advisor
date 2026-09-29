// Central typed API layer. All backend access flows through here.

const BASE = ''; // vite proxy handles /api -> backend

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = message;
  }
}

export function getToken(): string | null {
  return localStorage.getItem('apir_token');
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem('apir_token', token);
  else localStorage.removeItem('apir_token');
}

async function handle<T>(res: Response): Promise<T> {
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    const msg = body?.detail || body?.error || `Request failed (${res.status})`;
    throw new ApiError(res.status, typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return body as T;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, { ...options, headers });
  return handle<T>(res);
}

/** Role of a provider inside the ordered fallback chain. */
export type ProviderRole = 'primary' | 'fallback_1' | 'fallback_2' | 'fallback_3' | 'fallback_4' | 'extra';

/**
 * Lifecycle of a provider slot.
 * - `not_configured`  no base URL / no API key
 * - `discovering`     key present, model not resolved yet (discovered on first use)
 * - `online`          model resolved and the probe succeeded
 * - `offline`         model resolved but the provider is not answering
 * - `unavailable`     circuit breaker open, waiting for cooldown
 */
export type ProviderState = 'not_configured' | 'discovering' | 'online' | 'offline' | 'unavailable';

/** Where the model id came from: pinned in env, or auto-discovered from the API. */
export type ProviderModelSource = 'configured' | 'discovered' | 'none';

/**
 * One row of the provider chain returned by GET /api/admin/settings/ai-providers.
 * NOTE: the backend never returns an API key — only `key_configured` (boolean)
 * and `key_masked` (a fixed redaction with no characters of the secret).
 */
export interface AiProviderInfo {
  key: string;
  name: string;
  kind: 'ollama' | 'external';
  index: number;
  role: ProviderRole;
  order: number;
  model: string;
  model_available: boolean;
  model_source: ProviderModelSource;
  configured: boolean;
  in_chain: boolean;
  key_configured: boolean;
  key_masked: string;
  healthy: boolean;
  breaker_open: boolean;
  state: ProviderState;
  detail: string;
  primary: boolean;
}

export interface AiEmbeddingInfo {
  provider: string;
  model: string;
  dimension?: number;
  healthy: boolean;
  detail?: string;
}

export interface AiProviderStatus {
  providers: AiProviderInfo[];
  embedding: AiEmbeddingInfo;
}

export interface ProviderTestResult {
  key: string;
  name: string;
  role: ProviderRole;
  model: string;
  model_available: boolean;
  model_source: ProviderModelSource;
  configured: boolean;
  healthy: boolean;
  state: ProviderState;
  key_configured: boolean;
  key_masked: string;
  detail: string;
}

export const api = {
  // Auth
  authStatus: () => request<{ needs_setup: boolean }>('/api/auth/status'),
  setupAdmin: (payload: { name: string; email: string; password: string }) =>
    request<any>('/api/auth/setup-admin', { method: 'POST', body: JSON.stringify(payload) }),
  login: (email: string, password: string) =>
    request<any>('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  logout: () => request<any>('/api/auth/logout', { method: 'POST' }),
  me: () => request<any>('/api/auth/me'),
  changePassword: (old_password: string, new_password: string) =>
    request<any>('/api/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ old_password, new_password }),
    }),

  // Users (admin)
  listUsers: () => request<any[]>('/api/users'),
  createUser: (payload: any) => request<any>('/api/users', { method: 'POST', body: JSON.stringify(payload) }),
  updateUser: (id: number, payload: any) => request<any>(`/api/users/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  setUserProjects: (id: number, project_ids: number[]) =>
    request<any>(`/api/users/${id}/projects`, { method: 'PUT', body: JSON.stringify({ project_ids }) }),
  resetPassword: (id: number, password: string) =>
    request<any>(`/api/users/${id}/reset-password`, { method: 'POST', body: JSON.stringify({ password }) }),

  // Projects
  listProjects: () => request<any[]>('/api/projects'),
  getProject: (id: number) => request<any>(`/api/projects/${id}`),
  createProject: (payload: any) => request<any>('/api/projects', { method: 'POST', body: JSON.stringify(payload) }),
  updateProject: (id: number, payload: any) => request<any>(`/api/projects/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  archiveProject: (id: number) => request<any>(`/api/projects/${id}`, { method: 'DELETE' }),

  // Documents
  listDocuments: (params: Record<string, any> = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
    });
    const q = qs.toString();
    return request<any[]>(`/api/documents${q ? `?${q}` : ''}`);
  },
  getDocument: (id: number) => request<any>(`/api/documents/${id}`),
  previewDocument: (id: number) => request<any>(`/api/documents/${id}/preview`),
  downloadUrl: (id: number) => `/api/documents/${id}/download`,
  reprocessDocument: (id: number) => request<any>(`/api/documents/${id}/reprocess`, { method: 'POST' }),
  deleteDocument: (id: number) => request<any>(`/api/documents/${id}`, { method: 'DELETE' }),

  uploadDocument: async (projectId: number, file: File, onProgress?: (pct: number) => void): Promise<any> => {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const token = getToken();
      xhr.open('POST', `${BASE}/api/documents/upload?project_id=${projectId}`);
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
      };
      xhr.onload = () => {
        let body: any = null;
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          /* ignore */
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body);
        else {
          reject(new ApiError(xhr.status, body?.detail || `Upload failed (${xhr.status})`));
        }
      };
      xhr.onerror = () => reject(new ApiError(0, 'Network error during upload.'));
      const fd = new FormData();
      fd.append('file', file);
      xhr.send(fd);
    });
  },

  // Risks
  listRisks: (projectId: number) => request<any[]>(`/api/risks?project_id=${projectId}`),
  createRisk: (projectId: number, payload: any) =>
    request<any>(`/api/risks?project_id=${projectId}`, { method: 'POST', body: JSON.stringify(payload) }),
  updateRisk: (id: number, payload: any) => request<any>(`/api/risks/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),

  // Blockers
  listBlockers: (projectId: number) => request<any[]>(`/api/blockers?project_id=${projectId}`),
  reportBlocker: (projectId: number, payload: any) =>
    request<any>(`/api/blockers?project_id=${projectId}`, { method: 'POST', body: JSON.stringify(payload) }),
  updateBlocker: (id: number, payload: any) => request<any>(`/api/blockers/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),

  // Tasks
  listTasks: (params: Record<string, any> = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
    });
    const q = qs.toString();
    return request<any[]>(`/api/tasks${q ? `?${q}` : ''}`);
  },
  createTask: (projectId: number, payload: any) =>
    request<any>(`/api/tasks?project_id=${projectId}`, { method: 'POST', body: JSON.stringify(payload) }),
  updateTask: (id: number, payload: any) => request<any>(`/api/tasks/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),

  // AI Insights
  listInsights: (projectId: number) => request<any[]>(`/api/projects/${projectId}/insights`),
  projectHealth: (projectId: number) => request<any>(`/api/projects/${projectId}/insights/health`),
  runAnalysis: (projectId: number) => request<any>(`/api/projects/${projectId}/insights`, { method: 'POST' }),
  aiRuns: (projectId: number) => request<any[]>(`/api/projects/${projectId}/insights/runs`),

  // Assistant
  askAssistant: (projectId: number, question: string) =>
    request<any>(`/api/projects/${projectId}/assistant`, { method: 'POST', body: JSON.stringify({ question }) }),

  // Dashboards
  adminDashboard: () => request<any>('/api/admin/dashboard'),
  meDashboard: () => request<any>('/api/me/dashboard'),

  // Settings
  generalSettings: () => request<any>('/api/settings/general'),
  aiProviderStatus: () => request<AiProviderStatus>('/api/admin/settings/ai-providers'),
  testProvider: (key: string) => request<ProviderTestResult>(`/api/admin/settings/ai-providers/${key}/test`, { method: 'POST' }),
  adminGeneral: () => request<any>('/api/admin/settings/general'),
  updateGeneral: (payload: any) => request<any>('/api/admin/settings/general', { method: 'PUT', body: JSON.stringify(payload) }),

  // Audit
  auditLogs: (params: Record<string, any> = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
    });
    const q = qs.toString();
    return request<any>(`/api/admin/audit-logs${q ? `?${q}` : ''}`);
  },
};

export async function downloadWithAuth(url: string, filename: string) {
  const token = getToken();
  const res = await fetch(`${BASE}${url}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    let msg = `Download failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) msg = body.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  const blob = await res.blob();
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename || 'download';
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}