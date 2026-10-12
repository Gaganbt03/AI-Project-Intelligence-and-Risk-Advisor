import { CircleSlash } from 'lucide-react';
import { AppShell } from '../layout/AppShell';
import { EmptyState } from '../components/EmptyState';
import { useWorkspace } from '../workspace/WorkspaceContext';
import { BlockersPanel } from '../panels/BlockersPanel';

export default function Blockers() {
  const { projectId } = useWorkspace();

  return (
    <AppShell title="Blockers" crumb="Issues · Blocker Log">
      {projectId ? (
        <BlockersPanel projectId={projectId} />
      ) : (
        <EmptyState icon={<CircleSlash size={26} />} title="Preparing workspace" description="Your private workspace is loading." />
      )}
    </AppShell>
  );
}
