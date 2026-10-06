import { useCallback, useEffect, useMemo, useState } from 'react';
import { ListChecks, Plus, Circle } from 'lucide-react';
import { api } from '../api/client';
import { EmptyState } from '../components/EmptyState';
import { Badge, SeverityBadge, statusTone } from '../components/Badge';
import { Modal } from '../components/Modal';
import { useToast } from '../ui/ToastContext';
import { fmtDateOnly } from '../utils/format';
import { useAuth } from '../auth/AuthContext';

const STATUSES = ['Pending', 'In Progress', 'Completed', 'Blocked'];

export function TasksPanel({
  projectId,
  users = [],
  myOnly = false,
  onChanged,
}: {
  projectId: number;
  users?: { id: number; name: string }[];
  myOnly?: boolean;
  onChanged?: () => void;
}) {
  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [form, setForm] = useState<any>({});
  const { user } = useAuth();
  const toast = useToast();

  const canManage = "ADMIN" === 'ADMIN';
  const canEditTask = (task: any) => canManage || task.assigned_to === user?.id;

  const load = useCallback(async () => {
    try {
      const params: Record<string, any> = { project_id: projectId };
      if (myOnly && user) params.assigned_to = user.id;
      const data = await api.listTasks(params);
      setTasks(data);
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load tasks.');
    } finally {
      setLoading(false);
    }
  }, [projectId, myOnly, user, toast]);

  useEffect(() => { load(); }, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm({
      title: '',
      description: '',
      assigned_to: users[0]?.id ?? null,
      due_date: '',
      priority: 'Medium',
      status: 'Pending',
    });
    setOpen(true);
  };

  const openEdit = (t: any) => {
    setEditing(t);
    setForm({
      title: t.title,
      description: t.description || '',
      assigned_to: t.assigned_to ?? null,
      due_date: t.due_date ? t.due_date.slice(0, 10) : '',
      priority: t.priority,
      status: t.status,
    });
    setOpen(true);
  };

  const save = async () => {
    if (!form.title?.trim()) return toast.error('Task title is required.');
    const payload = {
      title: form.title.trim(),
      description: form.description || '',
      assigned_to: form.assigned_to || undefined,
      due_date: form.due_date || undefined,
      priority: form.priority,
      status: form.status,
    };
    try {
      if (editing && canManage) {
        await api.updateTask(editing.id, {
          title: payload.title,
          description: payload.description,
          priority: payload.priority,
          status: payload.status,
          due_date: form.due_date || null,
          assigned_to: payload.assigned_to,
        });
        toast.success('Task updated.');
      } else if (editing) {
        await api.updateTask(editing.id, { status: form.status });
        toast.success('Task status updated.');
      } else {
        await api.createTask(projectId, payload);
        toast.success('Task created.');
      }
      setOpen(false);
      load();
      onChanged?.();
    } catch (err: any) {
      toast.error(err?.message || 'Save failed.');
    }
  };

  const quickStatus = async (t: any, status: string) => {
    try {
      await api.updateTask(t.id, { status });
      toast.success(`Task marked ${status}.`);
      load();
      onChanged?.();
    } catch (err: any) {
      toast.error(err?.message || 'Update failed.');
    }
  };

  const counts = useMemo(() => {
    return {
      pending: tasks.filter((t) => t.status === 'Pending').length,
      progress: tasks.filter((t) => t.status === 'In Progress').length,
      done: tasks.filter((t) => t.status === 'Completed').length,
      blocked: tasks.filter((t) => t.status === 'Blocked').length,
    };
  }, [tasks]);

  const sum = Math.max(tasks.length, 1);

  return (
    <div className="col">
      <div className="row-between wrap">
        <div className="grid grid-4 w-full">
          <Minicard label="Pending" value={counts.pending} color="var(--amber)" pct={(counts.pending / sum) * 100} />
          <Minicard label="In Progress" value={counts.progress} color="var(--violet-2)" pct={(counts.progress / sum) * 100} />
          <Minicard label="Completed" value={counts.done} color="var(--emerald)" pct={(counts.done / sum) * 100} />
          <Minicard label="Blocked" value={counts.blocked} color="var(--red)" pct={(counts.blocked / sum) * 100} />
        </div>
        {canManage && (
          <button className="btn btn-primary btn-sm" onClick={openCreate} style={{ alignSelf: 'flex-end' }}>
            <Plus size={15} /> New Task
          </button>
        )}
      </div>

      {loading ? (
        <div className="row gap-sm" style={{ padding: 24 }}><span className="spinner" /><span className="muted small">Loading tasks…</span></div>
      ) : tasks.length === 0 ? (
        <EmptyState
          icon={<ListChecks size={26} />}
          title={myOnly ? 'No tasks assigned to you' : 'No tasks yet'}
          description={myOnly ? 'Assigned action items will appear here so you can update their status.' : 'Tasks and action items (manual or AI-generated) will appear here.'}
          action={canManage && !myOnly ? <button className="btn btn-secondary btn-sm" onClick={openCreate}><Plus size={15} /> Create Task</button> : undefined}
        />
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Task</th>
                <th>Assignee</th>
                <th>Due</th>
                <th>Priority</th>
                <th>Source</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr key={t.id}>
                  <td>
                    <div className="cell-strong" style={{ maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.title}</div>
                    <div className="tiny dim" style={{ maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.description || ''}</div>
                  </td>
                  <td className="muted small">{t.assigned_name || 'Unassigned'}</td>
                  <td className="muted small" style={{ whiteSpace: 'nowrap' }}>{fmtDateOnly(t.due_date)}</td>
                  <td><SeverityBadge value={t.priority} /></td>
                  <td className="tiny">
                    {t.ai_generated ? <Badge tone="violet" plain>AI</Badge> : <Badge tone="neutral" plain>Manual</Badge>}
                    {t.source_ref && <div className="dim" style={{ marginTop: 3 }}>{t.source_ref}</div>}
                  </td>
                  <td>
                    <Badge tone={statusTone(t.status)}>{t.status}</Badge>
                    <div className="tiny dim" style={{ marginTop: 4 }}>
                      <Circle size={8} style={{ verticalAlign: 'middle', marginRight: 4, color: 'var(--text-3)' }} />
                      Use actions to update
                    </div>
                  </td>
                  <td>
                    <div className="cell-actions">
                      {canEditTask(t) && (
                        <>
                          {['Pending', 'In Progress'].includes(t.status) && (
                            <button className="btn btn-secondary btn-sm" onClick={() => quickStatus(t, t.status === 'Pending' ? 'In Progress' : 'Completed')}>
                              {t.status === 'Pending' ? 'Start' : 'Complete'}
                            </button>
                          )}
                          {t.status === 'Blocked' && (
                            <button className="btn btn-secondary btn-sm" onClick={() => quickStatus(t, 'In Progress')}>Unblock</button>
                          )}
                          {t.status === 'Completed' && (
                            <button className="btn btn-secondary btn-sm" onClick={() => quickStatus(t, 'Pending')}>Reopen</button>
                          )}
                          <button className="btn btn-ghost btn-sm" onClick={() => openEdit(t)}>{canManage ? 'Edit' : 'Status'}</button>
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

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={editing ? (canManage ? 'Edit Task' : 'Update Task Status') : 'Create Task'}
        footer={
          <>
            <button className="btn btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn btn-primary" onClick={save}>{canManage ? 'Save Task' : 'Update Status'}</button>
          </>
        }
      >
        <div className="col">
          {canManage && (
            <>
              <div className="field">
                <label>Title</label>
                <input className="input" value={form.title || ''} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Task / action item title" />
              </div>
              <div className="field">
                <label>Description</label>
                <textarea className="textarea" value={form.description || ''} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Optional description" />
              </div>
            </>
          )}
          <div className="grid grid-2">
            {canManage && (
              <div className="field">
                <label>Assignee</label>
                <select className="select" value={form.assigned_to ?? ''} onChange={(e) => setForm({ ...form, assigned_to: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">Unassigned</option>
                  {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                </select>
              </div>
            )}
            {canManage && (
              <div className="field">
                <label>Due date</label>
                <input className="input" type="date" value={form.due_date || ''} onChange={(e) => setForm({ ...form, due_date: e.target.value })} />
              </div>
            )}
            {canManage && (
              <div className="field">
                <label>Priority</label>
                <select className="select" value={form.priority || 'Medium'} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
                  {['Low', 'Medium', 'High', 'Critical'].map((p) => <option key={p}>{p}</option>)}
                </select>
              </div>
            )}
            <div className="field">
              <label>Status</label>
              <select className="select" value={form.status || 'Pending'} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                {STATUSES.map((s) => <option key={s}>{s}</option>)}
              </select>
            </div>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function Minicard({ label, value, color, pct }: { label: string; value: number; color: string; pct: number }) {
  return (
    <div className="card card-flat col gap-sm" style={{ padding: 14 }}>
      <div className="row-between">
        <span className="tiny dim">{label}</span>
        <b style={{ color }}>{value}</b>
      </div>
      <div className="meter"><div className="meter-fill" style={{ width: `${pct}%`, background: color, opacity: 0.75 }} /></div>
    </div>
  );
}