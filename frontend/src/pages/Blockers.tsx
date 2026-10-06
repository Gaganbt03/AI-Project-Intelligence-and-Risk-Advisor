import { useEffect, useState } from 'react';
import { CircleSlash } from 'lucide-react';
import { AppShell } from '../layout/AppShell';
import { EmptyState } from '../components/EmptyState';
import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import ProjectSelector from '../components/ProjectSelector';
import { BlockersPanel } from '../panels/BlockersPanel';

export default function Blockers() {
  const { user } = useAuth();
  const isAdmin = false;
  const [projectId, setProjectId] = useState<number | null>(null);
  const [users, setUsers] = useState<any[]>([]);

  useEffect(() => {
    if (isAdmin) api.listUsers().then(setUsers).catch(() => {});
  }, [isAdmin]);

  return (
    <AppShell title="Blockers" crumb="Issues · Blocker Log">
      <ProjectSelector projectId={projectId} onChange={setProjectId} />
      {projectId ? (
        <BlockersPanel projectId={projectId} employees={users.map((u) => ({ id: u.id, name: u.name }))} />
      ) : (
        <EmptyState icon={<CircleSlash size={26} />} title="Select a project" description="Choose a project above to view or report blockers." />
      )}
    </AppShell>
  );
}