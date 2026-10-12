import { useEffect, useState } from 'react';
import { ListChecks } from 'lucide-react';
import { AppShell } from '../layout/AppShell';
import { EmptyState } from '../components/EmptyState';
import { api } from '../api/client';
import { useWorkspace } from '../workspace/WorkspaceContext';
import { TasksPanel } from '../panels/TasksPanel';

export default function Tasks() {
  const { projectId } = useWorkspace();
  const [users, setUsers] = useState<any[]>([]);

  useEffect(() => {
    api.listUsers().then(setUsers).catch(() => {});
  }, []);

  return (
    <AppShell title="Tasks" crumb="Work · Tasks">
      {projectId ? (
        <TasksPanel projectId={projectId} users={users.map((u) => ({ id: u.id, name: u.name }))} />
      ) : (
        <EmptyState icon={<ListChecks size={26} />} title="Preparing workspace" description="Your private workspace is loading." />
      )}
    </AppShell>
  );
}
