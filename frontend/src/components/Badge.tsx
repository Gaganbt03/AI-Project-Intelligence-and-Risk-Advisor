import type { ReactNode } from 'react';

export function Badge({
  tone = 'neutral',
  plain,
  children,
  pulse,
}: {
  tone?:
    | 'neutral'
    | 'violet'
    | 'magenta'
    | 'ok'
    | 'warn'
    | 'err'
    | 'cyan'
    | 'critical'
    | 'high'
    | 'medium'
    | 'low';
  plain?: boolean;
  children: ReactNode;
  pulse?: boolean;
}) {
  return (
    <span className={`badge badge-${tone} ${plain ? 'badge-plain' : ''} ${pulse ? 'pulse' : ''}`}>
      {children}
    </span>
  );
}

// Status -> tone mapping helpers
export function statusTone(v: string): 'ok' | 'warn' | 'err' | 'neutral' | 'violet' | 'cyan' {
  const s = (v || '').toLowerCase();
  if (['completed', 'processed', 'resolved', 'active', 'mitigated', 'closed', 'connected', 'high available'].includes(s)) return 'ok';
  if (['pending', 'in progress', 'processing', 'on hold', 'uploaded', 'not started', 'medium'].includes(s)) return 'warn';
  if (['failed', 'blocked', 'archived', 'deactivated', 'critical', 'error'].includes(s)) return 'err';
  if (['open', 'planning', 'low', 'not configured'].includes(s)) return 'neutral';
  if (['ai detected', 'ai_detected', 'ai generated', 'ai_generated'].includes(s)) return 'violet';
  return 'cyan';
}

export function SeverityBadge({ value }: { value: string }) {
  const v = (value || '').toLowerCase();
  const tone = ['critical', 'high', 'medium', 'low'].includes(v) ? (v as any) : 'neutral';
  return <Badge tone={tone}>{value || '—'}</Badge>;
}