import { TriangleAlert } from 'lucide-react';
import { AppShell } from '../layout/AppShell';
import { EmptyState } from '../components/EmptyState';
import { useWorkspace } from '../workspace/WorkspaceContext';
import { RisksPanel } from '../panels/RisksPanel';

export default function Risks() {
  const { projectId } = useWorkspace();

  return (
    <AppShell title="Risks" crumb="Risk Register">
      {projectId ? (
        <RisksPanel projectId={projectId} />
      ) : (
        <EmptyState icon={<TriangleAlert size={26} />} title="Preparing workspace" description="Your private workspace is loading." />
      )}
    </AppShell>
  );
}
