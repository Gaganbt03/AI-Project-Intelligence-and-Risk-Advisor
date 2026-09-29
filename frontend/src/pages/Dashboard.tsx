import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  FolderKanban, Users, FileText, TriangleAlert, CircleSlash, Sparkles, Activity,
  Plus, ArrowRight, CheckCircle2, ListChecks, Bot,
} from 'lucide-react';
import { api } from '../api/client';
import { AppShell } from '../layout/AppShell';
import { EmptyState, PageLoader } from '../components/EmptyState';
import { Badge, SeverityBadge, statusTone } from '../components/Badge';
import { useAuth } from '../auth/AuthContext';
import { useToast } from '../ui/ToastContext';
import { timeAgo } from '../utils/format';

export default function Dashboard() {
  const { user } = useAuth();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const toast = useToast();
  const isAdmin = user?.role === 'ADMIN';

  const load = useCallback(async () => {
    try {
      const d = isAdmin ? await api.adminDashboard() : await api.meDashboard();
      setData(d);
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load dashboard.');
    } finally {
      setLoading(false);
    }
  }, [isAdmin, toast]);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return (
      <AppShell title={isAdmin ? 'Admin Dashboard' : 'My Dashboard'} crumb="Home">
        <PageLoader label="Loading dashboard…" />
      </AppShell>
    );
  }

  if (isAdmin) return <AdminDash data={data} onChanged={load} />;
  return <EmployeeDash data={data} onChanged={load} />;
}

function StatCard({ icon, label, value, accent }: { icon: React.ReactNode; label: string; value: any; accent?: string }) {
  return (
    <div className="card stat-card">
      <div className="stat-ico">{icon}</div>
      <div>
        <div className="stat-value">{value ?? 0}</div>
        <div className="stat-label">{label}</div>
      </div>
      {accent && <div className="tiny" style={{ color: accent }}>{accent}</div>}
    </div>
  );
}

function AdminDash({ data, onChanged }: { data: any; onChanged: () => void }) {
  const s = data.summary;
  const hasProjects = s.total_projects > 0;

  return (
    <AppShell title="Admin Dashboard" crumb="Home · Overview" actions={
      <Link to="/projects" className="btn btn-primary btn-sm"><Plus size={15} /> New Project</Link>
    }>
      <div className="grid grid-4">
        <StatCard icon={<FolderKanban size={18} />} label="Total Projects" value={s.total_projects} />
        <StatCard icon={<UploadActive />} label="Active Projects" value={s.active_projects} accent={s.active_projects ? emeraldDot('live') : undefined} />
        <StatCard icon={<Users size={18} />} label="Team Members" value={s.team_members} />
        <StatCard icon={<FileText size={18} />} label="Documents" value={s.documents} />
        <StatCard icon={<TriangleAlert size={18} />} label="Open Risks" value={s.open_risks} />
        <StatCard icon={<TriangleAlert size={18} />} label="Critical Risks" value={s.critical_risks} accent={s.critical_risks ? redDot(`${s.critical_risks} need attention`) : undefined} />
        <StatCard icon={<CircleSlash size={18} />} label="Open Blockers" value={s.open_blockers} />
        <StatCard icon={<Activity size={18} />} label="Projects With Analysis" value={data.projects.filter((p: any) => p.has_analysis).length} />
      </div>

      {!hasProjects && (
        <EmptyState
          icon={<FolderKanban size={26} />}
          title="No projects created yet"
          description="Create your first project to start building your project intelligence workspace."
          action={<Link to="/projects" className="btn btn-primary"><Plus size={16} /> Create Project</Link>}
        />
      )}

      {hasProjects && (
        <div className="panel">
          <div className="panel-head"><h3>Project Health Overview</h3><span className="ph-sub">real metrics only · no fabricated scores</span></div>
          <div className="table-wrap" style={{ border: 'none', borderRadius: 0 }}>
            <table className="data">
              <thead>
                <tr>
                  <th>Project</th><th>Status</th><th>Priority</th><th>Risk Level</th><th>Open Risks</th><th>Health Basis</th><th style={{ textAlign: 'right' }} />
                </tr>
              </thead>
              <tbody>
                {data.projects.map((row: any) => (
                  <tr key={row.project.id}>
                    <td>
                      <Link to={`/projects/${row.project.id}`} className="cell-strong" style={{ color: 'var(--violet-2)' }}>{row.project.name}</Link>
                      <div className="tiny dim">{row.project.member_count} members · {row.project.document_count} documents</div>
                    </td>
                    <td><Badge tone={statusTone(row.project.status)}>{row.project.status}</Badge></td>
                    <td><SeverityBadge value={row.project.priority} /></td>
                    <td><Badge tone={riskTone(row.risk_level)} plain>{row.risk_level}</Badge></td>
                    <td>
                      <b style={{ color: row.open_risks ? 'var(--amber)' : 'var(--emerald)' }}>{row.open_risks}</b>
                    </td>
                    <td className="tiny dim">{row.has_analysis ? 'Analysis run ✓' : 'No analysis yet'}</td>
                    <td style={{ textAlign: 'right' }}><Link to={`/projects/${row.project.id}`} className="btn btn-secondary btn-sm">Open <ArrowRight size={13} /></Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="grid grid-2">
        <div className="panel">
          <div className="panel-head"><h3><Sparkles size={15} /> Recent AI Insights</h3></div>
          <div className="panel-body col" style={{ gap: 4 }}>
            {data.recent_insights.length === 0 ? (
              <div className="tiny dim" style={{ padding: 14 }}>No AI insights generated yet. Upload documents and run analysis on a project.</div>
            ) : (
              data.recent_insights.slice(0, 6).map((i: any) => (
                <div key={i.id} className="activity-item">
                  <span className="activity-dot" />
                  <div className="col gap-sm" style={{ gap: 3 }}>
                    <div className="small"><b>{titleFor(i)}</b> <span className="dim tiny">· {agentLabel(i.agent)}</span></div>
                    <div className="tiny dim">{i.summary?.slice(0, 140) || i.title}</div>
                    <Link to={`/projects/${i.project_id}`} className="tiny" style={{ color: 'var(--violet-2)' }}>View project →</Link>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="panel">
          <div className="panel-head"><h3>Recent Activity</h3></div>
          <div className="panel-body col" style={{ gap: 4 }}>
            {data.recent_activity.length === 0 ? (
              <div className="tiny dim" style={{ padding: 14 }}>System events will appear here as the workspace is used.</div>
            ) : (
              data.recent_activity.map((a: any) => (
                <div key={a.id} className="activity-item">
                  <span className="activity-dot" />
                  <div className="col gap-sm" style={{ gap: 3 }}>
                    <div className="small"><b>{beautify(a.action)}</b> <span className="dim tiny">· {a.user_email || 'system'}</span></div>
                    {a.detail && <div className="tiny dim">{a.detail}</div>}
                    <div className="tiny dim">{timeAgo(a.created_at)}</div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}

function EmployeeDash({ data, onChanged }: { data: any; onChanged: () => void }) {
  const hasProjects = data.projects.length > 0;

  return (
    <AppShell title="My Dashboard" crumb="Home · My Work">
      <div className="grid grid-4">
        <StatCard icon={<FolderKanban size={18} />} label="My Projects" value={data.projects.length} />
        <StatCard icon={<ListChecks size={18} />} label="Pending Tasks" value={data.pending_action_items} />
        <StatCard icon={<CheckCircle2 size={18} />} label="Completed Tasks" value={data.completed_tasks} />
        <StatCard icon={<TriangleAlert size={18} />} label="Open Risks" value={data.open_risks} />
        <StatCard icon={<CircleSlash size={18} />} label="Open Blockers" value={data.open_blockers} />
        <StatCard icon={<FileText size={18} />} label="Recent Documents" value={data.recent_documents.length} />
        <StatCard icon={<Activity size={18} />} label="Open Action Items" value={data.my_tasks.filter((t: any) => ['Pending', 'In Progress'].includes(t.status)).length} />
        <StatCard icon={<Bot size={18} />} label="Assigned Risks" value={data.open_risks} />
      </div>

      {!hasProjects && (
        <EmptyState
          icon={<FolderKanban size={26} />}
          title="No projects assigned yet"
          description="Once an administrator assigns projects to you, your projects, tasks, documents and AI insights will appear here."
        />
      )}

      <div className="grid grid-2">
        <div className="panel">
          <div className="panel-head"><h3><ListChecks size={15} /> My Tasks</h3></div>
          <div className="panel-body col" style={{ gap: 8 }}>
            {data.my_tasks.length === 0 ? (
              <div className="tiny dim" style={{ padding: 14 }}>No tasks assigned to you.</div>
            ) : (
              data.my_tasks.slice(0, 8).map((t: any) => (
                <Link key={t.id} to={`/projects/${t.project_id}`} className="card card-flat row-between" style={{ padding: 12 }}>
                  <div className="col gap-sm" style={{ gap: 3 }}>
                    <span className="small cell-strong">{t.title}</span>
                    <span className="tiny dim">Project #{t.project_id}{t.due_date ? ` · due ${t.due_date.slice(0, 10)}` : ''}</span>
                  </div>
                  <Badge tone={statusTone(t.status)}>{t.status}</Badge>
                </Link>
              ))
            )}
          </div>
        </div>

        <div className="panel">
          <div className="panel-head"><h3><Sparkles size={15} /> Recent AI Insights</h3></div>
          <div className="panel-body col" style={{ gap: 4 }}>
            {data.recent_insights.length === 0 ? (
              <div className="tiny dim" style={{ padding: 14 }}>No AI insights for your projects yet.</div>
            ) : (
              data.recent_insights.slice(0, 6).map((i: any) => (
                <div key={i.id} className="activity-item">
                  <span className="activity-dot" />
                  <div className="col gap-sm" style={{ gap: 3 }}>
                    <div className="small"><b>{titleFor(i)}</b> <span className="dim tiny">· {agentLabel(i.agent)}</span></div>
                    <Link to={`/projects/${i.project_id}`} className="tiny" style={{ color: 'var(--violet-2)' }}>Open project →</Link>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head"><h3><FileText size={15} /> Recent Documents</h3></div>
        <div className="panel-body col" style={{ gap: 8 }}>
          {data.recent_documents.length === 0 ? (
            <div className="tiny dim" style={{ padding: 14 }}>No documents have been uploaded to your projects yet.</div>
          ) : (
            data.recent_documents.map((d: any) => (
              <Link key={d.id} to={`/projects/${d.project_id}`} className="card card-flat row-between" style={{ padding: 12 }}>
                <div className="row gap-sm">
                  <div className={`doc-row-file ft-${d.file_type || 'txt'}`}>{d.file_type?.toUpperCase()}</div>
                  <div className="col gap-sm" style={{ gap: 2 }}>
                    <span className="small cell-strong">{d.original_name}</span>
                    <span className="tiny dim">{timeAgo(d.uploaded_at)}</span>
                  </div>
                </div>
                <Badge tone={statusTone(d.status)}>{d.status}</Badge>
              </Link>
            ))
          )}
        </div>
      </div>
    </AppShell>
  );
}

// ---------- helpers ----------
function UploadActive() {
  return <Activity size={18} />;
}
function emeraldDot(t: string) {
  return `● ${t}`;
}
function redDot(t: string) {
  return `● ${t}`;
}
function agentLabel(a: string) {
  const map: Record<string, string> = { scope: 'Scope', risk: 'Risk', forecast: 'Forecast', blocker: 'Blocker', action: 'Action Item', health: 'Health' };
  return map[a] || a;
}
function titleFor(i: any) {
  if (i.title) return i.title;
  const map: Record<string, string> = {
    project_goal: 'Project Goal',
    scope: 'Scope Item',
    out_of_scope: 'Out of Scope',
    deliverable: 'Deliverable',
    milestone: 'Milestone',
    timeline: 'Timeline',
    responsibility: 'Responsibility',
    technology: 'Technology',
    requirement: 'Requirement',
  };
  return map[i.category] || i.category || 'Insight';
}
function riskTone(v: string) {
  const s = (v || '').toLowerCase();
  if (s.includes('high')) return 'high';
  if (s.includes('medium')) return 'medium';
  if (s.includes('low')) return 'low';
  return 'neutral';
}
function beautify(action: string) {
  return action
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}