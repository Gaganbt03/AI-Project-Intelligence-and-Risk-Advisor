import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { FolderKanban, Plus, ArrowRight, Users, FileText, Pencil, Archive } from 'lucide-react';
import { api } from '../api/client';
import { AppShell } from '../layout/AppShell';
import { EmptyState, PageLoader } from '../components/EmptyState';
import { Badge, SeverityBadge, statusTone } from '../components/Badge';
import { Modal } from '../components/Modal';
import { useToast } from '../ui/ToastContext';
import { useAuth } from '../auth/AuthContext';
import { fmtDateOnly } from '../utils/format';

const STATUSES = ['Planning', 'Active', 'On Hold', 'Completed', 'Archived'];
const PRIORITIES = ['Low', 'Medium', 'High', 'Critical'];

export default function Projects() {
  const { user } = useAuth();
  const isAdmin = true;
  const [projects, setProjects] = useState<any[]>([]);
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [form, setForm] = useState<any>({});
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      setProjects(await api.listProjects());
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load projects.');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
    if (isAdmin) {
      api.listUsers().then(setUsers).catch(() => {});
    }
  }, [load, isAdmin]);

  const blank = () => ({
    name: '',
    description: '',
    objective: '',
    manager_id: '',
    start_date: '',
    expected_end_date: '',
    priority: 'Medium',
    status: 'Planning',
    member_ids: [],
  });

  const openCreate = () => { setEditing(null); setForm(blank()); setOpen(true); };
  const openEdit = (p: any) => {
    setEditing(p);
    setForm({
      name: p.name,
      description: p.description || '',
      objective: p.objective || '',
      manager_id: p.manager_id ?? '',
      start_date: p.start_date ? p.start_date.slice(0, 10) : '',
      expected_end_date: p.expected_end_date ? p.expected_end_date.slice(0, 10) : '',
      priority: p.priority,
      status: p.status,
      member_ids: p.member_ids || [],
    });
    setOpen(true);
  };

  const save = async () => {
    if (!form.name?.trim()) return toast.error('Project name is required.');
    const payload = {
      name: form.name.trim(),
      description: form.description || '',
      objective: form.objective || '',
      manager_id: form.manager_id ? Number(form.manager_id) : null,
      start_date: form.start_date || null,
      expected_end_date: form.expected_end_date || null,
      priority: form.priority,
      status: form.status,
      member_ids: (form.member_ids || []).map(Number),
    };
    try {
      if (editing) await api.updateProject(editing.id, payload);
      else await api.createProject(payload);
      toast.success(editing ? 'Project updated.' : 'Project created.');
      setOpen(false);
      load();
    } catch (err: any) {
      toast.error(err?.message || 'Save failed.');
    }
  };

  const archive = async (p: any) => {
    if (!window.confirm(`Archive "${p.name}"? It will be soft-archived and kept for history.`)) return;
    try {
      await api.archiveProject(p.id);
      toast.success('Project archived.');
      load();
    } catch (err: any) {
      toast.error(err?.message || 'Archive failed.');
    }
  };

  const toggleMember = (id: number) => {
    setForm((f: any) => {
      const cur = f.member_ids || [];
      return { ...f, member_ids: cur.includes(id) ? cur.filter((x: number) => x !== id) : [...cur, id] };
    });
  };

  if (loading) return <AppShell title="Projects" crumb="Projects"><PageLoader label="Loading projects…" /></AppShell>;

  const fe = (id: any, v: any) => (form.member_ids || []).includes(Number(id));

  return (
    <AppShell title={isAdmin ? 'Projects' : 'My Projects'} crumb="Projects" actions={
      isAdmin ? <button className="btn btn-primary btn-sm" onClick={openCreate}><Plus size={15} /> Create Project</button> : undefined
    }>
      {projects.length === 0 ? (
        <EmptyState
          icon={<FolderKanban size={26} />}
          title={isAdmin ? 'No projects yet' : 'No projects assigned'}
          description={isAdmin
            ? 'Create your first project to start building your project intelligence workspace. You can upload documents afterwards.'
            : 'Projects assigned to you by an administrator will appear here.'}
          action={isAdmin ? <button className="btn btn-primary" onClick={openCreate}><Plus size={16} /> Create Project</button> : undefined}
        />
      ) : (
        <div className="grid grid-auto">
          {projects.map((p) => (
            <div key={p.id} className="card card-glow col" style={{ gap: 12 }}>
              <div className="row-between wrap" style={{ gap: 8 }}>
                <Badge tone={statusTone(p.status)}>{p.status}</Badge>
                {isAdmin && p.status !== 'Archived' && (
                  <div className="row gap-sm">
                    <button className="btn btn-icon" title="Edit" onClick={() => openEdit(p)}><Pencil size={14} /></button>
                    <button className="btn btn-icon" title="Archive" style={{ color: 'var(--amber)' }} onClick={() => archive(p)}><Archive size={14} /></button>
                  </div>
                )}
              </div>
              <div className="col" style={{ gap: 5 }}>
                <h3 style={{ fontSize: '17px' }}>{p.name}</h3>
                <p className="tiny dim" style={{ lineHeight: 1.55, minHeight: 42 }}>{p.description || p.objective || 'No description yet.'}</p>
              </div>
              <div className="row wrap gap-sm">
                <SeverityBadge value={p.priority} />

                <Badge tone="neutral" plain><FileText size={11} /> {p.document_count} docs</Badge>
              </div>
              <div className="tiny dim">
                {p.start_date ? `${fmtDateOnly(p.start_date)}` : 'No start date'} →{' '}
                {p.expected_end_date ? fmtDateOnly(p.expected_end_date) : '—'}
              </div>
              <div className="divider" />
              <Link to={`/projects/${p.id}`} className="btn btn-secondary btn-sm w-full">Open Project <ArrowRight size={14} /></Link>
            </div>
          ))}
        </div>
      )}

      {isAdmin && (
        <Modal open={open} onClose={() => setOpen(false)} title={editing ? 'Edit Project' : 'Create Project'} wide footer={
          <>
            <button className="btn btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn btn-primary" onClick={save}>{editing ? 'Save Changes' : 'Create Project'}</button>
          </>
        }>
          <div className="col">
            <div className="field">
              <label>Project Name</label>
              <input className="input" value={form.name || ''} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Orion Platform" />
            </div>
            <div className="field">
              <label>Objective</label>
              <textarea className="textarea" value={form.objective || ''} onChange={(e) => setForm({ ...form, objective: e.target.value })} placeholder="High-level objective" style={{ minHeight: 52 }} />
            </div>
            <div className="field">
              <label>Description</label>
              <textarea className="textarea" value={form.description || ''} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>
            <div className="grid grid-2">
              <div className="field">
                <label>Project Manager</label>
                <select className="select" value={form.manager_id ?? ''} onChange={(e) => setForm({ ...form, manager_id: e.target.value ? Number(e.target.value) : '' })}>
                  <option value="">Unassigned</option>
                  {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                </select>
              </div>
              <div className="field">
                <label>Status</label>
                <select className="select" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                  {STATUSES.map((s) => <option key={s}>{s}</option>)}
                </select>
              </div>
              <div className="field">
                <label>Priority</label>
                <select className="select" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
                  {PRIORITIES.map((p) => <option key={p}>{p}</option>)}
                </select>
              </div>
              <div className="field">
                <label>Start Date</label>
                <input className="input" type="date" value={form.start_date || ''} onChange={(e) => setForm({ ...form, start_date: e.target.value })} />
              </div>
              <div className="field">
                <label>Expected End Date</label>
                <input className="input" type="date" value={form.expected_end_date || ''} onChange={(e) => setForm({ ...form, expected_end_date: e.target.value })} />
              </div>
            </div>

            <div className="field">
              <label>Assigned To</label>
              {users.length === 0 ? (
                <div className="tiny dim">No users exist yet — you can assign people later from a project.</div>
              ) : (
                <div className="chip-row">
                  {users.map((u) => (
                    <button key={u.id} type="button" className={`chip ${fe(u.id, form) ? 'active' : ''}`} onClick={() => toggleMember(u.id)}>
                      {fe(u.id, form) ? '✓ ' : ''}{u.name}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </Modal>
      )}
    </AppShell>
  );
}