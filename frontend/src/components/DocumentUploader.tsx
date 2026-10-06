import { useEffect, useRef, useState, type DragEvent } from 'react';
import { UploadCloud, FileText, CheckCircle2, AlertCircle, ShieldCheck, Loader2, TriangleAlert } from 'lucide-react';
import {
  analysisProgress,
  api,
  currentStageLabel,
  type AnalysisStatus,
  type PipelineStage,
  type ValidationReport,
} from '../api/client';
import { useToast } from '../ui/ToastContext';
import { fmtBytes } from '../utils/format';

const ACCEPTED = ['.pdf', '.docx', '.csv', '.txt'];

/** How long to keep waiting for the backend pipeline before giving up on polling. */
const POLL_LIMIT_MS = 8 * 60 * 1000;
const POLL_INTERVAL_MS = 1500;

const STAGE_ICON: Record<PipelineStage['status'], 'ok' | 'wait' | 'warn' | 'bad'> = {
  done: 'ok',
  running: 'wait',
  pending: 'wait',
  skipped: 'wait',
  warning: 'warn',
  failed: 'bad',
};

function isFinished(status: string) {
  return status === 'Completed' || status === 'CompletedWithWarnings' || status === 'Failed';
}

export function DocumentUploader({
  projectId,
  onUploaded,
}: {
  projectId: number;
  onUploaded?: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const [drag, setDrag] = useState(false);
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState('Uploading');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [fileName, setFileName] = useState('');
  const [preflight, setPreflight] = useState<ValidationReport | null>(null);
  const [checking, setChecking] = useState(false);
  const [run, setRun] = useState<AnalysisStatus | null>(null);

  // Poll the real backend pipeline state. Nothing here is simulated: every
  // stage label and percentage comes from /analysis/status.
  const waitForPipeline = async (): Promise<AnalysisStatus | null> => {
    const deadline = Date.now() + POLL_LIMIT_MS;
    while (Date.now() < deadline) {
      let status: AnalysisStatus;
      try {
        status = await api.projectAnalysisStatus(projectId);
      } catch {
        // A transient status failure must not abort the upload; retry.
        await new Promise((r) => window.setTimeout(r, POLL_INTERVAL_MS));
        continue;
      }
      setRun(status);
      if (isFinished(status.status)) return status;
      await new Promise((r) => window.setTimeout(r, POLL_INTERVAL_MS));
    }
    return null;
  };

  const reset = () => {
    setBusy(false);
    setProgress(0);
    setFileName('');
    setPreflight(null);
  };

  const runWith = async (file: File) => {
    if (!ACCEPTED.some((ext) => file.name.toLowerCase().endsWith(ext))) {
      toast.error(`Unsupported format. Supported: ${ACCEPTED.join(' ')}`);
      return;
    }

    // Milestone 3 pre-flight: ask the server to run the metadata rules before
    // spending bandwidth on a file that would be rejected anyway.
    setChecking(true);
    setError('');
    try {
      const check = await api.preflightUpload(projectId, file.name, file.type || '', file.size);
      setPreflight(check);
      if (!check.ok) {
        setError(check.detail);
        toast.error(`Rejected before upload: ${check.detail}`);
        return;
      }
    } catch {
      // A failed pre-flight must never block a legitimate upload; the server
      // re-runs the full check on receipt.
      setPreflight(null);
    } finally {
      setChecking(false);
    }

    setFileName(file.name);
    setBusy(true);
    setProgress(0);
    setPhase('Uploading');
    setRun(null);

    try {
      await api.uploadDocument(projectId, file, (pct) => setProgress(Math.min(pct, 20)));
      setPhase('Analysing project automatically');
      onUploaded?.();

      const finished = await waitForPipeline();

      if (!finished) {
        setPhase('Still running in the background');
        toast.info("Uploaded. Analysis is still running — reopen this page in a moment.");
        reset();
        return;
      }
      if (finished.status === 'Failed') {
        setPhase('Analysis failed');
        toast.error(finished.error || 'Project analysis failed.');
        return;
      }
      const count = finished.counts.generated_documents ?? 0;
      setPhase('Completed');
      setProgress(100);
      toast.success(
        finished.status === 'CompletedWithWarnings'
          ? `'${file.name}' analysed with warnings — some steps could not use an AI provider.`
          : `'${file.name}' analysed. ${count} document(s) ready.`,
      );
      onUploaded?.();
      window.setTimeout(reset, 4000);
    } catch (err: any) {
      setError(err?.message || 'Upload failed.');
      toast.error(err?.message || 'Upload failed.');
      reset();
    }
  };

  // Stop polling if the user navigates away mid-run.
  useEffect(() => () => setRun(null), []);

  const onDrop = async (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    const file = e.dataTransfer.files?.[0];
    if (file) await runWith(file);
  };

  const onPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) await runWith(file);
    if (inputRef.current) inputRef.current.value = '';
  };

  const uploading = progress < 20 && phase === 'Uploading';

  return (
    <div className="col">
      <div
        className={`dropzone ${drag ? 'drag' : ''}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
      >
        <input ref={inputRef} type="file" accept={ACCEPTED.join(',')} onChange={onPick} />
        <div className="dz-ico"><UploadCloud size={25} /></div>
        <div>
          <h4>Drop your project document</h4>
          <div className="dz-formats" style={{ marginTop: 6 }}>PDF · DOCX · CSV · TXT</div>
        </div>
        <button className="btn btn-secondary btn-sm" type="button" onClick={(e) => { e.stopPropagation(); inputRef.current?.click(); }}>
          <FileText size={15} /> Choose File
        </button>
      </div>

      {checking && (
        <div className="row gap-sm tiny dim">
          <Loader2 size={13} className="spin" /> Running validation checks…
        </div>
      )}

      {preflight?.ok && !checking && !busy && (
        <div className="row gap-sm tiny" style={{ color: 'var(--emerald)' }}>
          <ShieldCheck size={13} /> Passed {preflight.checks.length} validation check(s) — structure and content
          are verified again once the file arrives.
        </div>
      )}

      {busy && (
        <div className="card card-flat upload-progress col gap-sm">
          <div className="row-between" style={{ fontSize: 13 }}>
            <span className="cell-strong" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{fileName}</span>
            <span className="tiny dim">{run ? `${analysisProgress(run)}%` : `${progress}%`}</span>
          </div>
          <div className="progress-track">
            <div
              className="progress-fill"
              style={{ width: `${run ? analysisProgress(run) : progress}%` }}
            />
          </div>
          <div className="row gap-sm">
            {run && isFinished(run.status) ? (
              run.status === 'Failed'
                ? <AlertCircle size={15} style={{ color: 'var(--err)' }} />
                : <CheckCircle2 size={15} style={{ color: 'var(--emerald)' }} />
            ) : uploading ? (
              <span className="spinner" style={{ width: 14, height: 14 }} />
            ) : (
              <Loader2 size={15} className="spin" />
            )}
            <span className="tiny muted">
              {run ? currentStageLabel(run) : phase}
            </span>
          </div>

          {run && run.stages.length > 0 && (
            <ul className="pipeline-steps">
              {run.stages.map((s) => (
                <li key={s.key} className={`pipeline-step ${s.status}`}>
                  {STAGE_ICON[s.status] === 'ok' && <CheckCircle2 size={13} style={{ color: 'var(--emerald)' }} />}
                  {STAGE_ICON[s.status] === 'wait' && (
                    <span className="step-dot" style={{ background: s.status === 'running' ? 'var(--accent)' : 'var(--line)' }} />
                  )}
                  {STAGE_ICON[s.status] === 'warn' && <TriangleAlert size={13} style={{ color: 'var(--warn)' }} />}
                  {STAGE_ICON[s.status] === 'bad' && <AlertCircle size={13} style={{ color: 'var(--err)' }} />}
                  <span className="step-label">{s.label}</span>
                  {s.detail && <span className="step-detail">{s.detail}</span>}
                </li>
              ))}
            </ul>
          )}

          {run?.error && <div className="form-error">{run.error}</div>}
        </div>
      )}

      {error && !busy && (
        <div className="form-error">
          <AlertCircle size={15} /> {error}
          {preflight && !preflight.ok && preflight.checks.length > 0 && (
            <div className="col" style={{ gap: 3, marginTop: 7 }}>
              {preflight.checks.map((c, i) => (
                <div key={i} className="tiny" style={{ color: c.passed ? 'var(--emerald)' : 'var(--err)' }}>
                  {c.passed ? '✓' : '✗'} {c.check.replace(/_/g, ' ')}
                  {c.detail && !c.passed ? ` — ${c.detail}` : ''}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="tiny dim" style={{ textAlign: 'center' }}>
        Max upload size: {fmtBytes(25 * 1024 * 1024)} · Duplicate content within a project is rejected ·
        analysis starts automatically after upload
      </div>
    </div>
  );
}
