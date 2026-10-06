import { useEffect, useState } from 'react';
import { ListChecks } from 'lucide-react';
import { AppShell } from '../layout/AppShell';
import { EmptyState } from '../components/EmptyState';
import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import ProjectSelector from '../components/ProjectSelector';
import { TasksPanel } from '../panels/TasksPanel';

export default function Tasks() {
  const { user } = useAuth();
  const isAdmin = true;
  const [projectId, setProjectId] = useState<number | null>(null);
  const [users, setUsers] = useState<any[]>([]);

  useEffect(() => {
    if (isAdmin) api.listUsers().then(setUsers).catch(() => {});
  }, [isAdmin]);

  return (
    <AppShell title={isAdmin ? 'Tasks' : 'My Tasks'} crumb="Work · Tasks">
      <ProjectSelector projectId={projectId} onChange={setProjectId} />
      {projectId ? (
        <TasksPanel
          projectId={projectId}
          users={users
            .filter((u) => (u.project_ids || []).includes(projectId))
            .map((u) => ({ id: u.id, name: u.name }))}
        />
      ) : (
        <EmptyState icon={<ListChecks size={26} />} title="Select a project" description="Choose a project above to view and manage tasks." />
      )}
    </AppShell>
  );
}