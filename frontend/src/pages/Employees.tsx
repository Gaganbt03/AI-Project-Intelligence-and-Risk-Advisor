import { useCallback, useEffect, useState } from 'react';
import { Users as UsersIcon, Plus, Pencil, KeyRound, UserCheck, UserX, FolderKanban } from 'lucide-react';
import { api } from '../api/client';
import { AppShell } from '../layout/AppShell';
import { EmptyState, PageLoader } from '../components/EmptyState';
import { Badge, statusTone } from '../components/Badge';
import { Modal } from '../components/Modal';
import { useToast } from '../ui/ToastContext';
import { initials, fmtDateOnly, timeAgo } from '../utils/format';

export default function Employees() {
  const [users, setUsers] = useState<any[]>([]);
  const [projects, setProjects] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [form, setForm] = useState<any>({});
  const [resetTarget, setResetTarget] = useState<any>(null);
  const [newPass, setNewPass] = useState('');
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const [u, p] = await Promise.all([api.listUsers(), api.listProjects()]);
      setUsers(u);
      setProjects(p);
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load employees.');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm({ name: '', email: '', password: '', role: 'EMPLOYEE', project_ids: [] });
    setOpen(true);
  };

  const openEdit = (u: any) => {
    setEditing(u);
    setForm({ name: u.name, email: u.email, password: '', role: u.role, project_ids: u.project_ids || [] });
    setOpen(true);
  };

  const toggleProject = (id: number) => {
    setForm((f: any) => {
      const cur = f.project_ids || [];
      return { ...f, project_ids: cur.includes(id) ? cur.filter((x: number) => x !== id) : [...cur, id] };
    });
  };

  const save = async () => {
    if (!form.name?.trim() || !form.email?.trim()) return toast.error('Name and email are required.');
    try {
      if (editing) {
        await api.updateUser(editing.id, {
          name: form.name.trim(),
          is_active: editing.is_active,
          ...(form.password ? { password: form.password } : {}),
        });
        await api.setUserProjects(editing.id, (form.project_ids || []).map(Number));
        toast.success('Employee updated.');
      } else {
        if (!form.password || form.password.length < 8) return toast.error('Password must be at least 8 characters.');
        await api.createUser({
          name: form.name.trim(),
          email: form.email.trim(),
          password: form.password,
          role: form.role,
          project_ids: (form.project_ids || []).map(Number),
        });
        toast.success('Employee created.');
      }
      setOpen(false);
      load();
    } catch (err: any) {
      toast.error(err?.message || 'Save failed.');
    }
  };

  const toggleActive = async (u: any) => {
    try {
      await api.updateUser(u.id, { is_active: !u.is_active });
      toast.success(u.is_active ? 'Account deactivated.' : 'Account activated.');
      load();
    } catch (err: any) {
      toast.error(err?.message || 'Update failed.');
    }
  };

  const doReset = async () => {
    if (!newPass || newPass.length < 8) return toast.error('New password must be at least 8 characters.');
    try {
      await api.resetPassword(resetTarget.id, newPass);
      toast.success('Password reset.');
      setResetTarget(null);
      setNewPass('');
    } catch (err: any) {
      toast.error(err?.message || 'Reset failed.');
    }
  };

  const projName = (id: number) => projects.find((p) => p.id === id)?.name || `#${id}`;

  if (loading) return <AppShell title="Employees" crumb="Management"><PageLoader label="Loading employees…" /></AppShell>;

  return (
    <AppShell title="Employees" crumb="Management · Team" actions={
      <button className="btn btn-primary btn-sm" onClick={openCreate}><Plus size={15} /> Add Employee</button>
    }>
      {users.length === 0 ? (
        <EmptyState
          icon={<UsersIcon size={26} />}
          title="No employees yet"
          description="Add team members and assign them to projects. Employees can upload documents, update their tasks, report blockers and ask the project assistant."
          action={<button className="btn btn-primary" onClick={openCreate}><Plus size={16} /> Add Employee</button>}
        />
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Name</th><th>Role</th><th>Assigned Projects</th><th>Status</th><th>Created</th><th>Last Login</th><th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div className="row gap-sm">
                      <div className="avatar">{initials(u.name)}</div>
                      <div className="col gap-sm" style={{ gap: 2 }}>
                        <span className="cell-strong">{u.name}</span>
                        <span className="tiny dim">{u.email}</span>
                      </div>
                    </div>
                  </td>
                  <td><Badge tone={u.role === 'ADMIN' ? 'magenta' : 'violet'} plain>{u.role}</Badge></td>
                  <td>
                    <div className="row wrap gap-sm" style={{ gap: 6 }}>
                      {(u.project_ids || []).length === 0 ? (
                        <span className="tiny dim">None</span>
                      ) : (
                        (u.project_ids || []).slice(0, 3).map((pid: number) => (
                          <Badge key={pid} tone="neutral" plain>{projName(pid)}</Badge>
                        ))
                      )}
                      {(u.project_ids || []).length > 3 && <Badge tone="neutral" plain>+{(u.project_ids || []).length - 3}</Badge>}
                    </div>
                  </td>
                  <td><Badge tone={statusTone(u.is_active ? 'active' : 'deactivated')}>{u.is_active ? 'Active' : 'Deactivated'}</Badge></td>
                  <td className="muted small" style={{ whiteSpace: 'nowrap' }}>{timeAgo(u.created_at)}</td>
                  <td className="muted small" style={{ whiteSpace: 'nowrap' }}>{u.last_login ? fmtDateOnly(u.last_login) : 'Never'}</td>
                  <td>
                    <div className="cell-actions">
                      <button className="btn btn-icon" title="Edit" onClick={() => openEdit(u)}><Pencil size={14} /></button>
                      <button className="btn btn-icon" title="Reset password" onClick={() => { setResetTarget(u); setNewPass(''); }}><KeyRound size={14} /></button>
                      {u.role !== 'ADMIN' && (
                        <button className="btn btn-icon" title={u.is_active ? 'Deactivate' : 'Activate'} style={{ color: u.is_active ? 'var(--warn)' : 'var(--emerald)' }} onClick={() => toggleActive(u)}>
                          {u.is_active ? <UserX size={14} /> : <UserCheck size={14} />}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title={editing ? `Edit · ${editing.name}` : 'Add Employee'} wide footer={
        <>
          <button className="btn btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
          <button className="btn btn-primary" onClick={save}>{editing ? 'Save Changes' : 'Create Employee'}</button>
        </>
      }>
        <div className="col">
          <div className="grid grid-2">
            <div className="field">
              <label>Full name</label>
              <input className="input" value={form.name || ''} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Priya Sharma" />
            </div>
            <div className="field">
              <label>Email</label>
              <input className="input" type="email" value={form.email || ''} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="employee@company.com" disabled={!!editing} />
            </div>
            <div className="field">
              <label>{editing ? 'New password (optional)' : 'Password'}</label>
              <input className="input" type="password" value={form.password || ''} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder={editing ? 'Leave blank to keep current' : 'At least 8 characters'} />
            </div>
            <div className="field">
              <label>Role</label>
              <select className="select" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} disabled={editing?.role === 'ADMIN'}>
                <option value="EMPLOYEE">Employee</option>
                <option value="ADMIN">Administrator</option>
              </select>
            </div>
          </div>

          <div className="field">
            <label>Assigned Projects</label>
            {projects.length === 0 ? (
              <div className="tiny dim">No projects exist yet. You can assign projects later.</div>
            ) : (
              <div className="chip-row">
                {projects.filter((p) => p.status !== 'Archived').map((p) => {
                  const on = (form.project_ids || []).includes(p.id);
                  return (
                    <button key={p.id} type="button" className={`chip ${on ? 'active' : ''}`} onClick={() => toggleProject(p.id)}>
                      {on ? '✓ ' : ''}<FolderKanban size={11} style={{ verticalAlign: 'middle', marginRight: 4 }} />{p.name}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </Modal>

      <Modal open={!!resetTarget} onClose={() => setResetTarget(null)} title={`Reset password · ${resetTarget?.name}`} footer={
        <>
          <button className="btn btn-ghost" onClick={() => setResetTarget(null)}>Cancel</button>
          <button className="btn btn-primary" onClick={doReset}>Reset Password</button>
        </>
      }>
        <div className="col">
          <div className="field">
            <label>New password</label>
            <input className="input" type="password" value={newPass} onChange={(e) => setNewPass(e.target.value)} placeholder="At least 8 characters" />
          </div>
          <div className="tiny dim">The new password is passed securely via an authenticated admin endpoint. Never share passwords through chat or email.</div>
        </div>
      </Modal>
    </AppShell>
  );
}