import { useState } from 'react';
import { TriangleAlert } from 'lucide-react';
import { AppShell } from '../layout/AppShell';
import { EmptyState } from '../components/EmptyState';
import ProjectSelector from '../components/ProjectSelector';
import { RisksPanel } from '../panels/RisksPanel';

export default function Risks() {
  const [projectId, setProjectId] = useState<number | null>(null);

  return (
    <AppShell title="Risks" crumb="Risk Register">
      <ProjectSelector projectId={projectId} onChange={setProjectId} />
      {projectId ? (
        <RisksPanel projectId={projectId} />
      ) : (
        <EmptyState icon={<TriangleAlert size={26} />} title="Select a project" description="Choose a project above to review its risk register." />
      )}
    </AppShell>
  );
}