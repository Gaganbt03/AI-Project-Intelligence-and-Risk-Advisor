import { type ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  FolderKanban,
  Users,
  FileText,
  Bot,
  Cpu,
  ScrollText,
  LogOut,
  ListChecks,
  TriangleAlert,
  CircleSlash,
  Sparkles,
  ShieldCheck,
} from 'lucide-react';
import { useAuth } from '../auth/AuthContext';
import { initials } from '../utils/format';

export interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  end?: boolean;
  roles?: string[];
}

const ADMIN_NAV: { group: string; items: NavItem[] }[] = [
  {
    group: '',
    items: [{ to: '/', label: 'Dashboard', icon: <LayoutDashboard size={17} />, end: true }],
  },
  {
    group: 'Management',
    items: [
      { to: '/projects', label: 'Projects', icon: <FolderKanban size={17} /> },
      { to: '/employees', label: 'Employees', icon: <Users size={17} /> },
      { to: '/documents', label: 'Documents', icon: <FileText size={17} /> },
    ],
  },
  {
    group: 'Intelligence',
    items: [
      { to: '/insights', label: 'AI Insights', icon: <Sparkles size={17} /> },
      { to: '/tasks', label: 'Tasks', icon: <ListChecks size={17} /> },
      { to: '/risks', label: 'Risks', icon: <TriangleAlert size={17} /> },
      { to: '/blockers', label: 'Blockers', icon: <CircleSlash size={17} /> },
    ],
  },
  {
    group: 'System',
    items: [
      { to: '/ai-settings', label: 'AI Providers', icon: <Cpu size={17} />, roles: ['ADMIN'] },
      { to: '/audit-logs', label: 'Audit Logs', icon: <ScrollText size={17} />, roles: ['ADMIN'] },
    ],
  },
];

const EMPLOYEE_NAV: { group: string; items: NavItem[] }[] = [
  {
    group: '',
    items: [{ to: '/', label: 'Dashboard', icon: <LayoutDashboard size={17} />, end: true }],
  },
  {
    group: 'My Work',
    items: [
      { to: '/projects', label: 'My Projects', icon: <FolderKanban size={17} /> },
      { to: '/tasks', label: 'My Tasks', icon: <ListChecks size={17} /> },
      { to: '/risks', label: 'Risks', icon: <TriangleAlert size={17} /> },
      { to: '/blockers', label: 'Blockers', icon: <CircleSlash size={17} /> },
      { to: '/insights', label: 'AI Insights', icon: <Sparkles size={17} /> },
      { to: '/report-blocker', label: 'Report Blocker', icon: <CircleSlash size={17} /> },
      { to: '/documents', label: 'Documents', icon: <FileText size={17} /> },
      { to: '/assistant', label: 'Project Assistant', icon: <Bot size={17} /> },
    ],
  },
];

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
  const isAdmin = user?.role === 'ADMIN';
  const groups = isAdmin ? ADMIN_NAV : EMPLOYEE_NAV;

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
          {groups.map((g, i) => (
            <div key={i}>
              {g.group && <div className="nav-group-label">{g.group}</div>}
              {g.items.map((item) => (
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
              <div className="urole">{user?.role === 'ADMIN' ? 'Administrator' : 'Employee'}</div>
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