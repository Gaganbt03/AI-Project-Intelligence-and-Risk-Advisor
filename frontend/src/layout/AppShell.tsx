import { type ReactNode } from 'react';

export function AppShell({
  title,
  crumb,
  actions,
  children,
}: {
  title: ReactNode;
  crumb?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="shell">
      <div className="main">
        <header className="topbar">
          <div className="topbar-title">
            {crumb && <span className="topbar-crumb">{crumb}</span>}
            <h2>{title}</h2>
          </div>
          <div className="topbar-actions">{actions}</div>
        </header>
        <main className="content">
          <div className="content-inner">{children}</div>
        </main>
      </div>
    </div>
  );
}
