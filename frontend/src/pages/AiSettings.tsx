import { useCallback, useEffect, useState } from 'react';
import { Server, PlugZap, Layers, RefreshCw, Save, KeyRound, ShieldCheck } from 'lucide-react';
import { api } from '../api/client';
import type { AiProviderInfo, AiProviderStatus, ProviderRole, ProviderState } from '../api/client';
import { AppShell } from '../layout/AppShell';
import { PageLoader } from '../components/EmptyState';
import { Badge } from '../components/Badge';
import { useToast } from '../ui/ToastContext';

const ROLE_LABELS: Record<ProviderRole, string> = {
  primary: 'Primary',
  fallback_1: 'Fallback 1',
  fallback_2: 'Fallback 2',
  fallback_3: 'Fallback 3',
  fallback_4: 'Fallback 4',
  extra: 'Extra',
};

const ROLE_TONES: Record<ProviderRole, 'magenta' | 'violet' | 'cyan' | 'neutral'> = {
  primary: 'magenta',
  fallback_1: 'violet',
  fallback_2: 'violet',
  fallback_3: 'violet',
  fallback_4: 'violet',
  extra: 'neutral',
};

const STATE_LABELS: Record<ProviderState, string> = {
  not_configured: 'Not Configured',
  discovering: 'Discovering model',
  online: 'Online',
  offline: 'Offline',
  unavailable: 'Unavailable',
};

const STATE_TONES: Record<ProviderState, 'ok' | 'err' | 'warn' | 'neutral'> = {
  not_configured: 'neutral',
  discovering: 'warn',
  online: 'ok',
  offline: 'err',
  unavailable: 'warn',
};

export default function AiSettings() {
  const [status, setStatus] = useState<AiProviderStatus | null>(null);
  const [general, setGeneral] = useState<any>(null);
  const [editable, setEditable] = useState<any>(null);
  const [results, setResults] = useState<Record<string, { healthy: boolean; detail: string }>>({});
  const [testing, setTesting] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const [s, g] = await Promise.all([api.aiProviderStatus(), api.adminGeneral()]);
      setStatus(s);
      setGeneral(g);
      setEditable({
        chunk_size: g.chunk_size,
        chunk_overlap: g.chunk_overlap,
        ai_max_chunks: g.ai_max_chunks,
        ai_temperature: g.ai_temperature,
      });
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load AI settings.');
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const test = async (key: string) => {
    setTesting(key);
    try {
      const r = await api.testProvider(key);
      setResults((prev) => ({ ...prev, [key]: { healthy: r.healthy, detail: r.detail } }));
      if (r.healthy) toast.success(`${r.name}: ${r.detail}`);
      else toast.error(`${r.name}: ${r.detail}`);
    } catch (err: any) {
      toast.error(err?.message || 'Connection test failed.');
    } finally {
      setTesting(null);
      load();
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      await api.updateGeneral({
        chunk_size: Number(editable.chunk_size),
        chunk_overlap: Number(editable.chunk_overlap),
        ai_max_chunks: Number(editable.ai_max_chunks),
        ai_temperature: Number(editable.ai_temperature),
      });
      toast.success('Settings saved.');
      load();
    } catch (err: any) {
      toast.error(err?.message || 'Save failed.');
    } finally {
      setSaving(false);
    }
  };

  if (!status || !general) return <AppShell title="AI Settings" crumb="Settings"><PageLoader label="Loading AI settings…" /></AppShell>;

  const renderProvider = (p: AiProviderInfo) => {
    const tested = results[p.key];
    const online = tested ? tested.healthy : p.healthy;
    const detail = tested ? tested.detail : p.detail;
    const roleLabel = ROLE_LABELS[p.role] ?? p.role;
    const state: ProviderState = p.state;
    const modelLine = p.model_source === 'discovered'
      ? `${p.model} (auto-discovered)`
      : p.model;
    return (
      <div key={p.key} className="card card-flat row gap-sm wrap" style={{ padding: 14 }}>
        <div className="row gap-sm" style={{ flex: 1, minWidth: 240 }}>
          <span className={`dot ${online ? 'dot-ok' : 'dot-bad'}`} />
          <div className="col gap-sm" style={{ gap: 2 }}>
            <div className="row gap-sm wrap">
              <b className="small">{p.name}</b>
              <Badge tone={ROLE_TONES[p.role] ?? 'neutral'} plain>{roleLabel}</Badge>
              {p.configured && <Badge tone="ok" plain>Configured</Badge>}
              {!p.in_chain && <Badge tone="neutral" plain>Skipped</Badge>}
              {p.breaker_open && <Badge tone="warn" plain>Circuit open</Badge>}
            </div>
            <span className="tiny dim">{modelLine}</span>
            {p.key_configured && (
              <span className="tiny dim row gap-sm">
                <KeyRound size={11} /> API key <code>{p.key_masked}</code> (stored server-side, never sent to the browser)
              </span>
            )}
          </div>
        </div>
        <div className="small" style={{ maxWidth: 380 }} title={detail}>{detail}</div>
        <div className="row gap-sm">
          <Badge tone={STATE_TONES[state]}>{STATE_LABELS[state] ?? state}</Badge>
          <button className="btn btn-secondary btn-sm" onClick={() => test(p.key)} disabled={testing === p.key}>
            {testing === p.key ? <><RefreshCw size={13} className="spin" /> Testing…</> : <><PlugZap size={13} /> Test</>}
          </button>
        </div>
      </div>
    );
  };

  const chain = status.providers.filter((p) => p.role !== 'extra');
  const extras = status.providers.filter((p) => p.role === 'extra');

  return (
    <AppShell title="AI Settings" crumb="Settings · AI Providers">
      <div className="col">
        <div className="panel">
          <div className="panel-head">
            <h3><Server size={15} /> Provider Chain</h3>
            <span className="ph-sub">Ollama primary · sequential failover · keys never sent to the browser · models auto-discovered when left blank</span>
          </div>
          <div className="panel-body">
            <div className="col">
              {chain.map(renderProvider)}
            </div>
            {extras.length > 0 && (
              <details style={{ marginTop: 12 }}>
                <summary className="tiny dim" style={{ cursor: 'pointer' }}>
                  Additional OpenAI-compatible endpoints ({extras.length})
                </summary>
                <div className="col" style={{ marginTop: 10 }}>
                  {extras.map(renderProvider)}
                </div>
              </details>
            )}
            <div className="row gap-sm tiny dim" style={{ marginTop: 12 }}>
              <ShieldCheck size={13} />
              <span>Providers are tried one at a time in the order above. The first one that answers is reported back to the UI. Only an API key is required per provider — a blank model is discovered from the provider's own catalogue and cached.</span>
            </div>
          </div>
        </div>

        <div className="panel">
          <div className="panel-head"><h3><Layers size={15} /> Embedding Provider</h3></div>
          <div className="panel-body row gap-sm wrap">
            <div className="row gap-sm">
              <span className={`dot ${status.embedding.healthy ? 'dot-ok' : 'dot-bad'}`} />
              <div className="col gap-sm" style={{ gap: 2 }}>
                <b className="small">{status.embedding.provider} · {status.embedding.model}</b>
                <span className="tiny dim">{status.embedding.dimension ? `${status.embedding.dimension}-dimension vectors` : status.embedding.detail}</span>
              </div>
            </div>
            <div style={{ marginLeft: 'auto' }}>
              <Badge tone={status.embedding.healthy ? 'ok' : 'err'}>{status.embedding.healthy ? 'Healthy' : 'Unhealthy'}</Badge>
            </div>
          </div>
        </div>

        <div className="panel">
          <div className="panel-head"><h3>Pipeline Tuning</h3><span className="ph-sub">These values apply to new chunking / analysis runs</span></div>
          <div className="panel-body">
            <div className="grid grid-4">
              <div className="field">
                <label>Chunk size (tokens)</label>
                <input className="input" type="number" value={editable?.chunk_size} min={200} max={6000}
                  onChange={(e) => setEditable({ ...editable, chunk_size: e.target.value })} />
              </div>
              <div className="field">
                <label>Chunk overlap</label>
                <input className="input" type="number" value={editable?.chunk_overlap} min={0} max={1500}
                  onChange={(e) => setEditable({ ...editable, chunk_overlap: e.target.value })} />
              </div>
              <div className="field">
                <label>Max chunks per doc</label>
                <input className="input" type="number" value={editable?.ai_max_chunks} min={1} max={200}
                  onChange={(e) => setEditable({ ...editable, ai_max_chunks: e.target.value })} />
              </div>
              <div className="field">
                <label>AI temperature</label>
                <input className="input" type="number" step="0.1" min="0" max="1.5" value={editable?.ai_temperature}
                  onChange={(e) => setEditable({ ...editable, ai_temperature: e.target.value })} />
              </div>
            </div>
            <div className="row-between">
              <div className="tiny dim">Defaults from env: chunk {general.chunk_size} · overlap {general.chunk_overlap} · max {general.ai_max_chunks} · temp {general.ai_temperature} · max upload {general.max_upload_mb} MB</div>
              <button className="btn btn-primary" onClick={save} disabled={saving}>
                {saving ? <><RefreshCw size={15} className="spin" /> Saving…</> : <><Save size={15} /> Save Changes</>}
              </button>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
