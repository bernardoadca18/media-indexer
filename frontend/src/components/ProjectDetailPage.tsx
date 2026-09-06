import { useParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, Video, FileText, Clock, CheckCircle, XCircle, Loader } from 'lucide-react';
import { api } from '../api/client';

function statusColor(status: string) {
  switch (status) {
    case 'ready':
      return 'text-green-400';
    case 'pending':
    case 'processing':
      return 'text-yellow-400';
    case 'failed':
      return 'text-red-400';
    default:
      return 'text-slate-400';
  }
}

function statusIcon(status: string) {
  switch (status) {
    case 'ready':
      return <CheckCircle className="w-4 h-4 text-green-400" />;
    case 'pending':
    case 'processing':
      return <Loader className="w-4 h-4 text-yellow-400 animate-spin" />;
    case 'failed':
      return <XCircle className="w-4 h-4 text-red-400" />;
    default:
      return <Clock className="w-4 h-4 text-slate-400" />;
  }
}

export function ProjectDetailPage() {
  const { projectId } = useParams<{ projectId: string }>();

  const { data: project, isLoading: loadingProject } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: !!projectId,
  });

  const { data: sessions, isLoading: loadingSessions } = useQuery({
    queryKey: ['sessions', projectId],
    queryFn: () => api.listSessions(projectId!),
    enabled: !!projectId,
  });

  if (loadingProject) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="animate-spin w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full" />
      </div>
    );
  }

  if (!project) {
    return (
      <div className="text-center py-20 text-red-400">Project not found</div>
    );
  }

  return (
    <div>
      <Link
        to="/"
        className="inline-flex items-center gap-2 text-slate-400 hover:text-slate-200 mb-6"
      >
        <ArrowLeft className="w-4 h-4" />
        Back to projects
      </Link>

      <div className="mb-8">
        <h2 className="text-2xl font-bold">{project.name}</h2>
        {project.description && (
          <p className="text-slate-500 mt-1">{project.description}</p>
        )}
      </div>

      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold">Sessions</h3>
        <Link
          to={`/ingest?project=${projectId}`}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
        >
          <Video className="w-4 h-4" />
          Ingest Media
        </Link>
      </div>

      {loadingSessions ? (
        <div className="text-center py-10 text-slate-500">Loading sessions...</div>
      ) : sessions && sessions.length > 0 ? (
        <div className="space-y-3">
          {sessions.map((session) => (
            <Link
              key={session.id}
              to={`/projects/${projectId}/sessions/${session.id}`}
              className="flex items-center justify-between p-4 bg-slate-800 border border-slate-700 rounded-lg hover:border-slate-600 transition-colors"
            >
              <div className="flex items-center gap-4">
                {statusIcon(session.status)}
                <div>
                  <div className="font-medium">{session.label}</div>
                  <div className="text-sm text-slate-500">
                    {session.notes_filename ? (
                      <span className="flex items-center gap-1">
                        <FileText className="w-3 h-3" />
                        {session.notes_filename}
                      </span>
                    ) : (
                      <span className="text-slate-600">No notes</span>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-4 text-sm text-slate-500">
                <span className={statusColor(session.status)}>{session.status}</span>
                <span>{new Date(session.created_at).toLocaleDateString()}</span>
              </div>
            </Link>
          ))}
        </div>
      ) : (
        <div className="text-center py-10 text-slate-500">
          No sessions yet. Ingest your first media to get started.
        </div>
      )}
    </div>
  );
}
