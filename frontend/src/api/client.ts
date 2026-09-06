const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

export interface Project {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
}

export interface SessionSummary {
  id: string;
  label: string;
  status: string;
  notes_filename: string | null;
  created_at: string;
}

export interface MediaSession {
  id: string;
  project_id: string;
  media_id: string;
  label: string;
  status: string;
  notes_filename: string | null;
  raw_notes: string | null;
  idempotency_key: string | null;
  created_at: string;
}

export interface ProcessingJob {
  id: string;
  project_id: string;
  session_id: string;
  media_id: string;
  job_type: string;
  status: string;
  stage: string;
  progress: number;
  error_message: string | null;
  attempts: number;
  max_attempts: number;
  created_at: string;
  updated_at: string;
}

export interface SessionIngestOut {
  session: MediaSession;
  job: ProcessingJob;
}

export interface SearchResult {
  session_id: string;
  segment_index: number;
  start_ms: number;
  end_ms: number;
  search_text: string;
  similarity: number;
  evidence: Array<{
    type: string;
    ref: string;
  }>;
}

async function fetchJSON<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API Error ${res.status}: ${text}`);
  }
  return res.json();
}

export const api = {
  // Projects
  listProjects: (offset = 0, limit = 50) =>
    fetchJSON<Project[]>(`/projects?offset=${offset}&limit=${limit}`),

  getProject: (id: string) =>
    fetchJSON<Project>(`/projects/${id}`),

  createProject: (data: { name: string; description?: string }) =>
    fetchJSON<Project>('/projects', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Sessions
  listSessions: (projectId: string) =>
    fetchJSON<SessionSummary[]>(`/projects/${projectId}/sessions`),

  ingestSession: async (
    projectId: string,
    data: { label: string; video: File; notes?: File; idempotencyKey?: string }
  ): Promise<SessionIngestOut> => {
    const formData = new FormData();
    formData.append('label', data.label);
    formData.append('video', data.video);
    if (data.notes) formData.append('notes', data.notes);

    const res = await fetch(`${API_BASE}/projects/${projectId}/sessions`, {
      method: 'POST',
      headers: data.idempotencyKey
        ? { 'Idempotency-Key': data.idempotencyKey }
        : undefined,
      body: formData,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Ingest failed: ${text}`);
    }
    return res.json();
  },

  importNotes: async (projectId: string, sessionId: string, notes: File): Promise<SessionSummary> => {
    const formData = new FormData();
    formData.append('notes', notes);

    const res = await fetch(`${API_BASE}/projects/${projectId}/sessions/${sessionId}/notes`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Import notes failed: ${text}`);
    }
    return res.json();
  },

  // Jobs
  getJob: (projectId: string, sessionId: string) =>
    fetchJSON<ProcessingJob>(`/projects/${projectId}/sessions/${sessionId}/job`),

  // Search
  searchSessions: (projectId: string, query: string, limit = 20) =>
    fetchJSON<SearchResult[]>(`/projects/${projectId}/search?q=${encodeURIComponent(query)}&limit=${limit}`),

  searchGlobal: (query: string, limit = 20) =>
    fetchJSON<SearchResult[]>(`/search?q=${encodeURIComponent(query)}&limit=${limit}`),
};
