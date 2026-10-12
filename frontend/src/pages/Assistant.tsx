import { Bot } from 'lucide-react';
import { AppShell } from '../layout/AppShell';
import { EmptyState } from '../components/EmptyState';
import { useWorkspace } from '../workspace/WorkspaceContext';
import { AssistantPanel } from '../panels/AssistantPanel';

export default function Assistant() {
  const { projectId } = useWorkspace();

  return (
    <AppShell title="AI Assistant" crumb="Analysis · Q&A">
      {projectId ? (
        <AssistantPanel projectId={projectId} />
      ) : (
        <EmptyState icon={<Bot size={26} />} title="Preparing workspace" description="Your private workspace is loading." />
      )}
    </AppShell>
  );
}
