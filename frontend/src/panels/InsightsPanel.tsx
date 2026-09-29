import { useCallback, useEffect, useMemo, useState } from 'react';
import { Sparkles, Play, CheckCircle2, XCircle, Loader2, FileText, CalendarClock, CircleDot, ListChecks } from 'lucide-react';
import { api } from '../api/client';
import { EmptyState } from '../components/EmptyState';
import { Badge, SeverityBadge, statusTone } from '../components/Badge';
import { useToast } from '../ui/ToastContext';
import { useAuth } from '../auth/AuthContext';

const AGENT_LABELS: Record<string, string> = {
  scope: 'Scope Extraction Agent',
  risk: 'Risk & Delivery Forecast Agent',
  forecast: 'Delivery Forecasting',
  blocker: 'Blocker Agent',
  action: 'Action Item Agent',
};

export function InsightsPanel({ projectId, onRunDone }: { projectId: number; onRunDone?: () => void }) {
  const [insights, setInsights] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<any>(null);
  const [revealed, setRevealed] = useState<string[]>([]);
  const { user } = useAuth();
  const toast = useToast();
  const isAdmin = user?.role === 'ADMIN';

  const load = useCallback(async () => {
    try {
      setInsights(await api.listInsights(projectId));
    } catch (err: any) {
      toast.error(err?.message || 'Failed to load insights.');
    } finally {
      setLoading(false);
    }
  }, [projectId, toast]);

  useEffect(() => { load(); }, [load]);

  const run = async () => {
    setRunning(true);
    setRunResult(null);
    setRevealed([]);
    const stepKeys = ['scope', 'risk', 'forecast', 'blocker', 'action'];
    const timer = window.setInterval(() => {
      setRevealed((r) => (r.length < stepKeys.length ? [...r, stepKeys[r.length]] : r));
    }, 1600);
    try {
      const res = await api.runAnalysis(projectId);
      setRunResult(res);
      toast.success('Project Intelligence Analysis completed.');
      load();
      onRunDone?.();
    } catch (err: any) {
      toast.error(err?.message || 'Analysis failed.');
    } finally {
      window.clearInterval(timer);
      setRevealed(stepKeys);
      setRunning(false);
    }
  };

  const groupBy = (agent: string) => insights.filter((i) => i.agent === agent);

  const scopeItems = groupBy('scope');
  const riskInsights = groupBy('risk');
  const forecast = groupBy('forecast')[0];
  const blockerInsights = groupBy('blocker');
  const actionInsights = groupBy('action');
  const healthRow = groupBy('health')[0];

  const stats = useMemo(() => {
    const severityCounts = (list: any[]) => {
      const m: Record<string, number> = {};
      list.forEach((i) => {
        const s = i.payload?.severity || 'Unknown';
        m[s] = (m[s] || 0) + 1;
      });
      return m;
    };
    return {
      deliverableCount: scopeItems.filter((s) => s.category === 'deliverable').length,
      scopeCount: scopeItems.filter((s) => s.category === 'scope').length,
      milestoneCount: scopeItems.filter((s) => s.category === 'milestone').length,
      riskSev: severityCounts(riskInsights),
      blockerCount: blockerInsights.length,
      actionCount: actionInsights.length,
      actionOpen: actionInsights.filter((a) => a.payload?.task_id).length,
    };
  }, [scopeItems, riskInsights, blockerInsights, actionInsights]);

  return (
    <div className="col">
      {isAdmin && (
        <div className="panel">
          <div className="panel-head">
            <h3><Play size={15} /> Run Project Intelligence Analysis</h3>
            <span className="ph-sub">Scope · Risk · Forecast · Blocker · Action Items</span>
          </div>
          <div className="panel-body col">
            <button className="btn btn-primary btn-lg" onClick={run} disabled={running} style={{ alignSelf: 'flex-start' }}>
              {running ? <Loader2 size={17} className="spin" /> : <Sparkles size={17} />}
              {running ? 'Analyzing project…' : 'Run Project Intelligence Analysis'}
            </button>
            <div className="tiny dim">
              Runs all five agents over retrieved project-document context. AI results are validated against schemas before storage.
            </div>

            {(running || runResult) && (
              <div className="ai-steps mt-1">
                {(() => {
                  const steps = runResult
                    ? Object.keys(runResult.agents).map((k) => ({
                        key: k,
                        status: runResult.agents[k].status,
                        count: runResult.agents[k].count || 0,
                        error: runResult.agents[k].error,
                      }))
                    : ['scope', 'risk', 'forecast', 'blocker', 'action'].map((k) => ({
                        key: k,
                        status: revealed.includes(k) ? 'running' : 'pending',
                        count: 0,
                        error: '',
                      }));
                  return steps.map((step, idx) => {
                    const state = step.status === 'running' ? 'running' : step.status === 'Completed' ? 'done' : step.status === 'Failed' ? 'failed' : 'pending';
                    return (
                      <div key={`${step.key}-${idx}`} className={`ai-step ${state}`}>
                        {state === 'done' ? <CheckCircle2 size={15} style={{ color: 'var(--emerald)' }} />
                          : state === 'failed' ? <XCircle size={15} style={{ color: 'var(--red)' }} />
                          : state === 'running' ? <Loader2 size={15} className="spin" /> : <CircleDot size={15} />}
                        <span className="cell-strong">{AGENT_LABELS[step.key] || step.key}</span>
                        {state === 'done' ? (
                          <span className="step-time">✓ {step.key === 'forecast' ? 'completed' : `${step.count} extracted`}</span>
                        ) : state === 'failed' ? (
                          <span className="step-time" style={{ color: 'var(--red)' }}>{step.error?.slice(0, 90) || 'failed'}</span>
                        ) : state === 'running' ? (
                          <span className="step-time">retrieving context…</span>
                        ) : null}
                      </div>
                    );
                  });
                })()}
                {runResult?.health_metrics && (
                  <div className="tiny dim" style={{ padding: '4px 2px' }}>
                    Health metrics ready for later scoring: {runResult.health_metrics.task_completion_rate}% completion · {runResult.health_metrics.documents} documents · {runResult.health_metrics.open_risks} open risks
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {loading ? (
        <div className="row gap-sm" style={{ padding: 24 }}><span className="spinner" /><span className="muted small">Loading insights…</span></div>
      ) : insights.length === 0 && !runResult ? (
        <EmptyState
          icon={<Sparkles size={26} />}
          title="No AI insights yet"
          description={isAdmin ? 'Upload project documents, then run the analysis to extract scope, risks, blockers and action items.' : 'The administrator has not run a full AI analysis for this project yet.'}
        />
      ) : (
        <>
          {!isAdmin && runResult && (
            <div className="form-success"><CheckCircle2 size={15} /> AI analysis was completed by the administrator.</div>
          )}

          {stats.deliverableCount > 0 && (
            <div className="panel">
              <div className="panel-head"><h3>Scope Summary</h3><span className="ph-sub">{stats.scopeCount} scope items · {stats.deliverableCount} deliverables · {stats.milestoneCount} milestones</span></div>
              <div className="panel-body grid grid-auto">
                {scopeItems.slice(0, 8).map((s) => (
                  <div className="card card-flat col gap-sm" key={s.id} style={{ padding: 14 }}>
                    <Badge tone={s.category === 'deliverable' ? 'ok' : s.category === 'milestone' ? 'cyan' : s.category === 'out_of_scope' ? 'medium' : 'violet'} plain>
                      {s.category === 'project_goal' ? 'Goal' : s.category}
                    </Badge>
                    <div className="small cell-strong">{s.title}</div>
                    {s.summary && <div className="tiny dim">{s.summary}</div>}
                    {s.evidence?.length > 0 && <EvidenceList evidence={s.evidence} />}
                  </div>
                ))}
              </div>
            </div>
          )}

          {forecast && (
            <div className="card card-glow">
              <div className="row-between wrap">
                <div className="row gap-sm">
                  <CalendarClock size={18} style={{ color: 'var(--violet-2)' }} />
                  <h3 style={{ fontSize: 15.5 }}>Delivery Forecast</h3>
                </div>
                <Badge tone={statusTone(forecast.payload?.schedule_status)}>{forecast.payload?.schedule_status}</Badge>
              </div>
              <div className="divider mt-1 mb-2" />
              <div className="col" style={{ gap: 12 }}>
                <div className="kv">
                  <dt>Current Status</dt>
                  <dd>{forecast.payload?.current_status || '—'}</dd>
                </div>
                <div className="kv">
                  <dt>Expected Delivery</dt>
                  <dd>{forecast.payload?.expected_delivery || 'Not forecast'}</dd>
                </div>
                {forecast.payload?.factors?.map((f: any, i: number) => (
                  <div key={i} className="evidence">
                    <CircleDot size={13} style={{ flexShrink: 0, marginTop: 2 }} />
                    <div>
                      <b>{f.kind}</b> · {f.description}
                      {f.evidence && <div className="tiny dim" style={{ marginTop: 2 }}>{f.evidence}</div>}
                    </div>
                  </div>
                ))}
              </div>
              {forecast.payload?.caveat && (
                <div className="tiny dim mt-2" style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
                  {forecast.payload.caveat}
                </div>
              )}
            </div>
          )}

          {riskInsights.length > 0 && (
            <div className="panel">
              <div className="panel-head"><h3>Detected Risks</h3><span className="ph-sub">{riskInsights.length} total</span></div>
              <div className="panel-body col">
                <div className="grid grid-4">
                  {['Critical', 'High', 'Medium', 'Low'].map((s) => (
                    <div key={s} className="card card-flat row-between" style={{ padding: 12 }}>
                      <Badge tone={s.toLowerCase() as any} plain>{s}</Badge>
                      <b>{stats.riskSev[s] || 0}</b>
                    </div>
                  ))}
                </div>
                {riskInsights.slice(0, 12).map((r) => (
                  <div key={r.id} className="panel" style={{ borderRadius: 'var(--r-md)' }}>
                    <div className="panel-body" style={{ padding: 14 }}>
                      <div className="row-between wrap">
                        <div className="cell-strong small">{r.title}</div>
                        <SeverityBadge value={r.payload?.severity} />
                      </div>
                      <div className="tiny dim mt-1">{r.summary}</div>
                      {r.payload?.recommended_action && (
                        <div className="tiny muted mt-1" style={{ color: 'var(--violet-2)' }}>
                          Recommended: {r.payload.recommended_action}
                        </div>
                      )}
                      {r.evidence?.length > 0 && <EvidenceList evidence={r.evidence} />}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {blockerInsights.length > 0 && (
            <div className="panel">
              <div className="panel-head"><h3>Detected Blockers</h3><span className="ph-sub">{blockerInsights.length}</span></div>
              <div className="panel-body col">
                {blockerInsights.slice(0, 12).map((b) => (
                  <div key={b.id} className="panel" style={{ borderRadius: 'var(--r-md)' }}>
                    <div className="panel-body" style={{ padding: 14 }}>
                      <div className="row-between wrap">
                        <div className="cell-strong small">{b.title}</div>
                        <SeverityBadge value={b.payload?.severity} />
                      </div>
                      <div className="tiny dim mt-1">{b.summary}</div>
                      {b.payload?.owner && <div className="tiny muted mt-1">Owner: {b.payload.owner}</div>}
                      {b.evidence?.length > 0 && <EvidenceList evidence={b.evidence} />}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {actionInsights.length > 0 && (
            <div className="panel">
              <div className="panel-head"><h3>Extracted Action Items</h3><span className="ph-sub">{actionInsights.length} → tasks queue (<ListChecks size={12} style={{ verticalAlign: 'middle' }} />)</span></div>
              <div className="panel-body col">
                {actionInsights.slice(0, 12).map((a) => (
                  <div key={a.id} className="panel" style={{ borderRadius: 'var(--r-md)' }}>
                    <div className="panel-body" style={{ padding: 14 }}>
                      <div className="row-between wrap">
                        <div className="cell-strong small">{a.title}</div>
                        <Badge tone={a.payload?.priority?.toLowerCase() || 'neutral'}>{a.payload?.priority || 'Medium'}</Badge>
                      </div>
                      {a.summary && <div className="tiny dim mt-1">{a.summary}</div>}
                      {a.payload?.deadline && <div className="tiny muted mt-1">Deadline: {a.payload.deadline}</div>}
                      {a.evidence?.length > 0 && <EvidenceList evidence={a.evidence} />}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {healthRow && (
            <div className="panel">
              <div className="panel-head"><h3>Health Metrics</h3><span className="ph-sub">underlying data for the future scoring module</span></div>
              <div className="panel-body grid grid-2">
                <MetricRow label="Task completion" value={`${healthRow.payload?.task_completion_rate ?? 0}%`} />
                <MetricRow label="Schedule status" value={healthRow.payload?.schedule_status || 'Unknown'} />
                <MetricRow label="Documents" value={String(healthRow.payload?.documents ?? 0)} />
                <MetricRow label="Open risks" value={String(healthRow.payload?.open_risks ?? 0)} />
                <MetricRow label="Open blockers" value={String(healthRow.payload?.open_blockers ?? 0)} />
                <MetricRow label="Total tasks" value={String(healthRow.payload?.total_tasks ?? 0)} />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="card card-flat row-between" style={{ padding: 13 }}>
      <span className="tiny dim">{label}</span>
      <b style={{ fontFamily: 'var(--mono)' }}>{value}</b>
    </div>
  );
}

function EvidenceList({ evidence }: { evidence: any[] }) {
  if (!evidence.length) return null;
  const src = evidence[0];
  return (
    <div className="evidence" style={{ marginTop: 8 }}>
      <FileText size={13} style={{ flexShrink: 0, marginTop: 2 }} />
      <div>
        <div className="small"><b>{src.document}</b>{src.page ? ` — Page ${src.page}` : ''}{src.section ? ` — ${src.section}` : ''}</div>
        {src.quote && <div className="tiny dim" style={{ marginTop: 2 }}>"{src.quote.slice(0, 180)}{src.quote.length > 180 ? '…' : ''}"</div>}
      </div>
    </div>
  );
}