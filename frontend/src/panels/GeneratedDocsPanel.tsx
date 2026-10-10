import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle, BookOpen, CalendarClock, ChevronDown, ChevronRight, Download, Eye, FileText,
  Info, Loader2, RefreshCw, ShieldCheck, Sparkles, Wand2,
} from 'lucide-react';
import {
  api,
  downloadWithAuth,
  type AnalysisStatus,
  type DateTrace,
  type DocumentValidationReport,
  type FieldVerdict,
  type GeneratedArtifact,
  type GeneratedDoc,
  type GeneratedDocMeta,
  type GeneratedDocType,
  type ValidationFinding,
  type ValidationSummary,
} from '../api/client';
import { Badge } from '../components/Badge';
import { EmptyState } from '../components/EmptyState';
import { Modal } from '../components/Modal';
import { useToast } from '../ui/ToastContext';
import { fmtDate } from '../utils/format';
import { MarkdownView } from './MarkdownView';

const TYPES: { key: GeneratedDocType; label: string; blurb: string }[] = [
  {
    key: 'user_stories',
    label: 'User Stories',
    blurb: 'Grounded in the project’s own documents through the existing RAG pipeline. Stories that cannot be traced to a retrieved section are discarded.',
  },
  {
    key: 'risk_register',
    label: 'Risk Register',
    blurb: 'Projected from the risks already recorded for this project. No new risks are created and nothing is invented.',
  },
  {
    key: 'action_items',
    label: 'Action Item List',
    blurb: 'Built from existing tasks and open blockers. Each task appears exactly once; missing owners and dates are reported as not specified.',
  },
];

const TYPE_LABEL: Record<string, string> = {
  user_stories: 'User Stories',
  risk_register: 'Risk Register',
  action_items: 'Action Item List',
};

const NOT_SPECIFIED = 'Not specified in project data.';

/** Verdict → the badge the specification asks each field to carry. */
const VERDICT_TONE: Record<FieldVerdict, 'ok' | 'warn' | 'err' | 'neutral'> = {
  SUPPORTED: 'ok',
  CONFLICT: 'warn',
  UNSUPPORTED: 'err',
  MISSING: 'neutral',
};

const VERDICT_LABEL: Record<FieldVerdict, string> = {
  SUPPORTED: 'Supported',
  CONFLICT: 'Conflict',
  UNSUPPORTED: 'Removed',
  MISSING: 'Not specified',
};

/** Field keys whose value is long enough to deserve collapsed-by-default output. */
const LONG_FIELD_KEYS = new Set(['source_evidence', 'acceptance_criteria', 'mitigation', 'contingency']);

function isPlaceholder(value: unknown): boolean {
  const v = typeof value === 'string' ? value.trim() : '';
  return v === '' || v === NOT_SPECIFIED;
}

/** Relationship fields arrive as `string | {id,title}[] | string[]`. */
function renderValue(value: unknown): string {
  if (value === null || value === undefined) return NOT_SPECIFIED;
  if (typeof value === 'string') return value.trim() || NOT_SPECIFIED;
  if (typeof value === 'number') return String(value);
  if (Array.isArray(value)) {
    const parts = value.map((item) => {
      if (item && typeof item === 'object' && 'id' in (item as Record<string, unknown>)) {
        const rec = item as { id?: string; title?: string };
        return rec.title ? `${rec.id} — ${rec.title}` : String(rec.id ?? '');
      }
      return String(item);
    }).filter(Boolean);
    return parts.length ? parts.join('; ') : NOT_SPECIFIED;
  }
  return String(value);
}

function EvidenceBlock({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 220;
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="tiny dim">{long ? (open ? text : `${text.slice(0, 220)}…`) : text}</div>
      {long && (
        <button className="btn btn-ghost btn-sm" onClick={() => setOpen(!open)}>
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          {open ? 'Hide full evidence' : 'Show full evidence'}
        </button>
      )}
    </div>
  );
}

function ValidationSummaryCard({ summary }: { summary?: ValidationSummary }) {
  if (!summary) return null;
  const flagged = summary.conflicts + summary.unsupported_removed;
  return (
    <div className="card card-flat col" style={{ gap: 10, padding: 14 }}>
      <div className="row gap-sm">
        <ShieldCheck size={15} style={{ color: 'var(--violet-2)' }} />
        <b className="small">Validation and corrections</b>
        <span className="grow" />
        <Badge tone={flagged ? 'warn' : 'ok'} plain>
          {summary.status || 'not validated'}
        </Badge>
      </div>
      <div className="tiny dim" style={{ lineHeight: 1.6 }}>
        {summary.fields_checked} fields checked · {summary.supported} supported by project data ·{' '}
        {summary.corrected} corrected · {summary.unsupported_removed} removed as unsupported ·{' '}
        {summary.not_specified} not specified in project data · {summary.conflicts} conflicts recorded.
      </div>
      {summary.findings.filter((f) => f.verdict !== 'SUPPORTED').length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Field</th>
                <th>Verdict</th>
                <th>Value kept</th>
                <th>Explanation</th>
              </tr>
            </thead>
            <tbody>
              {summary.findings.filter((f) => f.verdict !== 'SUPPORTED').map((f, i) => (
                <tr key={`${f.field}-${i}`}>
                  <td className="mono tiny">{f.field}</td>
                  <td><Badge tone={VERDICT_TONE[f.verdict]} plain>{VERDICT_LABEL[f.verdict]}</Badge></td>
                  <td className="tiny">{f.value}</td>
                  <td className="tiny dim">{f.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/**
 * Concise grounding rules the validator applies, shown instead of any internal
 * reasoning. Section 19 of the specification forbids exposing chain-of-thought.
 */
const VALIDATION_RULES = [
  'A field is SUPPORTED when it can be traced to an uploaded document, a retrieved chunk, or an existing project record.',
  'A value that appears nowhere in those three is removed and replaced with “Not specified in project data.”',
  'An explicit date in a source document overrides a conflicting administrator-entered date; both values are kept.',
  'A recorded risk assessment is never rewritten — a disagreement is reported as a conflict.',
];

function ValidationReportView({
  report,
  loading,
  error,
  onRetry,
}: {
  report: DocumentValidationReport | null;
  loading: boolean;
  error: string;
  onRetry: () => void;
}) {
  if (loading) {
    return (
      <div className="panel-body row gap-sm" style={{ color: 'var(--text-2)' }}>
        <Loader2 size={15} className="spin" /> Loading the validation report…
      </div>
    );
  }
  if (error) {
    return (
      <EmptyState
        icon={<AlertTriangle size={20} />}
        title="Validation report unavailable"
        description={error}
      />
    );
  }
  if (!report) return null;

  const byVerdict = (verdict: FieldVerdict) => report.findings.filter((f) => f.verdict === verdict);
  const groups: { verdict: FieldVerdict; title: string }[] = [
    { verdict: 'CONFLICT', title: 'Conflicts — recorded value preserved' },
    { verdict: 'UNSUPPORTED', title: 'Removed — not traceable to project data' },
    { verdict: 'MISSING', title: 'Not specified in project data' },
    { verdict: 'SUPPORTED', title: 'Supported by project data' },
  ];

  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="card card-flat col" style={{ gap: 8, padding: 14 }}>
        <div className="row gap-sm">
          <ShieldCheck size={15} style={{ color: 'var(--violet-2)' }} />
          <b className="small">Validation report</b>
          <span className="grow" />
          <Badge tone={report.summary.conflicts + report.summary.unsupported_removed > 0 ? 'warn' : 'ok'} plain>
            {report.summary.status}
          </Badge>
        </div>
        <div className="tiny dim" style={{ lineHeight: 1.6 }}>
          {report.summary.fields_checked} fields checked · {report.summary.supported} supported ·{' '}
          {report.summary.corrected} corrected · {report.summary.unsupported_removed} removed as
          unsupported · {report.summary.not_specified} not specified ·{' '}
          {report.summary.conflicts} conflicts recorded
          {report.generated_at ? ` · validated ${fmtDate(report.generated_at)}` : ''}
        </div>
      </div>

      {report.notes.length > 0 && (
        <div className="card card-flat col" style={{ gap: 6, padding: 14 }}>
          <div className="row gap-sm tiny dim"><Info size={13} /> Grounding rules applied</div>
          <ul className="md-list tiny" style={{ margin: 0, paddingLeft: 18 }}>
            {report.notes.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        </div>
      )}

      {groups.map(({ verdict, title }) => {
        const rows = byVerdict(verdict);
        if (!rows.length) return null;
        return (
          <div key={verdict} className="card card-flat col" style={{ gap: 8, padding: 14 }}>
            <div className="row gap-sm">
              <b className="small">{title}</b>
              <span className="grow" />
              <Badge tone={VERDICT_TONE[verdict]} plain>{rows.length}</Badge>
            </div>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>Value kept</th>
                    <th>Source</th>
                    <th>Explanation</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((f, i) => (
                    <tr key={`${f.field}-${i}`}>
                      <td className="mono tiny">{f.field}</td>
                      <td className="tiny">{f.value}</td>
                      <td className="tiny dim">{f.source || '—'}</td>
                      <td className="tiny dim">{f.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}

      <button className="btn btn-ghost btn-sm" onClick={onRetry}>Refresh report</button>
    </div>
  );
}

function ArtifactCard({ artifact }: { artifact: GeneratedArtifact }) {
  const conflicts = artifact.conflicts ?? [];
  const trace = artifact.due_date_trace as Partial<DateTrace> | undefined;
  const dueTrace = trace && Object.keys(trace).length ? trace : null;

  return (
    <div className="card card-flat col" style={{ gap: 10, padding: 14 }}>
      <div className="row gap-sm">
        <span className="mono tiny" style={{ color: 'var(--violet-2)' }}>{artifact.id}</span>
        <b className="small grow">{artifact.title}</b>
        {conflicts.length > 0 && (
          <Badge tone="warn" plain><AlertTriangle size={11} /> {conflicts.length} conflict{conflicts.length > 1 ? 's' : ''}</Badge>
        )}
      </div>

      <div className="col" style={{ gap: 4 }}>
        {artifact.fields.map((field) => {
          const verdict = artifact.field_verdicts?.[field.key];
          const text = renderValue(field.value);
          const placeholder = isPlaceholder(field.value);
          return (
            <div className="row gap-sm" key={field.key} style={{ alignItems: 'flex-start' }}>
              <span className="tiny dim" style={{ minWidth: 132, flexShrink: 0 }}>{field.label}</span>
              <span className="grow col" style={{ gap: 4 }}>
                {LONG_FIELD_KEYS.has(field.key) && !placeholder && Array.isArray(field.value) ? (
                  <ul className="tiny" style={{ margin: 0, paddingLeft: 16 }}>
                    {(field.value as string[]).map((c, i) => <li key={i}>{c}</li>)}
                  </ul>
                ) : LONG_FIELD_KEYS.has(field.key) && !placeholder ? (
                  <EvidenceBlock text={text} />
                ) : (
                  <span
                    className="tiny"
                    style={placeholder ? { color: 'var(--text-3)', fontStyle: 'italic' } : undefined}
                  >
                    {text}
                  </span>
                )}
                {verdict && verdict !== 'SUPPORTED' && (
                  <Badge tone={VERDICT_TONE[verdict]} plain>{VERDICT_LABEL[verdict]}</Badge>
                )}
              </span>
            </div>
          );
        })}
      </div>

      {dueTrace && (
        <div className="tiny dim" style={{ lineHeight: 1.6 }}>
          Due date traceability — administrator-entered: {dueTrace.admin_date || NOT_SPECIFIED}; in source
          document: {dueTrace.document_date || NOT_SPECIFIED}; effective: {dueTrace.effective_date || NOT_SPECIFIED};
          precedence: {dueTrace.source || NOT_SPECIFIED}
          {dueTrace.conflict ? '; the two disagree and the conflict is recorded.' : '.'}
        </div>
      )}

      {conflicts.map((c, i) => (
        <div key={i} className="tiny row gap-sm" style={{ color: 'var(--amber)' }}>
          <AlertTriangle size={12} /> <span>{c}</span>
        </div>
      ))}
    </div>
  );
}

export function GeneratedDocsPanel({ projectId }: { projectId: number }) {
  const [docs, setDocs] = useState<GeneratedDocMeta[]>([]);
  const [descriptions, setDescriptions] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState<string | null>(null);
  const [viewing, setViewing] = useState<GeneratedDoc | null>(null);
  const [tab, setTab] = useState<'structured' | 'validation' | 'markdown'>('structured');
  const [regenerating, setRegenerating] = useState<number | null>(null);
  const [report, setReport] = useState<DocumentValidationReport | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState('');
  const [analysis, setAnalysis] = useState<AnalysisStatus | null>(null);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const res = await api.listGeneratedDocs(projectId);
      setDocs(res.documents || []);
      setDescriptions(res.summary?.descriptions || {});
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load generated documentation.');
    } finally {
      setLoading(false);
    }
  }, [projectId, toast]);

  const loadAnalysis = useCallback(async () => {
    try {
      setAnalysis(await api.projectAnalysisStatus(projectId));
    } catch {
      // The due-date banner is supplementary; never block the panel on it.
      setAnalysis(null);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadAnalysis(); }, [loadAnalysis]);

  /**
   * The validation report is fetched only when its tab is opened, so opening a
   * document stays fast and the report always reflects the version on screen.
   */
  const loadReport = useCallback(async (docId: number) => {
    setReportLoading(true);
    setReportError('');
    try {
      setReport(await api.generatedDocValidation(projectId, docId));
    } catch (err: any) {
      setReport(null);
      setReportError(err?.message || 'The validation report could not be loaded.');
    } finally {
      setReportLoading(false);
    }
  }, [projectId]);

  const generate = async (docType?: GeneratedDocType) => {
    setGenerating(docType || 'all');
    try {
      const res = await api.generateDocs(projectId, docType);
      const n = res.generated?.length || 0;
      if (n) toast.success(`${n} document${n > 1 ? 's' : ''} generated.`);
      else toast.error('Generation failed. Please try again.');
      if (res.failed) toast.error(`${res.failed} document type(s) could not be generated.`);
      await load();
    } catch (err: any) {
      toast.error(err?.message || 'Generation failed.');
    } finally {
      setGenerating(null);
    }
  };

  const open = async (doc: GeneratedDocMeta) => {
    setReport(null);
    setTab('structured');
    try {
      setViewing(await api.getGeneratedDoc(projectId, doc.id));
    } catch (err: any) {
      toast.error(err?.message || 'Could not open the document.');
    }
  };

  const selectTab = (next: 'structured' | 'validation' | 'markdown') => {
    setTab(next);
    if (next === 'validation' && viewing && !report && !reportLoading) {
      loadReport(viewing.id);
    }
  };

  const regenerate = async (doc: GeneratedDocMeta) => {
    setRegenerating(doc.id);
    try {
      await api.regenerateDoc(projectId, doc.id);
      toast.success('Document regenerated in place.');
      await load();
    } catch (err: any) {
      toast.error(err?.message || 'Regeneration failed.');
    } finally {
      setRegenerating(null);
    }
  };

  const download = async (doc: GeneratedDocMeta, format: 'md' | 'docx') => {
    const name = format === 'docx'
      ? doc.file_name.replace(/\.md$/, '.docx')
      : doc.file_name;
    try {
      await downloadWithAuth(api.generatedDocDownloadUrl(projectId, doc.id, format), name);
    } catch (err: any) {
      toast.error(err?.message || 'Download failed.');
    }
  };

  const byType = (t: string) => docs.find((d) => d.doc_type === t);
  const artifacts = useMemo(
    () => (viewing?.payload?.artifacts ?? []) as GeneratedArtifact[],
    [viewing],
  );
  const groundingNotes = useMemo(() => {
    const payload = viewing?.payload;
    if (!payload) return [] as string[];
    const notes = [...(payload.validation_notes ?? [])];
    for (const n of payload.notes ?? []) {
      if (!notes.includes(n)) notes.push(n);
    }
    return notes;
  }, [viewing]);

  if (loading) {
    return (
      <div className="panel">
        <div className="panel-body row gap-sm" style={{ color: 'var(--text-2)' }}>
          <Loader2 size={15} className="spin" /> Loading generated documentation…
        </div>
      </div>
    );
  }

  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="panel">
        <div className="panel-head">
          <h3><BookOpen size={16} /> Generated Documentation <span className="ph-sub">· separate from your uploaded files</span></h3>
          <button
            className="btn btn-primary btn-sm"
            onClick={() => generate()}
            disabled={generating !== null}
            title="Rebuild all three document types now"
          >
            {generating === 'all' ? <Loader2 size={14} className="spin" /> : <Wand2 size={14} />}
            Rebuild all
          </button>
        </div>
        <div className="panel-body">
          <div className="row wrap gap-sm tiny dim" style={{ marginBottom: 14, lineHeight: 1.6 }}>
            <span className="row gap-sm">
              <CalendarClock size={13} />
              <b className="cell-strong">Project Due Date:</b>{' '}
              {analysis?.due_date?.effective_date || 'Not specified'}
              {analysis?.due_date?.source === 'Document' && analysis?.due_date?.document && (
                <span className="dim"> — taken from {analysis.due_date.document}</span>
              )}
              {analysis?.due_date?.source === 'Admin' && (
                <span className="dim"> — taken from the project record</span>
              )}
            </span>
            {analysis?.due_date?.conflict && (
              <span className="row gap-sm" style={{ color: 'var(--amber)' }}>
                <AlertTriangle size={13} />
                The uploaded document states a different date, which takes precedence over the project record.
              </span>
            )}
            <span className="row gap-sm">
              <Wand2 size={13} />
              These documents are produced automatically after every upload.
            </span>
          </div>
          <div className="tiny dim" style={{ marginBottom: 14, lineHeight: 1.6 }}>
            Generated documents are stored separately from the original uploads. Regenerating replaces the generated copy only —
            your uploaded files are never modified or replaced.
          </div>
          <div className="grid grid-3">
            {TYPES.map((t) => {
              const doc = byType(t.key);
              return (
                <div key={t.key} className="card card-flat col" style={{ gap: 10, padding: 15 }}>
                  <div className="row-between">
                    <div className="row gap-sm">
                      <FileText size={15} style={{ color: 'var(--violet-2)' }} />
                      <b className="small">{t.label}</b>
                    </div>
                    {doc ? (
                      <Badge tone="ok" plain>v{doc.generation_count}</Badge>
                    ) : (
                      <Badge tone="neutral" plain>not generated</Badge>
                    )}
                  </div>
                  <div className="tiny dim" style={{ lineHeight: 1.55 }}>
                    {descriptions[t.key] || t.blurb}
                  </div>
                  {doc?.insufficient_evidence && (
                    <div className="row gap-sm tiny" style={{ color: 'var(--amber)' }}>
                      <AlertTriangle size={12} /> Insufficient evidence in uploaded project documents.
                    </div>
                  )}
                  <div className="row gap-sm" style={{ marginTop: 2 }}>
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => generate(t.key)}
                      disabled={generating !== null}
                    >
                      {generating === t.key ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />}
                      {doc ? 'Regenerate' : 'Generate'}
                    </button>
                    {doc && (
                      <>
                        <button className="btn btn-ghost btn-sm" onClick={() => open(doc)}>
                          <Eye size={13} /> View
                        </button>
                        <button className="btn btn-ghost btn-sm" onClick={() => download(doc, 'md')} title="Download Markdown">
                          <Download size={13} /> MD
                        </button>
                        <button className="btn btn-ghost btn-sm" onClick={() => download(doc, 'docx')} title="Download DOCX">
                          <Download size={13} /> DOCX
                        </button>
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h3>Generated documents</h3>
          <span className="ph-sub">{docs.length} of {TYPES.length} generated</span>
        </div>
        <div className="panel-body">
          {docs.length === 0 ? (
            <EmptyState
              icon={<BookOpen size={22} />}
              title="No documents yet"
              description="Upload a project document and user stories, a risk register and an action item list are generated automatically from it."
            />
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Document</th>
                    <th>Type</th>
                    <th>Version</th>
                    <th>Evidence</th>
                    <th>Provider</th>
                    <th>Updated</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {docs.map((d) => (
                    <tr key={d.id}>
                      <td className="cell-strong">{TYPE_LABEL[d.doc_type] || d.doc_type}</td>
                      <td className="tiny dim">{d.file_name}</td>
                      <td className="mono">v{d.generation_count}</td>
                      <td>
                        {d.insufficient_evidence ? (
                          <Badge tone="warn" plain>insufficient evidence</Badge>
                        ) : (
                          <Badge tone="ok" plain>grounded</Badge>
                        )}
                      </td>
                      <td className="tiny dim">{d.provider ? `${d.provider} · ${d.model}` : 'deterministic'}</td>
                      <td className="tiny dim">{fmtDate(d.updated_at)}</td>
                      <td>
                        <div className="row gap-sm">
                          <button className="btn btn-ghost btn-icon" onClick={() => open(d)} title="View">
                            <Eye size={14} />
                          </button>
                          <button
                            className="btn btn-ghost btn-icon"
                            onClick={() => regenerate(d)}
                            title="Regenerate in place"
                            disabled={regenerating === d.id}
                          >
                            {regenerating === d.id ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
                          </button>
                          <button
                            className="btn btn-ghost btn-icon"
                            onClick={() => download(d, 'md')}
                            title="Download Markdown"
                          >
                            <Download size={14} />
                          </button>
                          <button
                            className="btn btn-ghost btn-icon"
                            onClick={() => download(d, 'docx')}
                            title="Download DOCX"
                          >
                            <FileText size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <Modal
        open={!!viewing}
        onClose={() => { setViewing(null); setTab('structured'); setReport(null); }}
        title={viewing?.title || 'Document'}
        wide
      >
        {viewing && (
          <div className="col" style={{ gap: 12 }}>
            {viewing.insufficient_evidence && (
              <div className="card card-flat row gap-sm" style={{ padding: 12, color: 'var(--amber)' }}>
                <AlertTriangle size={14} />
                <span className="small">
                  Insufficient evidence in uploaded project documents.
                  {viewing.payload?.reason ? ` ${viewing.payload.reason}` : ''}
                </span>
              </div>
            )}
            <div className="row gap-sm">
              <button
                className={`btn btn-sm ${tab === 'structured' ? 'btn-secondary' : 'btn-ghost'}`}
                onClick={() => selectTab('structured')}
              >
                Structured
              </button>
              <button
                className={`btn btn-sm ${tab === 'validation' ? 'btn-secondary' : 'btn-ghost'}`}
                onClick={() => selectTab('validation')}
              >
                Validation
              </button>
              <button
                className={`btn btn-sm ${tab === 'markdown' ? 'btn-secondary' : 'btn-ghost'}`}
                onClick={() => selectTab('markdown')}
              >
                Markdown
              </button>
              <span className="grow" />
              <button className="btn btn-ghost btn-sm" onClick={() => download(viewing, 'md')}>
                <Download size={13} /> MD
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => download(viewing, 'docx')}>
                <Download size={13} /> DOCX
              </button>
            </div>

            {tab === 'structured' && (
              <div className="col" style={{ gap: 12 }}>
                <ValidationSummaryCard summary={viewing.payload?.validation} />
                {artifacts.length === 0 ? (
                  <EmptyState
                    icon={<FileText size={20} />}
                    title="No entries"
                    description="This document type produced no entries for the current project data."
                  />
                ) : (
                  artifacts.map((artifact) => (
                    <ArtifactCard key={artifact.id || artifact.title} artifact={artifact} />
                  ))
                )}
                {groundingNotes.length > 0 && (
                  <div className="card card-flat col" style={{ gap: 6, padding: 14 }}>
                    <div className="row gap-sm tiny dim">
                      <Info size={13} /> How this document was built
                    </div>
                    <ul className="md-list tiny dim" style={{ margin: 0, paddingLeft: 18 }}>
                      {groundingNotes.map((n, i) => <li key={i}>{n}</li>)}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {tab === 'validation' && (
              <ValidationReportView
                report={report}
                loading={reportLoading}
                error={reportError}
                onRetry={() => loadReport(viewing.id)}
              />
            )}

            {tab === 'markdown' && <MarkdownView content={viewing.content} />}
          </div>
        )}
      </Modal>
    </div>
  );
}
