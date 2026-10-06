import { useCallback, useEffect, useState } from 'react';
import {
  Activity, RefreshCw, TrendingUp, TrendingDown, Lightbulb, Info, Calculator, History, Loader2,
} from 'lucide-react';
import { api, type HealthReport, type HealthFormula } from '../api/client';
import { Badge } from '../components/Badge';
import { EmptyState } from '../components/EmptyState';
import { useToast } from '../ui/ToastContext';
import { fmtDate } from '../utils/format';

const STATUS_TONE: Record<string, 'ok' | 'warn' | 'err' | 'violet' | 'magenta' | 'neutral'> = {
  Excellent: 'ok',
  Healthy: 'ok',
  Watch: 'warn',
  'At Risk': 'err',
  Critical: 'err',
};

export function HealthPanel({ projectId }: { projectId: number }) {
  const [report, setReport] = useState<HealthReport | null>(null);
  const [formula, setFormula] = useState<HealthFormula | null>(null);
  const [history, setHistory] = useState<HealthReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [showFormula, setShowFormula] = useState(false);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const [latest, hist] = await Promise.all([
        api.latestHealthScore(projectId).catch(() => null),
        api.healthHistory(projectId).catch(() => ({ history: [] as HealthReport[] })),
      ]);
      if (latest) setReport(latest);
      setHistory(hist.history || []);
      if (!latest) {
        // No snapshot recorded yet — compute one without persisting.
        setReport(await api.projectHealthScore(projectId, { persist: false }));
      }
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load project health.');
    } finally {
      setLoading(false);
    }
  }, [projectId, toast]);

  useEffect(() => { load(); }, [load]);

  const rescore = async () => {
    setBusy(true);
    try {
      const r = await api.projectHealthScore(projectId);
      setReport(r);
      const hist = await api.healthHistory(projectId);
      setHistory(hist.history || []);
      toast.success(`Health rescored: ${r.status}`);
    } catch (err: any) {
      toast.error(err?.message || 'Rescoring failed.');
    } finally {
      setBusy(false);
    }
  };

  const exportReport = async () => {
    if (!report) return;
    const lines = [
      `# Project Health Report — ${report.project_name}`,
      '',
      `Overall score: ${report.overall_score}/100 (${report.status})`,
      `Data completeness: ${report.data_completeness}/100`,
      `Formula version: ${report.formula_version}`,
      '',
      '## Dimensions',
      ...report.dimensions.map(
        (d) => `- ${d.label}: ${d.supported ? `${d.score}/100` : 'not scored (insufficient data)'} — ${d.detail}`,
      ),
      '',
      '## Positive factors',
      ...(report.positives.length ? report.positives.map((p) => `- ${p.text} [${p.evidence}]`) : ['- None identified']),
      '',
      '## Negative factors',
      ...(report.negatives.length ? report.negatives.map((n) => `- ${n.text} [${n.evidence}]`) : ['- None identified']),
      '',
      '## Recommendations',
      ...(report.recommendations.length
        ? report.recommendations.map((r) => `- (${r.priority}) ${r.text}`)
        : ['- None']),
      '',
      'The scores are computed deterministically from stored project data.',
      'The written explanation does not influence any number above.',
    ].join('\n');
    try {
      const blob = new Blob([lines], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${(report.project_name || 'project').replace(/\s+/g, '_')}_health_report.md`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error('Could not build the report file.');
    }
  };

  if (loading) {
    return (
      <div className="panel">
        <div className="panel-body row gap-sm" style={{ color: 'var(--text-2)' }}>
          <Loader2 size={15} className="spin" /> Computing project health…
        </div>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="panel">
        <EmptyState
          icon={<Activity size={22} />}
          title="No health data yet"
          description="Run a health evaluation to score this project from its existing documents, tasks, risks and blockers."
        />
      </div>
    );
  }

  const tone = STATUS_TONE[report.status] || 'neutral';

  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="panel">
        <div className="panel-head">
          <h3><Activity size={16} /> Project Health <span className="ph-sub">· deterministic · formula {report.formula_version}</span></h3>
          <div className="row gap-sm">
            <button className="btn btn-ghost btn-sm" onClick={() => setShowFormula((v) => !v)} title="How this is calculated">
              <Calculator size={14} /> Formula
            </button>
            <button className="btn btn-secondary btn-sm" onClick={rescore} disabled={busy}>
              {busy ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />} Re-score
            </button>
            <button className="btn btn-primary btn-sm" onClick={exportReport}>Export</button>
          </div>
        </div>

        <div className="panel-body col" style={{ gap: 18 }}>
          <div className="row wrap gap-md" style={{ gap: 22 }}>
            <div className="col" style={{ gap: 4, minWidth: 190 }}>
              <div className="eyebrow">Overall health</div>
              <div className="row gap-sm" style={{ alignItems: 'baseline' }}>
                <span style={{ fontSize: 40, fontWeight: 700, letterSpacing: '-0.02em' }}>
                  {report.overall_score.toFixed(1)}
                </span>
                <span className="tiny dim">/ 100</span>
              </div>
              <Badge tone={tone}>{report.status}</Badge>
            </div>

            <div className="col" style={{ gap: 8, flex: 1, minWidth: 240 }}>
              <div className="meter">
                <div
                  className={`meter-fill ${
                    report.overall_score >= 70 ? 'ok' : report.overall_score >= 45 ? 'warn' : 'err'
                  }`}
                  style={{ width: `${Math.max(2, report.overall_score)}%` }}
                />
              </div>
              <div className="row wrap gap-sm tiny dim">
                <span>Excellent 85+</span>
                <span>Healthy 70+</span>
                <span>Watch 55+</span>
                <span>At Risk 40+</span>
                <span>Critical &lt;40</span>
              </div>
              <div className="tiny dim">
                Data completeness {report.data_completeness}/100
                {report.skipped_dimensions.length > 0 && (
                  <> · {report.skipped_dimensions.length} dimension(s) not scored for lack of data</>
                )}
              </div>
            </div>
          </div>

          {report.narrative && (
            <div className="card card-flat" style={{ padding: 15 }}>
              <div className="row-between" style={{ marginBottom: 8 }}>
                <div className="eyebrow">Explanation</div>
                <span className="tiny dim">
                  {report.narrative_provider || 'no provider'} {report.narrative_model || ''}
                </span>
              </div>
              <div className="small" style={{ lineHeight: 1.7 }}>{report.narrative}</div>
              <div className="tiny dim" style={{ marginTop: 9 }}>
                The text above explains the numbers. It does not produce them.
              </div>
            </div>
          )}

          {showFormula && (
            <FormulaCard projectId={projectId} formula={formula} onLoaded={setFormula} />
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel-head"><h3>Dimension breakdown</h3></div>
        <div className="panel-body col" style={{ gap: 14 }}>
          {report.dimensions.map((d) => (
            <div key={d.key} className="col" style={{ gap: 6 }}>
              <div className="row-between">
                <span className="small cell-strong">{d.label}</span>
                {d.supported ? (
                  <span className="small mono">{d.score.toFixed(1)}/100</span>
                ) : (
                  <Badge tone="neutral" plain>not scored</Badge>
                )}
              </div>
              <div className="meter">
                {d.supported ? (
                  <div
                    className={`meter-fill ${d.score >= 70 ? 'ok' : d.score >= 45 ? 'warn' : 'err'}`}
                    style={{ width: `${Math.max(2, d.score)}%` }}
                  />
                ) : (
                  <div className="meter-fill" style={{ width: '0%', opacity: 0.25 }} />
                )}
              </div>
              <div className="tiny dim">{d.detail}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="grid grid-2">
        <div className="panel">
          <div className="panel-head"><h3><TrendingUp size={15} /> Positive factors</h3></div>
          <div className="panel-body col" style={{ gap: 10 }}>
            {report.positives.length === 0 ? (
              <div className="tiny dim">No positive factors were identified from the stored project data.</div>
            ) : (
              report.positives.map((p, i) => (
                <div key={i} className="row gap-sm" style={{ alignItems: 'flex-start' }}>
                  <TrendingUp size={14} style={{ color: 'var(--emerald)', marginTop: 2, flexShrink: 0 }} />
                  <div className="col" style={{ gap: 2 }}>
                    <span className="small">{p.text}</span>
                    <span className="tiny dim mono">{p.evidence}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="panel">
          <div className="panel-head"><h3><TrendingDown size={15} /> Negative factors</h3></div>
          <div className="panel-body col" style={{ gap: 10 }}>
            {report.negatives.length === 0 ? (
              <div className="tiny dim">No negative factors were identified from the stored project data.</div>
            ) : (
              report.negatives.map((n, i) => (
                <div key={i} className="row gap-sm" style={{ alignItems: 'flex-start' }}>
                  <TrendingDown size={14} style={{ color: 'var(--amber)', marginTop: 2, flexShrink: 0 }} />
                  <div className="col" style={{ gap: 2 }}>
                    <span className="small">{n.text}</span>
                    <span className="tiny dim mono">{n.evidence}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head"><h3><Lightbulb size={15} /> Recommendations</h3></div>
        <div className="panel-body col" style={{ gap: 11 }}>
          {report.recommendations.length === 0 ? (
            <div className="tiny dim">No recommendations — the scored dimensions are all healthy.</div>
          ) : (
            report.recommendations.map((r, i) => (
              <div key={i} className="row gap-sm" style={{ alignItems: 'flex-start' }}>
                <Badge tone={r.priority === 'High' ? 'err' : r.priority === 'Medium' ? 'warn' : 'neutral'} plain>
                  {r.priority}
                </Badge>
                <div className="col" style={{ gap: 2 }}>
                  <span className="small">{r.text}</span>
                  <span className="tiny dim">Based on: {r.evidence}</span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h3><History size={15} /> Score history</h3>
          <span className="ph-sub">{history.length} snapshot(s)</span>
        </div>
        <div className="panel-body">
          {history.length === 0 ? (
            <div className="tiny dim">No snapshots recorded yet.</div>
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Recorded</th>
                    <th>Score</th>
                    <th>Status</th>
                    <th>Completeness</th>
                    <th>Skipped</th>
                  </tr>
                </thead>
                <tbody>
                  {history.slice(0, 20).map((h) => (
                    <tr key={h.id}>
                      <td>{fmtDate(h.created_at)}</td>
                      <td className="mono">{h.overall_score}</td>
                      <td><Badge tone={STATUS_TONE[h.status] || 'neutral'}>{h.status}</Badge></td>
                      <td className="mono">{h.data_completeness}</td>
                      <td className="tiny dim">{h.skipped_dimensions?.length || 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function FormulaCard({
  projectId,
  formula,
  onLoaded,
}: {
  projectId: number;
  formula: HealthFormula | null;
  onLoaded: (f: HealthFormula) => void;
}) {
  useEffect(() => {
    if (!formula) api.healthFormula(projectId).then(onLoaded).catch(() => undefined);
  }, [projectId, formula, onLoaded]);

  if (!formula) {
    return <div className="tiny dim row gap-sm"><Loader2 size={13} className="spin" /> Loading the published formula…</div>;
  }

  return (
    <div className="card card-flat col" style={{ gap: 10, padding: 15 }}>
      <div className="row gap-sm">
        <Info size={14} style={{ color: 'var(--cyan)' }} />
        <b className="small">How the score is calculated</b>
      </div>
      <div className="small mono" style={{ lineHeight: 1.6 }}>{formula.overall}</div>
      <div className="tiny dim mono">{formula.status_bands}</div>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Dimension</th>
              <th>Weight</th>
              <th>Required</th>
              <th>Formula</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(formula.dimensions).map(([key, d]) => (
              <tr key={key}>
                <td className="cell-strong">{key}</td>
                <td className="mono">{d.weight}</td>
                <td>{d.required ? 'Yes' : 'If data exists'}</td>
                <td className="tiny dim" style={{ maxWidth: 520, lineHeight: 1.55 }}>{d.formula}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {formula.notes.map((n, i) => (
        <div key={i} className="tiny dim">• {n}</div>
      ))}
    </div>
  );
}
