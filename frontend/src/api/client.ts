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

/* ------------------------------------------------------------------ *
 * Advanced analysis types: generated documents, health scoring,
 * conversational assistant and upload validation.
 * ------------------------------------------------------------------ */

export type GeneratedDocType = 'user_stories' | 'risk_register' | 'action_items';

/** One scored health dimension. `supported: false` means the data was missing. */
export interface HealthDimension {
  key: string;
  label: string;
  score: number;
  weight: number;
  supported: boolean;
  detail: string;
  facts: Record<string, unknown>;
}

export interface HealthFactor {
  type: string;
  text: string;
  evidence: string;
  severity?: string;
}

export interface HealthReport {
  project_id: number;
  project_name: string;
  formula_version: string;
  overall_score: number;
  status: string;
  dimensions: HealthDimension[];
  skipped_dimensions: string[];
  data_completeness: number;
  positives: HealthFactor[];
  negatives: HealthFactor[];
  recommendations: { priority: string; text: string; evidence: string; based_on: string }[];
  inputs: Record<string, unknown>;
  narrative: string;
  narrative_provider: string;
  narrative_model: string;
  has_analysis: boolean;
  last_analysis_at: string | null;
  snapshot_id?: number;
  computed_at?: string;
  /** Present on stored snapshots returned by /health/history and /health/latest. */
  id?: number;
  created_at?: string;
  formula?: HealthFormula;
  stored?: boolean;
}

export interface HealthFormula {
  version: string;
  overall: string;
  status_bands: string;
  dimensions: Record<string, { weight: number; required: boolean; formula: string }>;
  notes: string[];
}

/* ------------------------------------------------------------------ *
 * Automatic project analysis pipeline
 * ------------------------------------------------------------------ */

export type PipelineStageStatus = 'pending' | 'running' | 'done' | 'skipped' | 'warning' | 'failed';

export interface PipelineStage {
  key: string;
  label: string;
  status: PipelineStageStatus;
  detail: string;
  error: string;
  at: string | null;
}

/** Resolved project due date. Never invented — see the precedence rule. */
export interface DueDateInfo {
  effective_date: string;
  admin_date: string;
  document_date: string;
  source: string;
  evidence: string;
  document: string;
  conflict: boolean;
}

export interface PipelineHealth {
  overall_score: number | null;
  status: string;
  formula_version: string;
  data_completeness: number | null;
  created_at: string | null;
}

/**
 * Mirrors `app/services/pipeline.py:run_to_dict` exactly. Progress is derived
 * from `stages`, not sent as a number, so the UI can never report a percentage
 * the backend did not actually compute.
 */
export interface AnalysisStatus {
  project_id: number;
  running: boolean;
  status: string;
  trigger: string;
  current_stage: string;
  stages: PipelineStage[];
  counts: Record<string, number>;
  error: string;
  started_at: string | null;
  finished_at: string | null;
  due_date: DueDateInfo;
  health: PipelineHealth | null;
  assistant_ready: boolean;
}

/** Fraction of stages that reached a terminal state, as a whole percent. */
export function analysisProgress(status: AnalysisStatus): number {
  if (!status.stages.length) return 0;
  const settled = status.stages.filter((s) => s.status !== 'pending' && s.status !== 'running');
  return Math.round((settled.length / status.stages.length) * 100);
}

/** Label of the stage in flight, or of the last one that finished. */
export function currentStageLabel(status: AnalysisStatus): string {
  const running = status.stages.find((s) => s.status === 'running');
  if (running) return running.label;
  const match = status.stages.find((s) => s.key === status.current_stage);
  return match?.label ?? status.status;
}

export interface GeneratedDocMeta {
  id: number;
  project_id: number;
  doc_type: GeneratedDocType;
  title: string;
  file_name: string;
  file_type: string;
  generation_count: number;
  insufficient_evidence: boolean;
  provider: string;
  model: string;
  error: string | null;
  created_by: number | null;
  created_at: string;
  updated_at: string;
}

/** Validator verdict for a single field of a generated artifact. */
export type FieldVerdict = 'SUPPORTED' | 'MISSING' | 'CONFLICT' | 'UNSUPPORTED';

export interface ValidationFinding {
  field: string;
  verdict: FieldVerdict;
  value: string;
  source: string;
  note: string;
}

export interface ValidationSummary {
  status: string;
  fields_checked: number;
  supported: number;
  corrected: number;
  unsupported_removed: number;
  not_specified: number;
  conflicts: number;
  findings: ValidationFinding[];
}

/** Traceability of a due date: which value won and why. */
export interface DateTrace {
  effective_date: string;
  admin_date: string;
  document_date: string;
  source: string;
  evidence: string;
  document: string;
  conflict: boolean;
  vague_in_source: boolean;
}

/** One labelled field of an artifact, in the order the specification requires. */
export interface ArtifactField {
  key: string;
  label: string;
  value: unknown;
}

/** One user story, risk or action item, ready to render as a card. */
export interface GeneratedArtifact {
  id: string;
  title: string;
  fields: ArtifactField[];
  field_verdicts: Record<string, FieldVerdict>;
  conflicts: string[];
  due_date_trace: DateTrace | Record<string, never>;
}

export interface GeneratedDocPayload {
  doc_type: GeneratedDocType;
  title: string;
  project: string;
  artifacts: GeneratedArtifact[];
  validation: ValidationSummary;
  validation_notes: string[];
  notes?: string[];
  insufficient_evidence: boolean;
  reason?: string;
  [key: string]: unknown;
}

export interface GeneratedDoc extends GeneratedDocMeta {
  content: string;
  payload: GeneratedDocPayload;
}

export interface DocumentValidationReport {
  document_id: number;
  doc_type: GeneratedDocType;
  title: string;
  generated_at: string | null;
  summary: Omit<ValidationSummary, 'findings'>;
  findings: ValidationFinding[];
  notes: string[];
}

export interface AssistantSource {
  label: string;
  original_name: string;
  page: string | number;
  section: string;
  row: string | number;
  document_id: string | number;
}

export interface AssistantMessage {
  id: number;
  conversation_id: number;
  role: 'user' | 'assistant';
  content: string;
  sources: AssistantSource[];
  grounded: boolean;
  insufficient_evidence: boolean;
  used_structured_data: boolean;
  used_conversation_context: boolean;
  provider: string;
  model: string;
  error: string | null;
  created_at: string;
}

export interface AssistantConversation {
  id: number;
  project_id: number;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  messages?: AssistantMessage[];
}

export interface AssistantAnswer {
  answer: string;
  sources: AssistantSource[];
  grounded: boolean;
  insufficient_evidence: boolean;
  used_structured_data: boolean;
  used_conversation_context: boolean;
  provider: string;
  model: string;
  error: string;
  retrieval_error: string;
  retrieved_count: number;
  conversation_id?: number;
}

export interface ValidationCheck {
  check: string;
  passed: boolean;
  detail: string;
}

export interface ValidationReport {
  ok: boolean;
  extension: string;
  file_type: string;
  size: number;
  checks: ValidationCheck[];
  errors: string[];
  warnings: string[];
  detail: string;
  preview_only?: boolean;
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

  // Assistant
  askAssistant: (projectId: number, question: string) =>
    request<any>(`/api/projects/${projectId}/assistant`, { method: 'POST', body: JSON.stringify({ question }) }),

  /* ---------------------------------------------------------------- *
   * Milestone 3 — documentation generation
   * ---------------------------------------------------------------- */
  listGeneratedDocs: (projectId: number) =>
    request<{ project_id: number; summary: any; documents: GeneratedDocMeta[] }>(
      `/api/projects/${projectId}/documents`,
    ),
  getGeneratedDoc: (projectId: number, docId: number) =>
    request<GeneratedDoc>(`/api/projects/${projectId}/documents/${docId}`),
  generateDocs: (projectId: number, docType?: GeneratedDocType) =>
    request<{ generated: GeneratedDocMeta[]; failed?: number; summary: any }>(
      `/api/projects/${projectId}/documents/generate`,
      { method: 'POST', body: JSON.stringify({ doc_type: docType ?? null }) },
    ),
  regenerateDoc: (projectId: number, docId: number) =>
    request<GeneratedDoc>(`/api/projects/${projectId}/documents/${docId}/regenerate`, { method: 'POST' }),
  generatedDocDownloadUrl: (projectId: number, docId: number, format: 'md' | 'docx' = 'md') =>
    `/api/projects/${projectId}/documents/${docId}/download?format=${format}`,
  generatedDocValidation: (projectId: number, docId: number) =>
    request<DocumentValidationReport>(`/api/projects/${projectId}/documents/${docId}/validation`),

  /* ---------------------------------------------------------------- *
   * Automatic project analysis pipeline
   * ---------------------------------------------------------------- */
  projectAnalysisStatus: (projectId: number) =>
    request<AnalysisStatus>(`/api/projects/${projectId}/analysis/status`),
  runProjectAnalysis: (projectId: number) =>
    request<AnalysisStatus>(`/api/projects/${projectId}/analysis/run`, { method: 'POST' }),

  /* ---------------------------------------------------------------- *
   * Milestone 3 — deterministic health scoring
   * ---------------------------------------------------------------- */
  projectHealthScore: (projectId: number, opts: { explain?: boolean; persist?: boolean } = {}) => {
    const qs = new URLSearchParams();
    if (opts.explain === false) qs.set('explain', 'false');
    if (opts.persist === false) qs.set('persist', 'false');
    const q = qs.toString();
    return request<HealthReport>(`/api/projects/${projectId}/health${q ? `?${q}` : ''}`);
  },
  latestHealthScore: (projectId: number) => request<HealthReport>(`/api/projects/${projectId}/health/latest`),
  healthHistory: (projectId: number) =>
    request<{ project_id: number; history: HealthReport[] }>(`/api/projects/${projectId}/health/history`),
  healthFormula: (projectId: number) => request<HealthFormula>(`/api/projects/${projectId}/health/formula`),

  /* ---------------------------------------------------------------- *
   * Milestone 3 — conversational assistant
   * ---------------------------------------------------------------- */
  listConversations: (projectId: number) =>
    request<{ conversations: AssistantConversation[] }>(`/api/projects/${projectId}/assistant/conversations`),
  createConversation: (projectId: number, title = '') =>
    request<AssistantConversation>(`/api/projects/${projectId}/assistant/conversations`, {
      method: 'POST',
      body: JSON.stringify({ title }),
    }),
  getConversation: (projectId: number, conversationId: number) =>
    request<AssistantConversation>(`/api/projects/${projectId}/assistant/conversations/${conversationId}`),
  deleteConversation: (projectId: number, conversationId: number) =>
    request<{ ok: boolean }>(`/api/projects/${projectId}/assistant/conversations/${conversationId}`, {
      method: 'DELETE',
    }),
  askProjectAssistant: (projectId: number, payload: { question: string; conversation_id?: number | null; persist?: boolean }) =>
    request<AssistantAnswer>(`/api/projects/${projectId}/assistant/ask`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /* ---------------------------------------------------------------- *
   * Milestone 3 — upload validation
   * ---------------------------------------------------------------- */
  validationReport: (projectId: number) => request<any>(`/api/projects/${projectId}/validation/report`),
  preflightUpload: (projectId: number, fileName: string, contentType: string, size: number) => {
    const qs = new URLSearchParams({
      file_name: fileName,
      content_type: contentType,
      size: String(size),
    });
    return request<ValidationReport>(`/api/projects/${projectId}/validation/check?${qs.toString()}`, {
      method: 'POST',
    });
  },

  // Dashboard (single unified payload for every authenticated user)
  dashboard: () => request<any>('/api/dashboard'),

  // Settings
  generalSettings: () => request<any>('/api/settings/general'),

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