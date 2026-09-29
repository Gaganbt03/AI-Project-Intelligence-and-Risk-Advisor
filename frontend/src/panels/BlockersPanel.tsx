import { useCallback, useEffect, useMemo, useState } from 'react';
import { CircleSlash, Plus, FileText } from 'lucide-react';
import { api } from '../api/client';
import { EmptyState } from '../components/EmptyState';
import { Badge, SeverityBadge, statusTone } from '../components/Badge';
import { Modal } from '../components/Modal';
import { useToast } from '../ui/ToastContext';
import { fmtDate } from '../utils/format';
import { useAuth } from '../auth/AuthContext';

export function BlockersPanel({ projectId, employees = [], canReport = true }: { projectId: number; employees?: { id: number; name: string }[]; canReport?: boolean }) {
  const [blockers, setBlockers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<any>({});
  const { user } = useAuth();
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      setBlockers(await api.listBlockers(projectId));
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load blockers.');
    } finally {
      setLoading(false);
    }
  }, [projectId, toast]);

  useEffect(() => { load(); }, [load]);

  const openReport = () => {
    setForm({
      title: '',
      description: '',
      severity: 'Medium',
      owner: user?.name || '',
      expected_resolution: '',
    });
    setOpen(true);
  };

  const save = async () => {
    if (!form.title?.trim()) return toast.error('Blocker title is required.');
    try {
      await api.reportBlocker(projectId, form);
      toast.success('Blocker reported.');
      setOpen(false);
      load();
    } catch (err: any) {
      toast.error(err?.message || 'Save failed.');
    }
  };

  const setStatus = async (b: any, status: string) => {
    try {
      await api.updateBlocker(b.id, { status });
      toast.success(`Blocker marked ${status}.`);
      load();
    } catch (err: any) {
      toast.error(err?.message || 'Update failed.');
    }
  };

  const byStatus = useMemo(() => {
    const map: Record<string, number> = { Open: 0, 'In Progress': 0, Resolved: 0 };
    blockers.forEach((b) => {
      if (map[b.status] !== undefined) map[b.status] += 1;
    });
    return map;
  }, [blockers]);

  return (
    <div className="col">
      <div className="grid grid-3">
        {(['Open', 'In Progress', 'Resolved'] as const).map((s) => (
          <div className="card card-flat row-between" key={s} style={{ padding: 14 }}>
            <span className="tiny dim">{s}</span>
            <b style={{ color: s === 'Resolved' ? 'var(--emerald)' : s === 'In Progress' ? 'var(--amber)' : 'var(--red)' }}>{byStatus[s]}</b>
          </div>
        ))}
      </div>

      {canReport && (
        <div className="row-between">
          <div className="tiny dim">AI-detected blockers and employee-reported blockers are kept distinct.</div>
          <button className="btn btn-primary btn-sm" onClick={openReport}><Plus size={15} /> Report Blocker</button>
        </div>
      )}

      {loading ? (
        <div className="row gap-sm" style={{ padding: 24 }}><span className="spinner" /><span className="muted small">Loading blockers…</span></div>
      ) : blockers.length === 0 ? (
        <EmptyState
          icon={<CircleSlash size={26} />}
          title="No blockers"
          description="Blockers identified by AI from meeting notes and progress reports will appear here, alongside any you report."
          action={canReport ? <button className="btn btn-secondary btn-sm" onClick={openReport}><Plus size={15} /> Report Blocker</button> : undefined}
        />
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Blocker</th>
                <th>Severity</th>
                <th>Owner</th>
                <th>Source</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {blockers.map((b) => (
                <tr key={b.id}>
                  <td>
                    <div className="cell-strong" style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.title}</div>
                    <div className="tiny dim" style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.description}</div>
                    {b.expected_resolution && <div className="tiny dim" style={{ marginTop: 3 }}>Expected resolution: {b.expected_resolution}</div>}
                  </td>
                  <td><SeverityBadge value={b.severity} /></td>
                  <td className="muted small">{b.owner || '—'}</td>
                  <td className="tiny">
                    {b.source_type === 'ai_detected' ? <Badge tone="violet" plain>AI Detected</Badge> : <Badge tone="cyan" plain>Reported</Badge>}
                    {b.evidence && (
                      <div className="dim" style={{ marginTop: 4, maxWidth: 220 }}><FileText size={11} style={{ verticalAlign: 'middle', marginRight: 4 }} />{b.evidence}</div>
                    )}
                  </td>
                  <td>
                    <Badge tone={statusTone(b.status)}>{b.status}</Badge>
                    <div className="tiny dim" style={{ marginTop: 3 }}>{fmtDate(b.identified_at)}</div>
                  </td>
                  <td>
                    <div className="cell-actions">
                      {b.status === 'Open' && <button className="btn btn-secondary btn-sm" onClick={() => setStatus(b, 'In Progress')}>In Progress</button>}
                      {b.status !== 'Resolved' && <button className="btn btn-secondary btn-sm" onClick={() => setStatus(b, 'Resolved')}>Resolve</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="Report a Blocker" footer={
        <>
          <button className="btn btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
          <button className="btn btn-primary" onClick={save}>Report Blocker</button>
        </>
      }>
        <div className="col">
          <div className="field">
            <label>Title</label>
            <input className="input" value={form.title || ''} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="What is blocking progress?" />
          </div>
          <div className="field">
            <label>Description</label>
            <textarea className="textarea" value={form.description || ''} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </div>
          <div className="grid grid-3">
            <div className="field">
              <label>Severity</label>
              <select className="select" value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })}>
                {['Low', 'Medium', 'High', 'Critical'].map((s) => <option key={s}>{s}</option>)}
              </select>
            </div>
            <div className="field">
              <label>Owner</label>
              <select className="select" value={form.owner || ''} onChange={(e) => setForm({ ...form, owner: e.target.value })}>
                {[{ name: user?.name || '', id: 0 } as any, ...employees].map((e) => (
                  <option key={e.id} value={e.name}>{e.name}</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Expected resolution</label>
              <input className="input" value={form.expected_resolution || ''} onChange={(e) => setForm({ ...form, expected_resolution: e.target.value })} placeholder="e.g. 2026-10-01" />
            </div>
          </div>
        </div>
      </Modal>
    </div>
  );
}