import { useState } from 'react';
import { CircleSlash } from 'lucide-react';
import { AppShell } from '../layout/AppShell';
import { EmptyState } from '../components/EmptyState';
import ProjectSelector from '../components/ProjectSelector';
import { BlockersPanel } from '../panels/BlockersPanel';

export default function ReportBlocker() {
  const [projectId, setProjectId] = useState<number | null>(null);

  return (
    <AppShell title="Report Blocker" crumb="Issues · Report">
      <ProjectSelector projectId={projectId} onChange={setProjectId} />
      {projectId ? (
        <BlockersPanel projectId={projectId} employees={[]} />
      ) : (
        <EmptyState icon={<CircleSlash size={26} />} title="Select a project" description="Choose the project you are blocked on to file a quick report." />
      )}
    </AppShell>
  );
}