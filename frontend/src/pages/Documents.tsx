import { useCallback, useEffect, useMemo, useState } from 'react';
import { FileText, Download, Eye, RefreshCw, Trash2, Search, FilterX } from 'lucide-react';
import { api, downloadWithAuth } from '../api/client';
import { AppShell } from '../layout/AppShell';
import { EmptyState, PageLoader } from '../components/EmptyState';
import { Badge, statusTone } from '../components/Badge';
import { Modal } from '../components/Modal';
import { useToast } from '../ui/ToastContext';
import { useAuth } from '../auth/AuthContext';
import { fmtBytes, timeAgo, fmtDate } from '../utils/format';

export default function Documents() {
  const { user } = useAuth();
  const isAdmin = user?.role === 'ADMIN';
  const [docs, setDocs] = useState<any[]>([]);
  const [projects, setProjects] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [preview, setPreview] = useState<any>(null);
  const [filters, setFilters] = useState<any>({ project_id: '', file_type: '', status: '' });
  const toast = useToast();

  const loadProjects = useCallback(async () => {
    try {
      const p = await api.listProjects();
      setProjects(p);
    } catch {
      setProjects([]);
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const params: Record<string, any> = {};
      if (filters.project_id) params.project_id = filters.project_id;
      if (filters.file_type) params.file_type = filters.file_type;
      if (filters.status) params.status = filters.status;
      setDocs(await api.listDocuments(params));
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load documents.');
    } finally {
      setLoading(false);
    }
  }, [filters, toast]);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  useEffect(() => { load(); }, [load]);

  const projectName = useMemo(() => {
    const m: Record<string, string> = {};
    projects.forEach((p) => { m[p.id] = p.name; });
    return m;
  }, [projects]);

  const doDelete = async (d: any) => {
    if (!window.confirm(`Delete "${d.original_name}"? This also removes its vectors.`)) return;
    try {
      await api.deleteDocument(d.id);
      toast.success('Document deleted.');
      load();
    } catch (err: any) {
      toast.error(err?.message || 'Delete failed.');
    }
  };

  const doReprocess = async (d: any) => {
    try {
      await api.reprocessDocument(d.id);
      toast.info('Reprocessing started.');
      setTimeout(load, 1800);
    } catch (err: any) {
      toast.error(err?.message || 'Reprocess failed.');
    }
  };

  const openPreview = async (id: number) => {
    try {
      setPreview(await api.previewDocument(id));
    } catch (err: any) {
      toast.error(err?.message || 'Preview unavailable.');
    }
  };

  const clearFilters = () => setFilters({ project_id: '', file_type: '', status: '' });

  if (loading) return <AppShell title="Documents" crumb="Library"><PageLoader label="Loading documents…" /></AppShell>;

  const hasFilter = filters.project_id || filters.file_type || filters.status;

  return (
    <AppShell title="Documents" crumb="Library · Files">
      <div className="filters-bar">
        <div className="field">
          <label>Project</label>
          <select className="select" value={filters.project_id} onChange={(e) => setFilters({ ...filters, project_id: e.target.value })}>
            <option value="">All projects</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <div className="field">
          <label>File type</label>
          <select className="select" value={filters.file_type} onChange={(e) => setFilters({ ...filters, file_type: e.target.value })}>
            <option value="">All types</option>
            {['pdf', 'docx', 'csv', 'txt'].map((t) => <option key={t} value={t}>{t.toUpperCase()}</option>)}
          </select>
        </div>
        <div className="field">
          <label>Status</label>
          <select className="select" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
            <option value="">All statuses</option>
            {['Uploaded', 'Processing', 'Processed', 'Failed'].map((s) => <option key={s}>{s}</option>)}
          </select>
        </div>
        {hasFilter && (
          <button className="btn btn-secondary btn-sm" onClick={clearFilters}><FilterX size={14} /> Clear</button>
        )}
      </div>

      {docs.length === 0 ? (
        <EmptyState
          icon={<FileText size={26} />}
          title={hasFilter ? 'No documents match the filters' : 'No documents uploaded'}
          description="Uploaded files stay here with their original binary, extracted text, chunks and embeddings. Only files uploaded through the application appear — developer files are never indexed."
        />
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Document</th><th>Project</th><th>Type</th><th>Uploaded By</th><th>Date</th><th>Status</th><th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id}>
                  <td>
                    <div className="row gap-sm">
                      <div className={`doc-row-file ft-${d.file_type || 'txt'}`}>{d.file_type?.toUpperCase()}</div>
                      <div className="col gap-sm" style={{ gap: 2 }}>
                        <span className="cell-strong" style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.original_name}</span>
                        <span className="tiny dim">{fmtBytes(d.file_size)} · {d.chunk_count} chunks</span>
                      </div>
                    </div>
                  </td>
                  <td className="muted small">{projectName[d.project_id] || `#${d.project_id}`}</td>
                  <td><Badge tone="neutral" plain>{d.file_type?.toUpperCase()}</Badge></td>
                  <td className="muted small">{d.uploader_name || '—'}</td>
                  <td className="muted small" style={{ whiteSpace: 'nowrap' }} title={fmtDate(d.uploaded_at)}>{timeAgo(d.uploaded_at)}</td>
                  <td>
                    <Badge tone={statusTone(d.status)}>{d.status}</Badge>
                    {d.embedding_status === 'Completed' && d.status === 'Processed' && (
                      <div className="tiny dim" style={{ marginTop: 3 }}>Embeddings ✓ · {d.page_count ? `${d.page_count} pages` : ''}</div>
                    )}
                    {d.error_message && <div className="tiny" style={{ color: 'var(--red)', maxWidth: 240, marginTop: 3 }}>{d.error_message}</div>}
                  </td>
                  <td>
                    <div className="cell-actions">
                      <button className="btn btn-icon" title="View" onClick={() => openPreview(d.id)}><Eye size={15} /></button>
                      <button className="btn btn-icon" title="Download original" onClick={() => downloadWithAuth(api.downloadUrl(d.id), d.original_name)}><Download size={15} /></button>
                      {isAdmin && (
                        <>
                          <button className="btn btn-icon" title="Reprocess" onClick={() => doReprocess(d)}><RefreshCw size={15} /></button>
                          <button className="btn btn-icon" title="Delete" style={{ color: 'var(--red)' }} onClick={() => doDelete(d)}><Trash2 size={15} /></button>
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

      <Modal open={!!preview} onClose={() => setPreview(null)} title={preview?.original_name} wide>
        {preview && (
          <div className="col">
            <div className="row wrap gap-sm">
              <Badge tone={statusTone(preview.status)}>{preview.status}</Badge>
              {preview.chunk_count ? <Badge tone="violet" plain>{preview.chunk_count} chunks</Badge> : null}
              {preview.page_count ? <Badge tone="cyan" plain>{preview.page_count} pages</Badge> : null}
            </div>
            {preview.error_message && <div className="form-error"><Search size={15} /> {preview.error_message}</div>}
            <div className="preview-pane">{preview.text || '(No extracted text available.)'}</div>
            {preview.has_more && <div className="tiny dim">Preview truncated — download the original for the full file.</div>}
          </div>
        )}
      </Modal>
    </AppShell>
  );
}