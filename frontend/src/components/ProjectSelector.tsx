import { useEffect, useState } from 'react';
import { FolderKanban } from 'lucide-react';
import { api } from '../api/client';

const STORAGE_KEY = 'apir_project_id';

export default function ProjectSelector({ projectId, onChange }: { projectId: number | null; onChange: (id: number) => void }) {
  const [projects, setProjects] = useState<any[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    api.listProjects()
      .then((available) => {
        if (!active) return;
        setProjects(available);
        const currentIsAvailable = projectId !== null && available.some((project) => project.id === projectId);
        const remembered = Number(localStorage.getItem(STORAGE_KEY));
        const rememberedIsAvailable = available.some((project) => project.id === remembered);
        const nextId = currentIsAvailable
          ? projectId
          : rememberedIsAvailable
            ? remembered
            : available[0]?.id;
        if (nextId) {
          localStorage.setItem(STORAGE_KEY, String(nextId));
          if (nextId !== projectId) onChange(nextId);
        }
      })
      .catch((e: any) => {
        if (active) setError(e?.message || 'Failed to load projects.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [onChange, projectId]);

  const selectProject = (id: number) => {
    localStorage.setItem(STORAGE_KEY, String(id));
    onChange(id);
  };

  return (
    <div className="panel">
      <div className="panel-head">
        <h3><FolderKanban size={15} /> Project</h3>
      </div>
      <div className="panel-body">
        {loading ? (
          <div className="row gap-sm tiny dim"><span className="spinner" /> Loading projects…</div>
        ) : error ? (
          <div className="form-error">{error}</div>
        ) : projects.length === 0 ? (
          <div className="tiny dim">No accessible projects. Ask an administrator to assign you to a project.</div>
        ) : (
          <div className="chip-row">
            {projects.map((p) => (
              <button
                key={p.id}
                type="button"
                className={`chip ${p.id === projectId ? 'active' : ''}`}
                onClick={() => selectProject(p.id)}
              >
                {p.id === projectId ? '✓ ' : ''}{p.name}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}