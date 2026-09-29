import { useCallback, useEffect, useMemo, useState } from 'react';
import { TriangleAlert, Plus, FileText } from 'lucide-react';
import { api } from '../api/client';
import { EmptyState } from '../components/EmptyState';
import { Badge, SeverityBadge, statusTone } from '../components/Badge';
import { Modal } from '../components/Modal';
import { useToast } from '../ui/ToastContext';

export function RisksPanel({ projectId }: { projectId: number }) {
  const [risks, setRisks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<any>({});
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      setRisks(await api.listRisks(projectId));
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load risks.');
    } finally {
      setLoading(false);
    }
  }, [projectId, toast]);

  useEffect(() => { load(); }, [load]);

  const openCreate = () => {
    setForm({ title: '', description: '', severity: 'Medium', probability: 'Medium', impact: 'Medium', recommended_action: '' });
    setOpen(true);
  };

  const save = async () => {
    if (!form.title?.trim()) return toast.error('Risk title is required.');
    try {
      await api.createRisk(projectId, form);
      toast.success('Risk created.');
      setOpen(false);
      load();
    } catch (err: any) {
      toast.error(err?.message || 'Save failed.');
    }
  };

  const setStatus = async (r: any, status: string) => {
    try {
      await api.updateRisk(r.id, { status });
      toast.success(`Risk marked ${status}.`);
      load();
    } catch (err: any) {
      toast.error(err?.message || 'Update failed.');
    }
  };

  const bySev = useMemo(() => {
    const map: Record<string, number> = { Critical: 0, High: 0, Medium: 0, Low: 0 };
    risks.forEach((r) => {
      if (map[r.severity] !== undefined) map[r.severity] += 1;
    });
    return map;
  }, [risks]);

  return (
    <div className="col">
      <div className="grid grid-4">
        {(['Critical', 'High', 'Medium', 'Low'] as const).map((s) => (
          <div className="card card-flat row-between" key={s} style={{ padding: 14 }}>
            <span className="tiny dim">{s}</span>
            <b style={{ color: s === 'Critical' ? 'var(--sev-critical)' : s === 'High' ? 'var(--sev-high)' : s === 'Medium' ? 'var(--sev-medium)' : 'var(--sev-low)' }}>
              {bySev[s]}
            </b>
          </div>
        ))}
      </div>

      <div className="row-between">
        <div className="tiny dim">Risks identified by AI agents are stored with source evidence.</div>
        <button className="btn btn-primary btn-sm" onClick={openCreate}><Plus size={15} /> Add Risk</button>
      </div>

      {loading ? (
        <div className="row gap-sm" style={{ padding: 24 }}><span className="spinner" /><span className="muted small">Loading risks…</span></div>
      ) : risks.length === 0 ? (
        <EmptyState
          icon={<TriangleAlert size={26} />}
          title="No risks recorded"
          description="Run the AI analysis to detect schedule, dependency and scope risks — or add one manually with full evidence."
          action={<button className="btn btn-secondary btn-sm" onClick={openCreate}><Plus size={15} /> Add Risk</button>}
        />
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Risk</th>
                <th>Severity</th>
                <th>Probability</th>
                <th>Impact</th>
                <th>Evidence</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {risks.map((r) => (
                <tr key={r.id}>
                  <td>
                    <div className="cell-strong" style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.title}</div>
                    <div className="tiny dim" style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.description}</div>
                  </td>
                  <td><SeverityBadge value={r.severity} /></td>
                  <td><Badge tone="neutral" plain>{r.probability}</Badge></td>
                  <td><Badge tone="neutral" plain>{r.impact}</Badge></td>
                  <td>
                    {r.evidence ? (
                      <div className="tiny dim" style={{ maxWidth: 230, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        <FileText size={11} style={{ verticalAlign: 'middle', marginRight: 5 }} />{r.evidence}
                      </div>
                    ) : (
                      <span className="tiny" style={{ color: 'var(--amber)' }}>Evidence not found in uploaded documents.</span>
                    )}
                  </td>
                  <td><Badge tone={statusTone(r.status)}>{r.status}</Badge></td>
                  <td>
                    <div className="cell-actions">
                      {r.status === 'Open' && <button className="btn btn-secondary btn-sm" onClick={() => setStatus(r, 'Mitigated')}>Mitigate</button>}
                      {r.status === 'In Progress' && <button className="btn btn-secondary btn-sm" onClick={() => setStatus(r, 'Mitigated')}>Mitigate</button>}
                      {r.status !== 'Closed' && <button className="btn btn-ghost btn-sm" onClick={() => setStatus(r, 'Closed')}>Close</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="Add Risk" footer={
        <>
          <button className="btn btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
          <button className="btn btn-primary" onClick={save}>Save Risk</button>
        </>
      }>
        <div className="col">
          <div className="field">
            <label>Title</label>
            <input className="input" value={form.title || ''} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Risk title" />
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
              <label>Probability</label>
              <select className="select" value={form.probability} onChange={(e) => setForm({ ...form, probability: e.target.value })}>
                {['Low', 'Medium', 'High'].map((s) => <option key={s}>{s}</option>)}
              </select>
            </div>
            <div className="field">
              <label>Impact</label>
              <select className="select" value={form.impact} onChange={(e) => setForm({ ...form, impact: e.target.value })}>
                {['Low', 'Medium', 'High'].map((s) => <option key={s}>{s}</option>)}
              </select>
            </div>
          </div>
          <div className="field">
            <label>Recommended action</label>
            <input className="input" value={form.recommended_action || ''} onChange={(e) => setForm({ ...form, recommended_action: e.target.value })} />
          </div>
        </div>
      </Modal>
    </div>
  );
}