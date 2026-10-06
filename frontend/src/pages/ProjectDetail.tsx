import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft, FolderKanban, FileText, ListChecks, TriangleAlert, CircleSlash,
  Sparkles, Bot, ArrowRight, Calendar, Activity, BookOpen,
} from 'lucide-react';
import { api } from '../api/client';
import { AppShell } from '../layout/AppShell';
import { PageLoader } from '../components/EmptyState';
import { Badge, SeverityBadge, statusTone } from '../components/Badge';
import { useToast } from '../ui/ToastContext';
import { useAuth } from '../auth/AuthContext';
import { fmtDateOnly } from '../utils/format';
import { DocsPanel } from '../panels/DocsPanel';
import { TasksPanel } from '../panels/TasksPanel';
import { RisksPanel } from '../panels/RisksPanel';
import { BlockersPanel } from '../panels/BlockersPanel';
import { InsightsPanel } from '../panels/InsightsPanel';
import { AssistantPanel } from '../panels/AssistantPanel';
import { HealthPanel } from '../panels/HealthPanel';
import { GeneratedDocsPanel } from '../panels/GeneratedDocsPanel';

const TABS = [
  { key: 'overview', label: 'Overview', icon: <FolderKanban size={15} /> },
  { key: 'health', label: 'Health', icon: <Activity size={15} /> },
  { key: 'documents', label: 'Documents', icon: <FileText size={15} /> },
  { key: 'generated', label: 'Generated Docs', icon: <BookOpen size={15} /> },
  { key: 'tasks', label: 'Tasks', icon: <ListChecks size={15} /> },
  { key: 'risks', label: 'Risks', icon: <TriangleAlert size={15} /> },
  { key: 'blockers', label: 'Blockers', icon: <CircleSlash size={15} /> },
  { key: 'insights', label: 'AI Insights', icon: <Sparkles size={15} /> },
  { key: 'assistant', label: 'Assistant', icon: <Bot size={15} /> },
];

export default function ProjectDetail() {
  const { id } = useParams();
  const pid = Number(id);
  const { user } = useAuth();
  const isAdmin = true;
  const [project, setProject] = useState<any>(null);
  const [allUsers, setAllUsers] = useState<any[]>([]);
  const [tab, setTab] = useState('overview');
  const [loading, setLoading] = useState(true);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const p = await api.getProject(pid);
      setProject(p);
      if (isAdmin) {
        const us = await api.listUsers();
        setAllUsers(us);
      }
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load project.');
    } finally {
      setLoading(false);
    }
  }, [pid, isAdmin, toast]);

  useEffect(() => { load(); }, [load]);

  // Assignee options for task/blocker pickers. This is record data, not a team view.
  const assignees = useMemo(
    () => allUsers.filter((u) => (project?.member_ids || []).includes(u.id)),
    [allUsers, project],
  );

  const refreshInsights = useCallback(() => { load(); }, [load]);

  if (loading) return <AppShell title="…" crumb="Projects"><PageLoader label="Loading project…" /></AppShell>;
  if (!project) {
    return (
      <AppShell title="Project not found" crumb="Projects">
        <EmptyGhost onBack={() => {}} />
      </AppShell>
    );
  }

  const overview = (
    <div className="col">
      <div className="grid grid-3">
        <div className="card col" style={{ gap: 10 }}>
          <div className="eyebrow">Objective</div>
          <div className="small" style={{ lineHeight: 1.6 }}>{project.objective || 'No objective recorded yet.'}</div>
        </div>
        <div className="card col" style={{ gap: 10 }}>
          <div className="eyebrow">Description</div>
          <div className="small" style={{ lineHeight: 1.6 }}>{project.description || 'No description recorded yet.'}</div>
        </div>
        <div className="card col" style={{ gap: 12 }}>
          <div className="row-between"><span className="eyebrow">Manager</span><span className="small">{project.manager_name || 'Unassigned'}</span></div>
          <div className="row-between"><span className="eyebrow">Dates</span><span className="small"><Calendar size={12} style={{ verticalAlign: 'middle' }} /> {project.start_date ? fmtDateOnly(project.start_date) : '—'} → {project.expected_end_date ? fmtDateOnly(project.expected_end_date) : '—'}</span></div>
          <div className="row wrap gap-sm">
            <Badge tone={statusTone(project.status)}>{project.status}</Badge>
            <SeverityBadge value={project.priority} />
            </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head"><h3>Quick Actions</h3></div>
        <div className="panel-body grid grid-3" style={{ gap: 10 }}>
          <QuickLink icon={<FileText size={16} />} text="Upload documents" onClick={() => setTab('documents')} />
          <QuickLink icon={<ListChecks size={16} />} text="Manage tasks" onClick={() => setTab('tasks')} />
          <QuickLink icon={<Activity size={16} />} text="Check project health" onClick={() => setTab('health')} />
          <QuickLink icon={<BookOpen size={16} />} text="Generate documentation" onClick={() => setTab('generated')} />
          <QuickLink icon={<Sparkles size={16} />} text="Run AI analysis" onClick={() => setTab('insights')} />
          <QuickLink icon={<Bot size={16} />} text="Ask the assistant" onClick={() => setTab('assistant')} />
          <QuickLink icon={<TriangleAlert size={16} />} text="Review risks" onClick={() => setTab('risks')} />
        </div>
      </div>
    </div>
  );

  return (
    <AppShell
      title={project.name}
      crumb={`Projects · ${project.status}`}
      actions={
        <>
          {isAdmin && tab === 'insights' && <></>}
        </>
      }
    >
      <div className="row-between wrap" style={{ gap: 10 }}>
        <div className="row gap-sm">
          <Link to="/projects" className="btn btn-ghost btn-icon" title="Back"><ArrowLeft size={16} /></Link>
          <div className="col gap-sm" style={{ gap: 4 }}>
            <h1>{project.name}</h1>
            <div className="row wrap gap-sm">
              <Badge tone={statusTone(project.status)}>{project.status}</Badge>
              <SeverityBadge value={project.priority} />
              <span className="tiny dim">Created {new Date(project.created_at).toLocaleDateString()}</span>
            </div>
          </div>
        </div>
        <Link to="/projects" className="btn btn-secondary btn-sm"><ArrowRight size={14} /> All Projects</Link>
      </div>

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.key} className={`tab ${tab === t.key ? 'active' : ''}`} onClick={() => setTab(t.key)}>
            {t.icon}
            <span style={{ marginLeft: 7 }}>{t.label}</span>
          </button>
        ))}
      </div>

      {tab === 'overview' && overview}
      {tab === 'health' && <HealthPanel projectId={pid} />}
      {tab === 'documents' && <DocsPanel projectId={pid} />}
      {tab === 'generated' && <GeneratedDocsPanel projectId={pid} />}
      {tab === 'tasks' && <TasksPanel projectId={pid} users={assignees.map((u) => ({ id: u.id, name: u.name }))} />}
      {tab === 'risks' && <RisksPanel projectId={pid} />}
      {tab === 'blockers' && <BlockersPanel projectId={pid} employees={assignees.map((u) => ({ id: u.id, name: u.name }))} />}
      {tab === 'insights' && <InsightsPanel projectId={pid} onRunDone={refreshInsights} />}
      {tab === 'assistant' && <AssistantPanel projectId={pid} />}
    </AppShell>
  );
}

function QuickLink({ icon, text, onClick }: { icon: React.ReactNode; text: string; onClick: () => void }) {
  return (
    <button className="card card-flat row gap-sm" onClick={onClick} style={{ padding: 15, textAlign: 'left' }}>
      <span style={{ color: 'var(--violet-2)' }}>{icon}</span>
      <span className="small cell-strong">{text}</span>
      <ArrowRight size={13} style={{ marginLeft: 'auto', color: 'var(--text-3)' }} />
    </button>
  );
}

function EmptyGhost({ onBack }: { onBack: () => void }) {
  return (
    <div className="empty">
      <div className="empty-ico"><FolderKanban size={26} /></div>
      <h3>Project not found or inaccessible</h3>
      <button className="btn btn-secondary" onClick={onBack}>Back</button>
    </div>
  );
}