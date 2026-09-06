import { useState, useRef } from 'react';
import { useNavigate, useSearchParams, Link } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { Upload, Video, FileText, CheckCircle, Loader, ArrowLeft } from 'lucide-react';
import { api } from '../api/client';

function FileDropZone({
  accept,
  label,
  file,
  onFileSelect,
  optional = false,
}: {
  accept: string;
  label: string;
  file: File | null;
  onFileSelect: (file: File | null) => void;
  optional?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped) onFileSelect(dropped);
  };

  return (
    <div
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      className={`relative border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
        dragOver
          ? 'border-blue-500 bg-blue-500/10'
          : file
          ? 'border-green-500/50 bg-green-500/10'
          : 'border-slate-700 hover:border-slate-600'
      }`}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={(e) => onFileSelect(e.target.files?.[0] || null)}
        className="hidden"
      />
      {file ? (
        <div className="flex items-center justify-center gap-2 text-green-400">
          <CheckCircle className="w-5 h-5" />
          <span>{file.name}</span>
        </div>
      ) : (
        <div className="text-slate-500">
          <Upload className="w-8 h-8 mx-auto mb-2" />
          <p>
            {label}
            {optional && <span className="text-slate-600"> (optional)</span>}
          </p>
          <p className="text-xs text-slate-600 mt-1">Click or drag to upload</p>
        </div>
      )}
    </div>
  );
}

export function IngestPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const preselectedProject = searchParams.get('project');

  const [projectId, setProjectId] = useState(preselectedProject || '');
  const [label, setLabel] = useState('');
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [notesFile, setNotesFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.listProjects(),
  });

  const mutation = useMutation({
    mutationFn: async () => {
      if (!projectId) throw new Error('Select a project');
      if (!videoFile) throw new Error('Select a video file');
      if (!label.trim()) throw new Error('Enter a label');

      return api.ingestSession(projectId, {
        label: label.trim(),
        video: videoFile,
        notes: notesFile || undefined,
      });
    },
    onSuccess: (result) => {
      navigate(`/projects/${projectId}/sessions/${result.session.id}`);
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : 'Ingest failed');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    mutation.mutate();
  };

  return (
    <div className="max-w-2xl mx-auto">
      <Link
        to="/"
        className="inline-flex items-center gap-2 text-slate-400 hover:text-slate-200 mb-6"
      >
        <ArrowLeft className="w-4 h-4" />
        Back to projects
      </Link>

      <h2 className="text-2xl font-bold mb-6">Ingest Media</h2>

      <form onSubmit={handleSubmit} className="space-y-6">
        <div>
          <label className="block text-sm text-slate-400 mb-2">Project</label>
          <select
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">Select a project...</option>
            {projects?.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm text-slate-400 mb-2">Label</label>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Session label..."
            className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        <div>
          <label className="block text-sm text-slate-400 mb-2">
            <Video className="w-4 h-4 inline mr-1" />
            Video File
          </label>
          <FileDropZone
            accept="video/*"
            label="Video file"
            file={videoFile}
            onFileSelect={setVideoFile}
          />
        </div>

        <div>
          <label className="block text-sm text-slate-400 mb-2">
            <FileText className="w-4 h-4 inline mr-1" />
            Notes File
          </label>
          <FileDropZone
            accept=".txt,.md"
            label="Notes file"
            file={notesFile}
            onFileSelect={setNotesFile}
            optional
          />
        </div>

        {error && (
          <div className="p-3 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400 text-sm">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={mutation.isPending}
          className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          {mutation.isPending ? (
            <>
              <Loader className="w-4 h-4 animate-spin" />
              Ingesting...
            </>
          ) : (
            <>
              <Upload className="w-4 h-4" />
              Start Ingest
            </>
          )}
        </button>
      </form>
    </div>
  );
}
