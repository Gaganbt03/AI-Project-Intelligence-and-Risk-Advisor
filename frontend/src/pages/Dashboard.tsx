import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  FolderKanban, Users, FileText, TriangleAlert, CircleSlash, Sparkles, Activity,
  Plus, ArrowRight, ListChecks, Cpu, HeartPulse, Gauge, CalendarClock,
} from 'lucide-react';
import { api } from '../api/client';
import type { AiProviderStatus } from '../api/client';
import { AppShell } from '../layout/AppShell';
import { EmptyState, PageLoader } from '../components/EmptyState';
import { Badge, SeverityBadge, statusTone } from '../components/Badge';
import { useToast } from '../ui/ToastContext';
import { timeAgo } from '../utils/format';

export default function Dashboard() {
  const [data, setData] = useState<any>(null);
  const [ai, setAi] = useState<AiProviderStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const [d, a] = await Promise.all([
        api.dashboard(),
        api.aiProviderStatus().catch(() => null),
      ]);
      setData(d);
      setAi(a);
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load dashboard.');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return (
      <AppShell title="Dashboard" crumb="Home">
        <PageLoader label="Loading dashboard…" />
      </AppShell>
    );
  }

  return (
    <AppShell title="AI Project Intelligence & Risk Advisor" crumb="Home · Overview" actions={
      <Link to="/projects" className="btn btn-primary btn-sm"><Plus size={15} /> New Project</Link>
    }>
      <div className="col">
        <PortfolioStats data={data} />
        <AiStatusStrip ai={ai} />

        {data.projects.length === 0 ? (
          <div className="panel">
            <EmptyState
              icon={<FolderKanban size={26} />}
              title="No projects yet"
              description="Create a project to start uploading documents and building your project intelligence workspace."
              action={<Link to="/projects" className="btn btn-primary"><Plus size={16} /> Create Project</Link>}
            />
          </div>
        ) : (
          <>
            <ProjectHealth data={data} />
            <div className="grid grid-2">
              <TaskProgress data={data} />
              <DeliveryForecast data={data} />
            </div>
            <Recommendations data={data} />
          </>
        )}

        <div className="grid grid-2">
          <RecentInsights data={data} />
          <RecentDocuments data={data} />
        </div>

        <RecentActivity data={data} />
      </div>
    </AppShell>
  );
}

/* ------------------------------------------------------------------ *
 * Portfolio summary
 * ------------------------------------------------------------------ */
function PortfolioStats({ data }: { data: any }) {
  const s = data.summary;
  const analysed = data.projects.filter((p: any) => p.has_analysis).length;
  return (
    <div className="grid grid-4">
      <StatCard icon={<FolderKanban size={18} />} label="Projects" value={s.total_projects} />
      <StatCard icon={<Activity size={18} />} label="Active Projects" value={s.active_projects} accent={s.active_projects ? `● ${s.active_projects} running` : undefined} />

      <StatCard icon={<FileText size={18} />} label="Documents" value={s.documents} />
      <StatCard icon={<HeartPulse size={18} />} label="Projects Analysed" value={analysed} />
      <StatCard icon={<TriangleAlert size={18} />} label="Open Risks" value={s.open_risks} accent={s.critical_risks ? `● ${s.critical_risks} critical` : undefined} />
      <StatCard icon={<CircleSlash size={18} />} label="Open Blockers" value={s.open_blockers} />
      <StatCard icon={<Gauge size={18} />} label="My Open Tasks" value={data.pending_action_items} />
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Active AI provider / model — read from the live provider chain.
 * No key material is ever requested or rendered here.
 * ------------------------------------------------------------------ */
function AiStatusStrip({ ai }: { ai: AiProviderStatus | null }) {
  if (!ai || !ai.providers?.length) {
    return (
      <div className="panel">
        <div className="panel-head"><h3><Cpu size={15} /> AI Provider</h3></div>
        <div className="panel-body tiny dim">Provider status is unavailable right now.</div>
      </div>
    );
  }

  const inChain = ai.providers.filter((p) => p.in_chain);
  const active = inChain.find((p) => p.healthy) || inChain[0];
  const fallbacks = inChain.filter((p) => p.key !== active?.key);

  return (
    <div className="panel">
      <div className="panel-head">
        <h3><Cpu size={15} /> AI Provider</h3>
        <span className="ph-sub">live status from the server · no API keys are ever sent to the browser</span>
      </div>
      <div className="panel-body col" style={{ gap: 12 }}>
        <div className="row gap-sm wrap">
          <span className={`dot ${active?.healthy ? 'dot-ok' : 'dot-bad'}`} />
          <div className="col gap-sm" style={{ gap: 2 }}>
            <div className="row gap-sm wrap">
              <b>{active?.name ?? 'Unknown'}</b>
              <Badge tone={active?.healthy ? 'ok' : 'err'} plain>{active?.healthy ? 'Active' : 'Unavailable'}</Badge>
              <Badge tone="violet" plain>{active?.kind === 'ollama' ? 'Local' : 'Cloud'}</Badge>
              {active?.model_source === 'discovered' && <Badge tone="cyan" plain>Model auto-discovered</Badge>}
            </div>
            <span className="tiny dim">Model: {active?.model || 'not set'}</span>
          </div>
          <div style={{ marginLeft: 'auto' }}>
            <Link to="/ai-settings" className="btn btn-secondary btn-sm">AI Settings <ArrowRight size={13} /></Link>
          </div>
        </div>

        <div className="row gap-sm wrap">
          <span className="tiny dim" style={{ minWidth: 92 }}>Fallback chain</span>
          {fallbacks.length === 0 ? (
            <span className="tiny dim">No fallback provider configured.</span>
          ) : (
            fallbacks.map((p) => (
              <Badge key={p.key} tone={p.breaker_open ? 'err' : p.healthy ? 'ok' : 'neutral'} plain>
                {p.name}{p.model ? ` · ${p.model}` : ''}{p.breaker_open ? ' · circuit open' : ''}
              </Badge>
            ))
          )}
        </div>

        <div className="row gap-sm wrap">
          <span className="tiny dim" style={{ minWidth: 92 }}>Embeddings</span>
          <Badge tone={ai.embedding.healthy ? 'ok' : 'err'} plain>
            {ai.embedding.provider} · {ai.embedding.model}
            {ai.embedding.dimension ? ` · ${ai.embedding.dimension}-d` : ''}
          </Badge>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Project health overview — deterministic scores, no fabricated numbers
 * ------------------------------------------------------------------ */
function ProjectHealth({ data }: { data: any }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h3><HeartPulse size={15} /> Project Health</h3>
        <span className="ph-sub">live project data · open a project’s Health tab for the full score breakdown</span>
      </div>
      <div className="table-wrap" style={{ border: 'none', borderRadius: 0 }}>
        <table className="data">
          <thead>
            <tr>
              <th>Project</th><th>Status</th><th>Priority</th><th>Risk Level</th>
              <th>Open Risks</th><th>Blockers</th><th>Task Completion</th>
              <th>Analysis</th><th style={{ textAlign: 'right' }} />
            </tr>
          </thead>
          <tbody>
            {data.projects.map((row: any) => {
              const h = row.health || {};
              return (
                <tr key={row.project.id}>
                  <td>
                    <Link to={`/projects/${row.project.id}`} className="cell-strong" style={{ color: 'var(--violet-2)' }}>{row.project.name}</Link>
                    <div className="tiny dim">{row.project.member_count} members · {row.project.document_count} documents</div>
                  </td>
                  <td><Badge tone={statusTone(row.project.status)}>{row.project.status}</Badge></td>
                  <td><SeverityBadge value={row.project.priority} /></td>
                  <td><Badge tone={riskTone(row.risk_level)} plain>{row.risk_level}</Badge></td>
                  <td>
                    <b style={{ color: row.open_risks ? 'var(--amber)' : 'var(--emerald)' }}>{row.open_risks ?? 0}</b>
                  </td>
                  <td>
                    <b style={{ color: h.open_blockers ? 'var(--amber)' : 'var(--emerald)' }}>{h.open_blockers ?? 0}</b>
                  </td>
                  <td className="tiny">{completionLabel(h)}</td>
                  <td className="tiny dim">{row.has_analysis ? 'Analysed' : 'Not analysed'}</td>
                  <td style={{ textAlign: 'right' }}>
                    <Link to={`/projects/${row.project.id}`} className="btn btn-secondary btn-sm">Open <ArrowRight size={13} /></Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function completionLabel(h: any) {
  if (!h || !h.total_tasks) return 'No tasks';
  const rate = h.task_completion_rate ?? 0;
  return `${rate}% of ${h.total_tasks}`;
}

function TaskProgress({ data }: { data: any }) {
  const mine = data.my_tasks;
  const open = mine.filter((t: any) => ['Pending', 'In Progress'].includes(t.status));
  const done = data.completed_tasks;
  const total = mine.length;

  return (
    <div className="panel">
      <div className="panel-head"><h3><ListChecks size={15} /> Task Progress</h3><span className="ph-sub">assigned to you</span></div>
      <div className="panel-body col" style={{ gap: 8 }}>
        <div className="row gap-sm">
          <StatCard icon={<ListChecks size={18} />} label="Open" value={open.length} />
          <StatCard icon={<ListChecks size={18} />} label="Completed" value={done} />
        </div>
        {total > 0 && (
          <div className="col gap-sm" style={{ gap: 5 }}>
            <div className="meter"><div className="meter-fill ok" style={{ width: `${Math.round(done / total * 100)}%` }} /></div>
            <span className="tiny dim">{Math.round(done / total * 100)}% of your {total} assigned tasks complete</span>
          </div>
        )}
        {mine.length === 0 ? (
          <div className="tiny dim">No tasks assigned to you yet.</div>
        ) : (
          <div className="col" style={{ gap: 6 }}>
            {mine.slice(0, 6).map((t: any) => (
              <Link key={t.id} to={`/projects/${t.project_id}`} className="card card-flat row-between" style={{ padding: 10 }}>
                <div className="col gap-sm" style={{ gap: 3, minWidth: 0 }}>
                  <span className="small cell-strong">{t.title}</span>
                  <span className="tiny dim">{t.due_date ? `Due ${t.due_date.slice(0, 10)}` : 'No due date'}</span>
                </div>
                <Badge tone={statusTone(t.status)}>{t.status}</Badge>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function DeliveryForecast({ data }: { data: any }) {
  const rows = data.projects
    .filter((p: any) => p.health?.schedule_status)
    .map((p: any) => ({ id: p.project.id, name: p.project.name, schedule: p.health.schedule_status }));

  return (
    <div className="panel">
      <div className="panel-head"><h3><CalendarClock size={15} /> Delivery Forecast</h3><span className="ph-sub">from the latest analysis run</span></div>
      <div className="panel-body col" style={{ gap: 8 }}>
        {rows.length === 0 ? (
          <div className="tiny dim">No delivery forecast yet. Run an analysis on a project to generate one.</div>
        ) : (
          rows.map((r: any) => (
            <Link key={r.id} to={`/projects/${r.id}`} className="card card-flat row-between" style={{ padding: 10 }}>
              <span className="small cell-strong">{r.name}</span>
              <Badge tone={forecastTone(r.schedule)} plain>{r.schedule}</Badge>
            </Link>
          ))
        )}
      </div>
    </div>
  );
}

/**
 * Every line below is derived from counts the dashboard payload already
 * contains. Nothing is invented and no score is fabricated.
 */
function Recommendations({ data }: { data: any }) {
  const s = data.summary;
  const items: { tone: 'err' | 'warn' | 'ok'; text: string; to: string }[] = [];

  if (s.critical_risks > 0) {
    items.push({
      tone: 'err',
      text: `${s.critical_risks} critical risk${s.critical_risks === 1 ? '' : 's'} are open and need an owner.`,
      to: '/risks',
    });
  }
  if (s.open_blockers > 0) {
    items.push({
      tone: 'warn',
      text: `${s.open_blockers} open blocker${s.open_blockers === 1 ? '' : 's'} are delaying delivery.`,
      to: '/blockers',
    });
  }
  const unanalysed = data.projects.filter((p: any) => !p.has_analysis);
  if (unanalysed.length > 0) {
    items.push({
      tone: 'warn',
      text: `${unanalysed.length} project${unanalysed.length === 1 ? '' : 's'} not analysed yet: ${unanalysed.map((p: any) => p.project.name).join(', ')}.`,
      to: '/insights',
    });
  }
  const undocumented = data.projects.filter((p: any) => (p.project.document_count ?? 0) === 0);
  if (undocumented.length > 0) {
    items.push({
      tone: 'warn',
      text: `${undocumented.length} project${undocumented.length === 1 ? '' : 's'} have no documents uploaded, so analysis quality is limited.`,
      to: '/documents',
    });
  }
  const behind = data.projects.filter((p: any) => {
    const sch = (p.health?.schedule_status || '').toLowerCase();
    return sch.includes('at risk') || sch.includes('significant');
  });
  if (behind.length > 0) {
    items.push({
      tone: 'warn',
      text: `Delivery forecast flags ${behind.length} project${behind.length === 1 ? '' : 's'} at risk: ${behind.map((p: any) => p.project.name).join(', ')}.`,
      to: '/insights',
    });
  }
  if (items.length === 0) {
    items.push({
      tone: 'ok',
      text: s.total_projects === 0
        ? 'Create a project to get started.'
        : 'No outstanding risks, blockers or unanalysed projects right now.',
      to: '/projects',
    });
  }

  return (
    <div className="panel">
      <div className="panel-head"><h3><Sparkles size={15} /> Recommendations</h3><span className="ph-sub">derived from your current project data</span></div>
      <div className="panel-body col" style={{ gap: 8 }}>
        {items.map((it, i) => (
          <Link key={i} to={it.to} className="card card-flat row gap-sm" style={{ padding: 11 }}>
            <Badge tone={it.tone} plain>{it.tone === 'ok' ? 'All clear' : it.tone === 'err' ? 'Act now' : 'Review'}</Badge>
            <span className="small">{it.text}</span>
            <ArrowRight size={14} style={{ marginLeft: 'auto', color: 'var(--violet-2)' }} />
          </Link>
        ))}
      </div>
    </div>
  );
}

function RecentInsights({ data }: { data: any }) {
  return (
    <div className="panel">
      <div className="panel-head"><h3><Sparkles size={15} /> Recent AI Insights</h3></div>
      <div className="panel-body col" style={{ gap: 4 }}>
        {data.recent_insights.length === 0 ? (
          <div className="tiny dim" style={{ padding: 14 }}>
            No AI insights yet. Upload documents to a project and run an analysis.
          </div>
        ) : (
          data.recent_insights.slice(0, 6).map((i: any) => (
            <div key={i.id} className="activity-item">
              <span className="activity-dot" />
              <div className="col gap-sm" style={{ gap: 3 }}>
                <div className="small"><b>{titleFor(i)}</b> <span className="dim tiny">· {agentLabel(i.agent)}</span></div>
                {i.summary && <div className="tiny dim">{i.summary.slice(0, 140)}</div>}
                <Link to={`/projects/${i.project_id}`} className="tiny" style={{ color: 'var(--violet-2)' }}>View project →</Link>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function RecentDocuments({ data }: { data: any }) {
  return (
    <div className="panel">
      <div className="panel-head"><h3><FileText size={15} /> Recent Documents</h3></div>
      <div className="panel-body col" style={{ gap: 8 }}>
        {data.recent_documents.length === 0 ? (
          <div className="tiny dim" style={{ padding: 14 }}>No documents uploaded yet.</div>
        ) : (
          data.recent_documents.map((d: any) => (
            <Link key={d.id} to={`/projects/${d.project_id}`} className="card card-flat row-between" style={{ padding: 10 }}>
              <div className="row gap-sm" style={{ minWidth: 0 }}>
                <div className={`doc-row-file ft-${d.file_type || 'txt'}`}>{d.file_type?.toUpperCase()}</div>
                <div className="col gap-sm" style={{ gap: 2, minWidth: 0 }}>
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
  );
}

function RecentActivity({ data }: { data: any }) {
  return (
    <div className="panel">
      <div className="panel-head"><h3><Activity size={15} /> Recent Activity</h3></div>
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
  );
}

/* ------------------------------------------------------------------ *
 * helpers
 * ------------------------------------------------------------------ */
function StatCard({ icon, label, value, accent }: { icon: React.ReactNode; label: string; value: any; accent?: string }) {
  return (
    <div className="card stat-card">
      <div className="stat-ico">{icon}</div>
      <div>
        <div className="stat-value">{value ?? 0}</div>
        <div className="stat-label">{label}</div>
      </div>
      {accent && <div className="tiny" style={{ color: 'var(--amber)' }}>{accent}</div>}
    </div>
  );
}

function agentLabel(a: string) {
  const map: Record<string, string> = {
    scope: 'Scope', risk: 'Risk', forecast: 'Forecast',
    blocker: 'Blocker', action: 'Action Item', health: 'Health',
  };
  return map[a] || a;
}

function titleFor(i: any) {
  if (i.title) return i.title;
  const map: Record<string, string> = {
    project_goal: 'Project Goal',
    scope: 'Scope Item',
    out_of_scope: 'Out of Scope',
    deliverable: 'Deliverable',
    milestone: 'Project Milestone',
    timeline: 'Timeline',
    responsibility: 'Responsibility',
    technology: 'Technology',
    requirement: 'Requirement',
  };
  return map[i.category] || i.category || 'Insight';
}

function riskTone(v: string) {
  const s = (v || '').toLowerCase();
  if (s.includes('high') || s.includes('critical')) return 'critical';
  if (s.includes('medium')) return 'medium';
  if (s.includes('low')) return 'low';
  return 'neutral';
}

function forecastTone(v: string) {
  const s = (v || '').toLowerCase();
  if (s.includes('at risk') || s.includes('significant') || s.includes('delay')) return 'critical';
  if (s.includes('minor')) return 'medium';
  if (s.includes('on track')) return 'ok';
  return 'neutral';
}

function beautify(action: string) {
  return action
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}