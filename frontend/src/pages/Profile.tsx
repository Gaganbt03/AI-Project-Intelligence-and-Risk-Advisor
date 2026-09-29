import { useState } from 'react';
import { KeyRound, User as UserIcon, ShieldCheck } from 'lucide-react';
import { api } from '../api/client';
import { AppShell } from '../layout/AppShell';
import { Badge, statusTone } from '../components/Badge';
import { useToast } from '../ui/ToastContext';
import { useAuth } from '../auth/AuthContext';

export default function Profile() {
  const { user } = useAuth();
  const toast = useToast();
  const [form, setForm] = useState({ old: '', next: '', confirm: '' });

  const change = async () => {
    if (form.next.length < 8) return toast.error('New password must be at least 8 characters.');
    if (form.next !== form.confirm) return toast.error('Passwords do not match.');
    try {
      await api.changePassword(form.old, form.next);
      toast.success('Password updated.');
      setForm({ old: '', next: '', confirm: '' });
    } catch (err: any) {
      toast.error(err?.message || 'Change failed.');
    }
  };

  const initials = (user?.name || '').split(/\s+/).slice(0, 2).map((p) => p[0]?.toUpperCase() || '').join('') || '?';

  return (
    <AppShell title="My Profile" crumb="Account">
      <div className="grid grid-2">
        <div className="panel">
          <div className="panel-head"><h3><UserIcon size={15} /> Account</h3></div>
          <div className="panel-body row gap-sm" style={{ gap: 18 }}>
            <div className="avatar avatar-lg">{initials}</div>
            <div className="col gap-sm" style={{ gap: 4 }}>
              <h3 style={{ fontSize: 18 }}>{user?.name}</h3>
              <span className="muted small">{user?.email}</span>
              <div className="row gap-sm">
                <Badge tone={user?.role === 'ADMIN' ? 'magenta' : 'violet'}>{user?.role}</Badge>
                <Badge tone={statusTone(user?.is_active ? 'active' : 'deactivated')}>{user?.is_active ? 'Active' : 'Deactivated'}</Badge>
              </div>
            </div>
          </div>
        </div>
        <div className="panel">
          <div className="panel-head"><h3><ShieldCheck size={15} /> Role capabilities</h3></div>
          <div className="panel-body">
            {user?.role === 'ADMIN' ? (
              <ul className="list-small">
                <li>Create projects, assign teams and archive projects</li>
                <li>Manage employees and reset passwords</li>
                <li>Upload, reprocess and delete any document</li>
                <li>Run AI analysis agents and inspect results</li>
                <li>Tune the AI pipeline (chunking, temperature)</li>
                <li>Review the full audit trail</li>
              </ul>
            ) : (
              <ul className="list-small">
                <li>Upload documents to assigned projects</li>
                <li>Update your own task status</li>
                <li>Report and track blockers</li>
                <li>Ask the project assistant (answers cite your project's documents)</li>
              </ul>
            )}
            <div className="tiny dim" style={{ marginTop: 10 }}>Admin and employee capabilities are enforced server-side for every endpoint.</div>
          </div>
        </div>
        <div className="panel">
          <div className="panel-head"><h3><KeyRound size={15} /> Change password</h3></div>
          <div className="panel-body col">
            <div className="field">
              <label>Current password</label>
              <input className="input" type="password" value={form.old} onChange={(e) => setForm({ ...form, old: e.target.value })} />
            </div>
            <div className="field">
              <label>New password</label>
              <input className="input" type="password" value={form.next} onChange={(e) => setForm({ ...form, next: e.target.value })} placeholder="At least 8 characters" />
            </div>
            <div className="field">
              <label>Confirm new password</label>
              <input className="input" type="password" value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value })} />
            </div>
            <button className="btn btn-primary" onClick={change} disabled={!form.old || !form.next}>Update Password</button>
          </div>
        </div>
      </div>
    </AppShell>
  );
}