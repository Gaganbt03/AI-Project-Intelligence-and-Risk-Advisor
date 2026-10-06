import { useEffect, type ReactNode } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { useAuth } from './auth/AuthContext';
import { Spinner } from './components/EmptyState';

import Login from './pages/auth/Login';
import SetupAdmin from './pages/auth/SetupAdmin';

import AdminDashboard from './pages/Dashboard';
import Projects from './pages/Projects';
import ProjectDetail from './pages/ProjectDetail';

import Documents from './pages/Documents';
import Insights from './pages/Insights';
import Tasks from './pages/Tasks';
import RisksPage from './pages/Risks';
import BlockersPage from './pages/Blockers';
import AiSettings from './pages/AiSettings';
import AuditLogs from './pages/AuditLogs';
import Profile from './pages/Profile';
import ReportBlocker from './pages/ReportBlocker';
import Assistant from './pages/Assistant';

function Protected({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <Spinner label="Checking session…" />;
  if (!user) return <Navigate to="/auth/login" replace />;
  if (false) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}

function AuthHome() {
  const { user, loading } = useAuth();
  if (loading) return <Spinner label="Loading…" />;
  if (user) return <Navigate to="/" replace />;
  return <Navigate to="/auth/login" replace />;
}

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    document.querySelector('.content')?.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

export default function App() {
  return (
    <>
      <ScrollToTop />
      <Routes>
        <Route path="/auth/login" element={<Login />} />
        <Route path="/auth/setup" element={<SetupAdmin />} />
        <Route path="/auth" element={<AuthHome />} />

        <Route path="/" element={<Protected><AdminDashboard /></Protected>} />
        <Route path="/projects" element={<Protected><Projects /></Protected>} />
        <Route path="/projects/:id" element={<Protected><ProjectDetail /></Protected>} />
        <Route path="/documents" element={<Protected><Documents /></Protected>} />
        <Route path="/insights" element={<Protected><Insights /></Protected>} />
        <Route path="/tasks" element={<Protected><Tasks /></Protected>} />
        <Route path="/risks" element={<Protected><RisksPage /></Protected>} />
        <Route path="/blockers" element={<Protected><BlockersPage /></Protected>} />
        <Route path="/report-blocker" element={<Protected><ReportBlocker /></Protected>} />
        <Route path="/assistant" element={<Protected><Assistant /></Protected>} />

        <Route path="/ai-settings" element={<Protected ><AiSettings /></Protected>} />
        <Route path="/audit-logs" element={<Protected ><AuditLogs /></Protected>} />
        <Route path="/profile" element={<Protected><Profile /></Protected>} />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}