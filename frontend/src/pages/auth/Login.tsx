import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Bot, Brain, FileUp, ShieldCheck, Activity, LogIn, AlertCircle } from 'lucide-react';
import { useAuth } from '../../auth/AuthContext';
import { useToast } from '../../ui/ToastContext';

export default function Login() {
  const { login, user, needsSetup, loading } = useAuth();
  const nav = useNavigate();
  const toast = useToast();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (user && !loading) nav('/', { replace: true });
  }, [user, loading, nav]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      await login(email, password);
      toast.success('Welcome back.');
      nav('/', { replace: true });
    } catch (err: any) {
      setError(err?.message || 'Login failed.');
    } finally {
      setBusy(false);
    }
  };

  const feats = [
    { ico: <FileUp size={17} />, t: 'Document Intelligence', d: 'Upload PDF, DOCX, CSV and TXT project files and build a scoped knowledge base.' },
    { ico: <Brain size={17} />, t: 'Multi-Agent RAG Analysis', d: 'Scope, risk, forecast, blocker and action-item agents grounded in your documents.' },
    { ico: <Activity size={17} />, t: 'Risk & Delivery Advisor', d: 'Evidence-backed risks and projection of delivery challenges, not hallucinations.' },
  ];

  return (
    <div className="auth-wrap">
      <div className="auth-visual">
        <div className="brand-row" style={{ display: 'flex', alignItems: 'center', gap: 13 }}>
          <div className="brand-logo"><Bot size={19} /></div>
          <div>
            <div className="brand-name" style={{ fontSize: 15.5 }}>AI Project Intelligence</div>
            <div className="brand-sub" style={{ color: 'var(--violet-2)' }}>Risk Advisor</div>
          </div>
        </div>
        <div>
          <h1>Turn project documents into working intelligence.</h1>
          <p className="lede" style={{ marginTop: 18 }}>
            Every answer is grounded in real uploaded project documents. AI extracts scope,
            risks, blockers and action items — with proof of source on every claim.
          </p>
          <div className="auth-feats" style={{ marginTop: 44 }}>
            {feats.map((f) => (
              <div className="auth-feat" key={f.t}>
                <div className="f-ico">{f.ico}</div>
                <div>
                  <div className="f-t">{f.t}</div>
                  <div className="f-d">{f.d}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <ShieldCheck size={15} style={{ color: 'var(--emerald)' }} />
          <span className="tiny dim">Ollama · Qwen2.5 3B · Project-level RAG isolation · Role-based access</span>
        </div>
      </div>

      <div className="auth-panel">
        <div className="auth-card">
          <div className="brand-row">
            <div className="brand-logo"><Bot size={19} /></div>
            <div>
              <div className="brand-name" style={{ fontSize: 15 }}>Sign in</div>
              <div className="brand-sub">Project Intelligence Workspace</div>
            </div>
          </div>
          <h2>Welcome back</h2>
          <p className="auth-sub">Sign in to access your projects, documents and AI analysis.</p>

          {needsSetup && !loading && (
            <div className="form-success" style={{ marginBottom: 18 }}>
              No users exist yet. Create the first administrator account to get started.
            </div>
          )}

          <form className="auth-form" onSubmit={submit}>
            <div className="field">
              <label>Email</label>
              <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" required autoFocus />
            </div>
            <div className="field">
              <label>Password</label>
              <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" required />
            </div>

            {error && (
              <div className="form-error"><AlertCircle size={15} /> {error}</div>
            )}

            <button className="btn btn-primary btn-lg w-full" type="submit" disabled={busy} style={{ marginTop: 6 }}>
              {busy ? <span className="spinner" /> : <LogIn size={16} />} Sign in
            </button>
          </form>

          {needsSetup && !loading && (
            <div className="auth-alt">
              New installation? <Link to="/auth/setup"><b>Create the first administrator →</b></Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}