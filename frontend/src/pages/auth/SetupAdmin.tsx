import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Bot, Sparkles, AlertCircle, UserPlus, ShieldCheck } from 'lucide-react';
import { api, setToken } from '../../api/client';
import { useToast } from '../../ui/ToastContext';
import { useAuth } from '../../auth/AuthContext';
import { Spinner } from '../../components/EmptyState';

export default function SetupAdmin() {
  const nav = useNavigate();
  const toast = useToast();
  const { refresh } = useAuth();
  const [checking, setChecking] = useState(true);
  const [blocked, setBlocked] = useState(false);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .authStatus()
      .then((s) => setBlocked(!s.needs_setup))
      .catch(() => setBlocked(true))
      .finally(() => setChecking(false));
  }, []);

  if (checking) return <Spinner label="Checking setup state…" />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    if (password.length < 8) return setError('Password must be at least 8 characters.');
    if (password !== confirm) return setError('Passwords do not match.');
    setBusy(true);
    try {
      const res = await api.setupAdmin({ name, email, password });
      setToken(res.access_token);
      toast.success('Administrator account created.');
      await refresh();
      nav('/', { replace: true });
    } catch (err: any) {
      setError(err?.message || 'Setup failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-wrap">
      <div className="auth-visual" style={{ justifyContent: 'center' }}>
        <div>
          <div className="brand-row" style={{ display: 'flex', alignItems: 'center', gap: 13 }}>
            <div className="brand-logo"><Bot size={19} /></div>
            <div>
              <div className="brand-name" style={{ fontSize: 15.5 }}>AI Project Intelligence</div>
              <div className="brand-sub" style={{ color: 'var(--violet-2)' }}>Risk Advisor</div>
            </div>
          </div>
          <h1 style={{ marginTop: 34 }}>Prepare the ground for your project intelligence workspace.</h1>
          <p className="lede" style={{ marginTop: 18 }}>
            Create the first administrator account. From here you can create projects, add team
            members, upload real documents and run the multi-agent AI analysis pipeline.
          </p>
          <div className="auth-feats" style={{ marginTop: 40 }}>
            <div className="auth-feat">
              <div className="f-ico"><ShieldCheck size={17} /></div>
              <div>
                <div className="f-t">Secure by default</div>
                <div className="f-d">This screen is only available while zero users exist. Passwords are hashed and API keys stay out of the frontend.</div>
              </div>
            </div>
            <div className="auth-feat">
              <div className="f-ico"><Sparkles size={17} /></div>
              <div>
                <div className="f-t">Starts empty</div>
                <div className="f-d">No demo data is ever inserted. Every number you see comes from real actions and real documents.</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="auth-panel">
        <div className="auth-card">
          <div className="brand-row">
            <div className="brand-logo"><UserPlus size={19} /></div>
            <div>
              <div className="brand-name" style={{ fontSize: 15 }}>First-time setup</div>
              <div className="brand-sub">Create Administrator</div>
            </div>
          </div>
          <h2>Create your administrator</h2>
          <p className="auth-sub">This account will have full control over projects, employees, documents and AI configuration.</p>

          {blocked && (
            <div className="form-error"><AlertCircle size={15} /> Setup has already been completed. Please sign in instead.</div>
          )}

          {!blocked && (
            <form className="auth-form" onSubmit={submit}>
              <div className="field">
                <label>Full name</label>
                <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Jane Administrator" required />
              </div>
              <div className="field">
                <label>Email</label>
                <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="admin@company.com" required />
              </div>
              <div className="field">
                <label>Password</label>
                <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" required />
              </div>
              <div className="field">
                <label>Confirm password</label>
                <input className="input" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="Repeat password" required />
              </div>

              {error && <div className="form-error"><AlertCircle size={15} /> {error}</div>}

              <button className="btn btn-primary btn-lg w-full" type="submit" disabled={busy}>
                {busy ? <span className="spinner" /> : <UserPlus size={16} />} Create administrator
              </button>
            </form>
          )}

          <div className="auth-alt">
            Already set up? <Link to="/auth/login"><b>Back to sign in →</b></Link>
          </div>
        </div>
      </div>
    </div>
  );
}