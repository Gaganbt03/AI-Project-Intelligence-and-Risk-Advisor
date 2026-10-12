import { useEffect } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';

import Projects from './pages/Projects';
import ProjectDetail from './pages/ProjectDetail';

import Documents from './pages/Documents';
import Tasks from './pages/Tasks';
import RisksPage from './pages/Risks';
import BlockersPage from './pages/Blockers';
import AuditLogs from './pages/AuditLogs';
import Profile from './pages/Profile';
import ReportBlocker from './pages/ReportBlocker';
import Assistant from './pages/Assistant';
import { EmptyState, Spinner } from './components/EmptyState';
import { useWorkspace } from './workspace/WorkspaceContext';

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    document.querySelector('.content')?.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

/** Landing screen: the workspace's own Documents upload view. */
function WorkspaceHome() {
  const { projectId } = useWorkspace();
  if (!projectId) return null;
  return <Navigate to={`/projects/${projectId}`} replace />;
}

export default function App() {
  const { loading, error, retry } = useWorkspace();

  if (loading) {
    return <Spinner label="Preparing your private workspace…" />;
  }
  if (error) {
    return (
      <EmptyState
        title="Workspace unavailable"
        description={error}
        action={<button className="btn btn-primary" onClick={retry}>Try again</button>}
      />
    );
  }

  return (
    <>
      <ScrollToTop />
      <Routes>
        <Route path="/" element={<WorkspaceHome />} />
        <Route path="/projects" element={<Projects />} />
        <Route path="/projects/:id" element={<ProjectDetail />} />
        <Route path="/documents" element={<Documents />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/risks" element={<RisksPage />} />
        <Route path="/blockers" element={<BlockersPage />} />
        <Route path="/report-blocker" element={<ReportBlocker />} />
        <Route path="/assistant" element={<Assistant />} />

        <Route path="/audit-logs" element={<AuditLogs />} />
        <Route path="/profile" element={<Profile />} />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
