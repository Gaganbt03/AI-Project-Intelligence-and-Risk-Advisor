import { useCallback, useEffect, useState } from 'react';
import { ScrollText, ShieldCheck, FilterX } from 'lucide-react';
import { api } from '../api/client';
import { AppShell } from '../layout/AppShell';
import { EmptyState, PageLoader } from '../components/EmptyState';
import { Badge } from '../components/Badge';
import { useToast } from '../ui/ToastContext';
import { fmtDate } from '../utils/format';

const ACTION_TONES: Record<string, any> = {
  login: 'ok',
  login_failed: 'bad',
  logout: 'neutral',
  create: 'cyan',
  update: 'violet',
  delete: 'bad',
  upload: 'cyan',
  download: 'neutral',
  analysis: 'magenta',
};

export default function AuditLogs() {
  const [logs, setLogs] = useState<any>({ total: 0, items: [] });
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState<any>({ action: '' });
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const params: Record<string, any> = {};
      if (filters.action) params.action = filters.action;
      setLogs(await api.auditLogs({ limit: 250, ...params }));
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load audit logs.');
    } finally {
      setLoading(false);
    }
  }, [filters, toast]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <AppShell title="Audit Logs" crumb="Settings · Security"><PageLoader label="Loading audit logs…" /></AppShell>;

  return (
    <AppShell title="Audit Logs" crumb="Settings · Security">
      <div className="panel">
        <div className="panel-head">
          <h3><ShieldCheck size={16} /> Security Events</h3>
          <span className="ph-sub">{logs.total} events recorded · newest first</span>
        </div>
        <div className="panel-head" style={{ borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)' }}>
          <div className="row gap-sm">
            <div className="field" style={{ minWidth: 220 }}>
              <select className="select" value={filters.action} onChange={(e) => setFilters({ action: e.target.value })}>
                <option value="">All actions</option>
                {Array.from(new Set((logs.items as any[]).map((l: any) => l.action))).map((a) => (
                  <option key={String(a)} value={String(a)}>{String(a)}</option>
                ))}
              </select>
            </div>
            {filters.action && (
              <button className="btn btn-secondary btn-sm" onClick={() => setFilters({ action: '' })}><FilterX size={14} /> Clear</button>
            )}
          </div>
        </div>
        <div className="panel-body">
          {logs.items.length === 0 ? (
            <EmptyState icon={<ScrollText size={26} />} title="No audit events yet" description="Sign-ins, document uploads, AI analysis runs and entitlement changes are captured here automatically." />
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr><th>When</th><th>User</th><th>Action</th><th>Resource</th><th>Detail</th><th>IP</th></tr>
                </thead>
                <tbody>
                  {logs.items.map((l: any) => (
                    <tr key={l.id}>
                      <td className="muted small" style={{ whiteSpace: 'nowrap' }}>{fmtDate(l.created_at)}</td>
                      <td className="muted small">{l.user_email || `#${l.user_id}`}</td>
                      <td><Badge tone={ACTION_TONES[l.action] || 'neutral'}>{l.action}</Badge></td>
                      <td className="small dim">
                        {l.resource_type}{l.resource_id ? ` #${l.resource_id}` : ''}
                        {l.project_id ? <div className="tiny dim">project #{l.project_id}</div> : null}
                      </td>
                      <td className="muted small" style={{ maxWidth: 360 }}>{l.detail || '—'}</td>
                      <td className="muted tiny" style={{ whiteSpace: 'nowrap' }}>{l.ip_address || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}