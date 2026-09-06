import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, FileText, Loader, Upload } from 'lucide-react';
import { useState, useRef } from 'react';
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

export function SessionDetailPage() {
  const { projectId, sessionId } = useParams<{ projectId: string; sessionId: string }>();
  const queryClient = useQueryClient();
  const [importError, setImportError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data: session, isLoading: loadingSession } = useQuery({
    queryKey: ['session', sessionId],
    queryFn: () => api.getProject(projectId!).then(() => api.listSessions(projectId!)),
    enabled: !!projectId && !!sessionId,
  });

  const currentSession = session?.find((s) => s.id === sessionId);

  const { data: job, isLoading: loadingJob } = useQuery({
    queryKey: ['job', projectId, sessionId],
    queryFn: () => api.getJob(projectId!, sessionId!),
    enabled: !!projectId && !!sessionId,
  });

  const importMutation = useMutation({
    mutationFn: async (file: File) => {
      return api.importNotes(projectId!, sessionId!, file);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions', projectId] });
      setImportError(null);
    },
    onError: (err) => {
      setImportError(err instanceof Error ? err.message : 'Import failed');
    },
  });

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      importMutation.mutate(file);
    }
  };

  if (loadingSession || loadingJob) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="animate-spin w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full" />
      </div>
    );
  }

  if (!currentSession) {
    return (
      <div className="text-center py-20 text-red-400">Session not found</div>
    );
  }

  return (
    <div>
      <Link
        to={`/projects/${projectId}`}
        className="inline-flex items-center gap-2 text-slate-400 hover:text-slate-200 mb-6"
      >
        <ArrowLeft className="w-4 h-4" />
        Back to project
      </Link>

      <div className="grid gap-6 md:grid-cols-2">
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-6">
          <h2 className="text-xl font-bold mb-4">{currentSession.label}</h2>

          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-slate-500">Status</span>
              <span className={`font-medium ${statusColor(currentSession.status)}`}>
                {currentSession.status}
              </span>
            </div>

            <div className="flex items-center justify-between">
              <span className="text-slate-500">Created</span>
              <span className="text-slate-400">
                {new Date(currentSession.created_at).toLocaleString()}
              </span>
            </div>

            <div className="flex items-center justify-between">
              <span className="text-slate-500">Notes</span>
              {currentSession.notes_filename ? (
                <span className="flex items-center gap-1 text-slate-400">
                  <FileText className="w-4 h-4" />
                  {currentSession.notes_filename}
                </span>
              ) : (
                <button
                  onClick={() => fileInputRef.current?.click()}
                  className="flex items-center gap-1 text-blue-400 hover:text-blue-300"
                >
                  <Upload className="w-4 h-4" />
                  Import notes
                </button>
              )}
            </div>

            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.md"
              onChange={handleFileSelect}
              className="hidden"
            />

            {importError && (
              <div className="p-2 bg-red-500/20 border border-red-500/50 rounded text-red-400 text-sm">
                {importError}
              </div>
            )}

            {importMutation.isPending && (
              <div className="flex items-center gap-2 text-yellow-400">
                <Loader className="w-4 h-4 animate-spin" />
                Importing notes...
              </div>
            )}
          </div>
        </div>

        {job && (
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-6">
            <h3 className="text-lg font-semibold mb-4">Processing Job</h3>

            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-slate-500">Type</span>
                <span className="text-slate-400">{job.job_type}</span>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-slate-500">Stage</span>
                <span className="text-slate-400">{job.stage}</span>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-slate-500">Progress</span>
                <div className="flex items-center gap-2">
                  <div className="w-24 h-2 bg-slate-700 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-blue-500"
                      style={{ width: `${job.progress * 100}%` }}
                    />
                  </div>
                  <span className="text-slate-400 text-sm">
                    {Math.round(job.progress * 100)}%
                  </span>
                </div>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-slate-500">Attempts</span>
                <span className="text-slate-400">
                  {job.attempts} / {job.max_attempts}
                </span>
              </div>

              {job.error_message && (
                <div className="p-2 bg-red-500/20 border border-red-500/50 rounded text-red-400 text-sm">
                  {job.error_message}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

    </div>
  );
}
