import { type ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  FolderKanban,
  Users,
  FileText,
  Bot,
  ScrollText,
  LogOut,
  ListChecks,
  TriangleAlert,
  CircleSlash,
  ShieldCheck,
} from 'lucide-react';
import { useAuth } from '../auth/AuthContext';
import { initials } from '../utils/format';

export interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  end?: boolean;
}

const APP_NAV: { group: string; items: NavItem[] }[] = [
  {
    group: '',
    items: [{ to: '/', label: 'Dashboard', icon: <LayoutDashboard size={17} />, end: true }],
  },
  {
    group: 'Management',
    items: [
      { to: '/projects', label: 'Projects', icon: <FolderKanban size={17} /> },

      { to: '/documents', label: 'Documents', icon: <FileText size={17} /> },
    ],
  },
  {
    group: 'Intelligence',
    items: [
      { to: '/tasks', label: 'Tasks', icon: <ListChecks size={17} /> },
      { to: '/risks', label: 'Risks', icon: <TriangleAlert size={17} /> },
      { to: '/blockers', label: 'Blockers', icon: <CircleSlash size={17} /> },
      { to: '/report-blocker', label: 'Report Blocker', icon: <CircleSlash size={17} /> },
      { to: '/assistant', label: 'Project Assistant', icon: <Bot size={17} /> },
    ],
  },
  {
    group: 'System',
    items: [
      { to: '/audit-logs', label: 'Audit Logs', icon: <ScrollText size={17} /> },
    ],
  },
];

const nav = APP_NAV;

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
  const { user, logout } = useAuth();
  const isAdmin = false;
  const groups = nav;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="brand-logo">
            <Bot size={19} />
          </div>
          <div className="brand-text">
            <div className="brand-name">Project Intelligence</div>
            <div className="brand-sub">Risk Advisor</div>
          </div>
        </div>

        <nav className="nav">
          {groups.map((g: any, i: number) => (
            <div key={i}>
              {g.group && <div className="nav-group-label">{g.group}</div>}
              {g.items.map((item: NavItem) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                >
                  {item.icon}
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className="user-chip">
            <div className="avatar">{initials(user?.name)}</div>
            <div className="uinfo" style={{ flex: 1, minWidth: 0 }}>
              <div className="uname" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {user?.name}
              </div>
              <div className="urole">{false ? 'Administrator' : 'Employee'}</div>
            </div>
            <button className="btn btn-icon" onClick={() => logout()} title="Logout" style={{ marginLeft: 'auto' }}>
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

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

export function RoleIcon() {
  return <ShieldCheck size={15} />;
}