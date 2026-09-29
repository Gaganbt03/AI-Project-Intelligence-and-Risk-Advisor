import { useCallback, useEffect, useState } from 'react';
import { FileText, Download, Eye, RefreshCw, Trash2, Search } from 'lucide-react';
import { api, downloadWithAuth } from '../api/client';
import { DocumentUploader } from '../components/DocumentUploader';
import { EmptyState } from '../components/EmptyState';
import { Badge, statusTone } from '../components/Badge';
import { Modal } from '../components/Modal';
import { useToast } from '../ui/ToastContext';
import { fmtBytes, fmtDate, timeAgo } from '../utils/format';
import { useAuth } from '../auth/AuthContext';

export function DocsPanel({ projectId, compact }: { projectId: number; compact?: boolean }) {
  const [docs, setDocs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [preview, setPreview] = useState<any>(null);
  const { user } = useAuth();
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const data = await api.listDocuments({ project_id: projectId });
      setDocs(data);
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load documents.');
    } finally {
      setLoading(false);
    }
  }, [projectId, toast]);

  useEffect(() => { load(); }, [load]);

  const doDelete = async (id: number, name: string) => {
    if (!window.confirm(`Delete "${name}" permanently? This also removes its vectors.`)) return;
    try {
      await api.deleteDocument(id);
      toast.success('Document deleted.');
      load();
    } catch (err: any) {
      toast.error(err?.message || 'Delete failed.');
    }
  };

  const doReprocess = async (id: number) => {
    try {
      await api.reprocessDocument(id);
      toast.info('Reprocessing started.');
      setTimeout(load, 1500);
    } catch (err: any) {
      toast.error(err?.message || 'Reprocess failed.');
    }
  };

  const openPreview = async (id: number) => {
    try {
      const p = await api.previewDocument(id);
      setPreview(p);
    } catch (err: any) {
      toast.error(err?.message || 'Preview unavailable.');
    }
  };

  const ft = (t: string) => `ft-${t || 'txt'}`;

  return (
    <div className="col">
      <DocumentUploader projectId={projectId} onUploaded={load} />

      <div className="panel">
        <div className="panel-head">
          <h3><FileText size={16} /> Project Documents <span className="ph-sub">· {docs.length} uploaded, real original files stored</span></h3>
        </div>

        {loading ? (
          <div className="panel-body"><div className="row gap-sm"><span className="spinner" /><span className="muted small">Loading documents…</span></div></div>
        ) : docs.length === 0 ? (
          <div className="panel-body">
            <EmptyState
              icon={<FileText size={26} />}
              title="No documents uploaded"
              description="Upload the first project document to build the project knowledge base."
            />
          </div>
        ) : (
          <div className="table-wrap" style={{ border: 'none', borderRadius: 0 }}>
            <table className="data">
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Type</th>
                  <th>Uploaded By</th>
                  <th>Date</th>
                  <th>Status</th>
                  <th style={{ textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id}>
                    <td>
                      <div className="row gap-sm">
                        <div className={`doc-row-file ${ft(d.file_type)}`}>{d.file_type?.toUpperCase()}</div>
                        <div className="col gap-sm" style={{ gap: 3 }}>
                          <span className="cell-strong" style={{ maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {d.original_name}
                          </span>
                          <span className="tiny dim">{fmtBytes(d.file_size)} · {d.chunk_count} chunks · {d.page_count ? `${d.page_count} pages` : ''}</span>
                        </div>
                      </div>
                    </td>
                    <td><Badge tone="neutral" plain>{d.file_type?.toUpperCase()}</Badge></td>
                    <td className="muted small">{d.uploader_name || '—'}</td>
                    <td className="muted small" style={{ whiteSpace: 'nowrap' }} title={fmtDate(d.uploaded_at)}>{timeAgo(d.uploaded_at)}</td>
                    <td>
                      <Badge tone={statusTone(d.status)}>{d.status}</Badge>
                      {d.embedding_status && d.status === 'Processed' && (
                        <div className="tiny dim" style={{ marginTop: 3 }}>
                          Embeddings · {d.embedding_status}
                        </div>
                      )}
                      {d.error_message && (
                        <div className="tiny" style={{ color: 'var(--red)', marginTop: 3, maxWidth: 260 }}>{d.error_message}</div>
                      )}
                    </td>
                    <td>
                      <div className="cell-actions">
                        <button className="btn btn-icon" title="View" onClick={() => openPreview(d.id)}><Eye size={15} /></button>
                        <button className="btn btn-icon" title="Download original" onClick={() => downloadWithAuth(api.downloadUrl(d.id), d.original_name)}><Download size={15} /></button>
                        {user?.role === 'ADMIN' && (
                          <>
                            <button className="btn btn-icon" title="Reprocess" onClick={() => doReprocess(d.id)}><RefreshCw size={15} /></button>
                            <button className="btn btn-icon" title="Delete" style={{ color: 'var(--red)' }} onClick={() => doDelete(d.id, d.original_name)}><Trash2 size={15} /></button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {compact && (
        <div className="tiny dim">Filter narrow view — open the full Documents page for filters.</div>
      )}

      <Modal open={!!preview} onClose={() => setPreview(null)} title={preview?.original_name} wide>
        {preview && (
          <div className="col">
            <div className="row wrap gap-sm">
              <Badge tone={statusTone(preview.status)}>{preview.status}</Badge>
              {preview.chunk_count ? <Badge tone="violet" plain>{preview.chunk_count} chunks</Badge> : null}
              {preview.page_count ? <Badge tone="cyan" plain>{preview.page_count} pages</Badge> : null}
              {preview.embedding_status ? <Badge tone="neutral" plain>Embeddings: {preview.embedding_status}</Badge> : null}
            </div>
            {preview.error_message && <div className="form-error"><Search size={15} /> {preview.error_message}</div>}
            <div className="preview-pane">{preview.text || '(No extracted text available.)'}</div>
            {preview.has_more && <div className="tiny dim">Preview truncated — download the original for full content.</div>}
          </div>
        )}
      </Modal>
    </div>
  );
}