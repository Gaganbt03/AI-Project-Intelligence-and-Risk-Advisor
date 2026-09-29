import { useState } from 'react';
import { Bot } from 'lucide-react';
import { AppShell } from '../layout/AppShell';
import { EmptyState } from '../components/EmptyState';
import ProjectSelector from '../components/ProjectSelector';
import { AssistantPanel } from '../panels/AssistantPanel';

export default function Assistant() {
  const [projectId, setProjectId] = useState<number | null>(null);

  return (
    <AppShell title="AI Assistant" crumb="Analysis · Q&A">
      <ProjectSelector projectId={projectId} onChange={setProjectId} />
      {projectId ? (
        <AssistantPanel projectId={projectId} />
      ) : (
        <EmptyState icon={<Bot size={26} />} title="Select a project" description="Choose a project above to ask questions about its documents." />
      )}
    </AppShell>
  );
}