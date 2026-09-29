import { useState } from 'react';
import { Sparkles } from 'lucide-react';
import { AppShell } from '../layout/AppShell';
import { EmptyState } from '../components/EmptyState';
import ProjectSelector from '../components/ProjectSelector';
import { InsightsPanel } from '../panels/InsightsPanel';

export default function Insights() {
  const [projectId, setProjectId] = useState<number | null>(null);

  return (
    <AppShell title="AI Insights" crumb="Analysis · Agent Runs">
      <ProjectSelector projectId={projectId} onChange={setProjectId} />
      {projectId ? (
        <InsightsPanel projectId={projectId} />
      ) : (
        <EmptyState icon={<Sparkles size={26} />} title="Select a project" description="Choose a project above to run or review AI analysis." />
      )}
    </AppShell>
  );
}