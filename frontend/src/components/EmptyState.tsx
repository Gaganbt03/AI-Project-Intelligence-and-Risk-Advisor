import type { ReactNode } from 'react';
import { Sparkles } from 'lucide-react';

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-ico">{icon || <Sparkles size={26} />}</div>
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="loading-screen">
      <div className="loading-logo">
        <Sparkles size={28} />
      </div>
      {label && <div className="muted small">{label}</div>}
    </div>
  );
}

export function LoadingDots() {
  return (
    <span className="row gap-sm">
      <span className="spinner" />
      <span className="muted small">Loading…</span>
    </span>
  );
}

export function PageLoader({ label }: { label?: string }) {
  return (
    <div style={{ display: 'grid', placeItems: 'center', padding: '70px 0' }}>
      <div className="col" style={{ alignItems: 'center' }}>
        <span className="spinner spinner-lg" />
        {label && <div className="muted small">{label}</div>}
      </div>
    </div>
  );
}