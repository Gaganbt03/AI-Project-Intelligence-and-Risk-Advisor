import { useRef, useState, type DragEvent } from 'react';
import { UploadCloud, FileText, CheckCircle2, AlertCircle } from 'lucide-react';
import { api } from '../api/client';
import { useToast } from '../ui/ToastContext';
import { fmtBytes } from '../utils/format';

const ACCEPTED = ['.pdf', '.docx', '.csv', '.txt'];
const STEPS = ['Uploading', 'Extracting', 'Chunking', 'Generating Embeddings', 'Indexing', 'Completed'];

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
  const [phase, setPhase] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [fileName, setFileName] = useState('');

  const runWith = async (file: File) => {
    if (!ACCEPTED.some((ext) => file.name.toLowerCase().endsWith(ext))) {
      toast.error(`Unsupported format. Supported: ${ACCEPTED.join(' ')}`);
      return;
    }
    setError('');
    setFileName(file.name);
    setUploading(true);
    setPhase(0);
    setProgress(0);
    const phaseTimer = window.setInterval(() => {
      setPhase((p) => (p < STEPS.length - 1 ? p + 1 : p));
    }, 1400);

    try {
      await api.uploadDocument(projectId, file, (pct) => {
        setProgress(Math.min(pct, 72));
      });
      setProgress(100);
      setPhase(STEPS.length - 1);
      toast.success(`'${file.name}' uploaded and processing.`);
      onUploaded?.();
    } catch (err: any) {
      setError(err?.message || 'Upload failed.');
      toast.error(err?.message || 'Upload failed.');
    } finally {
      window.clearInterval(phaseTimer);
      window.setTimeout(() => {
        setUploading(false);
        setProgress(0);
        setPhase(0);
        setFileName('');
      }, 2400);
    }
  };

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

      {uploading && (
        <div className="card card-flat upload-progress col gap-sm">
          <div className="row-between" style={{ fontSize: 13 }}>
            <span className="cell-strong" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{fileName}</span>
            <span className="tiny dim">{progress}%</span>
          </div>
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${progress}%` }} />
          </div>
          <div className="row gap-sm">
            {progress >= 100 ? (
              <CheckCircle2 size={15} style={{ color: 'var(--emerald)' }} />
            ) : (
              <span className="spinner" style={{ width: 14, height: 14 }} />
            )}
            <span className="tiny muted">{STEPS[phase]}</span>
          </div>
        </div>
      )}

      {error && !uploading && (
        <div className="form-error"><AlertCircle size={15} /> {error}</div>
      )}

      <div className="tiny dim" style={{ textAlign: 'center' }}>
        Max upload size: {fmtBytes(25 * 1024 * 1024)} · Duplicate content within a project is rejected
      </div>
    </div>
  );
}