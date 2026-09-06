import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, FileText, Loader, Upload, Clock } from 'lucide-react';
import { useState, useRef } from 'react';
import { api } from '../api/client';
import { TimelineViewer } from './TimelineViewer';

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

  const { data: sessions, isLoading: loadingSession } = useQuery({
    queryKey: ['sessions', projectId],
    queryFn: () => api.listSessions(projectId!),
    enabled: !!projectId,
  });

  const { data: fullSession, isLoading: loadingFullSession } = useQuery({
    queryKey: ['fullSession', sessionId],
    queryFn: () => api.getSession(sessionId!),
    enabled: !!sessionId,
  });

  const currentSession = sessions?.find((s) => s.id === sessionId);

  const { data: job, isLoading: loadingJob } = useQuery({
    queryKey: ['job', projectId, sessionId],
    queryFn: () => api.getJob(projectId!, sessionId!),
    enabled: !!projectId && !!sessionId,
  });

  const { data: temporalIndexes } = useQuery({
    queryKey: ['temporal-indexes', sessionId],
    queryFn: () => api.listTemporalIndexes(sessionId!),
    enabled: !!sessionId && currentSession?.status === 'ready',
  });

  const completedTemporalIndex = temporalIndexes?.find(ti => ti.status === 'completed');

  const { data: timeline, isLoading: loadingTimeline } = useQuery({
    queryKey: ['semantic-timeline', sessionId, completedTemporalIndex?.id],
    queryFn: () => api.getSemanticTimeline(sessionId!, {
      temporal_index_id: completedTemporalIndex!.id,
    }),
    enabled: !!sessionId && !!completedTemporalIndex,
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

  if (loadingSession || loadingFullSession || loadingJob) {
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

      <div className="mt-6">
        <h2 className="text-lg font-semibold text-slate-200 mb-4">Notes</h2>
        {fullSession && fullSession.notes && fullSession.notes.length > 0 ? (
          <div className="space-y-3">
            {fullSession.notes.map((note) => (
              <div
                key={note.id}
                className="p-3 bg-slate-800 rounded-lg border border-slate-700"
              >
                <div className="text-xs text-slate-500 mb-1">Note #{note.order}</div>
                <p className="text-sm text-slate-300">{note.raw}</p>
                {note.description && (
                  <p className="text-xs text-slate-400 mt-1">{note.description}</p>
                )}
                {note.tags && note.tags.length > 0 && (
                  <div className="flex gap-1 mt-2 flex-wrap">
                    {note.tags.map((tag: string, i: number) => (
                      <span key={i} className="px-2 py-0.5 bg-slate-700 rounded text-xs text-slate-400">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : fullSession && fullSession.raw_notes ? (
          <div className="p-4 bg-slate-800 rounded-lg border border-slate-700">
            <pre className="text-sm text-slate-300 whitespace-pre-wrap font-mono">
              {fullSession.raw_notes}
            </pre>
          </div>
        ) : (
          <div className="p-4 bg-slate-800/50 rounded-lg border border-dashed border-slate-700">
            <p className="text-sm text-slate-500">No notes imported for this session.</p>
          </div>
        )}
      </div>

      {fullSession?.status === 'ready' && (
        <div className="mt-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-slate-200 flex items-center gap-2">
              <Clock className="w-5 h-5" />
              Semantic Timeline
            </h2>
            {temporalIndexes && temporalIndexes.length > 0 && (
              <span className="text-xs text-slate-500">
                {temporalIndexes.filter(ti => ti.status === 'completed').length} completed indexes
              </span>
            )}
          </div>

          {loadingTimeline ? (
            <div className="flex items-center justify-center py-10">
              <Loader className="w-6 h-6 animate-spin text-slate-500" />
            </div>
          ) : temporalIndexes && temporalIndexes.length === 0 ? (
            <div className="p-4 bg-slate-800/50 rounded-lg border border-dashed border-slate-700">
              <p className="text-sm text-slate-500">No temporal indexes available. Create one from the processed transcription and visual index.</p>
            </div>
          ) : !completedTemporalIndex ? (
            <div className="p-4 bg-slate-800/50 rounded-lg border border-yellow-500/50">
              <p className="text-sm text-slate-400">Temporal index processing in progress...</p>
            </div>
          ) : timeline ? (
            <TimelineViewer timeline={timeline} />
          ) : null}
        </div>
      )}
    </div>
  );
}
