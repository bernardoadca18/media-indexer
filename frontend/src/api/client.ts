const API_BASE = import.meta.env?.VITE_API_BASE || "http://localhost:8000";

export interface Project {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
}

export interface Note {
  id: string;
  order: number;
  raw: string;
  start_seconds: number | null;
  end_seconds: number | null;
  description: string | null;
  tags: string[] | null;
}

export interface SessionSummary {
  id: string;
  label: string;
  status: string;
  notes_filename: string | null;
  created_at: string;
}

export interface Session {
  id: string;
  label: string;
  status: string;
  notes_filename: string | null;
  raw_notes: string | null;
  notes: Note[];
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
  media_id: string;
  start_seconds: number;
  end_seconds: number;
  duration_seconds: number;
  score: number;
  rank: number;
  session_label: string;
  media_filename: string;
  temporal_index_id: string;
  transcript_preview: string | null;
  matched_note_ids: string[];
  provenance: Record<string, unknown>;
}

export interface MediaSearchResponse {
  query: string;
  project_id: string;
  scope: string;
  results: SearchResult[];
  searched_sessions: number;
  skipped_sessions: number;
  session_summaries: {
    id: string;
    label: string;
    searched: boolean;
    result_count: number;
    skip_reason: string | null;
  }[];
}

// Semantic Timeline Types
export interface TimelineRange {
  start_seconds: number | null;
  end_seconds: number | null;
  semantics: string;
}

export interface TimelineTranscriptionProvenance {
  transcription_id: string;
}

export interface TimelineVisualProvenance {
  visual_index_id: string;
}

export interface TimelineAlignmentProvenance {
  alignment_run_id: string;
  retrieval_run_id: string;
  provider: string;
  model: string;
  strategy: string;
  prompt_version: string;
  configuration_fingerprint: string;
}

export interface TimelineProvenance {
  project_id: string;
  session_id: string;
  media_id: string;
  temporal_index_id: string;
  temporal_index_fingerprint: string;
  transcription: TimelineTranscriptionProvenance;
  visual_index: TimelineVisualProvenance;
  alignment: TimelineAlignmentProvenance | null;
}

export interface TimelineTranscriptRef {
  id: string;
  sequence: number;
  start_seconds: number;
  end_seconds: number;
}

export interface TimelineSceneRef {
  id: string;
  sequence: number;
  start_seconds: number;
  end_seconds: number;
}

export interface TimelineFrameRef {
  id: string;
  kind: string;
  timestamp_seconds: number;
  storage_uri: string;
  mime_type: string;
  width: number | null;
  height: number | null;
  metadata: Record<string, unknown> | null;
}

export interface TimelineEvidence {
  transcript_segments: TimelineTranscriptRef[];
  scenes: TimelineSceneRef[];
  frames: TimelineFrameRef[];
}

export interface TimelineItem {
  temporal_segment_id: string;
  sequence: number;
  start_seconds: number;
  end_seconds: number;
  duration_seconds: number;
  text: string;
  metadata: Record<string, unknown> | null;
  silent: boolean;
  evidence: TimelineEvidence;
  matched_note_ids: string[];
  selected_alignment_ids: string[];
}

export interface TimelineSelectedAlignment {
  candidate_alignment_id: string;
  retrieval_candidate_id: string;
  retrieval_rank: number;
  start_seconds: number;
  end_seconds: number;
  confidence: number;
  anchors: Array<Record<string, unknown>>;
  reasoning_summary: string | null;
  timestamp_resolution_seconds: number | null;
  temporal_segment_ids: string[];
}

export interface TimelineNote {
  id: string;
  order: number;
  raw: string;
  start_seconds: number | null;
  end_seconds: number | null;
  description: string | null;
  tags: string[] | null;
  alignment_status: string;
  alignment_subject_id: string | null;
  selected_alignment: TimelineSelectedAlignment | null;
}

export interface SemanticTimeline {
  provenance: TimelineProvenance;
  range: TimelineRange;
  items: TimelineItem[];
  notes: TimelineNote[];
  total: number;
  offset: number;
  limit: number;
}

// Temporal Index Types
export interface TemporalIndex {
  id: string;
  session_id: string;
  transcription_id: string;
  visual_index_id: string;
  status: string;
  configuration_fingerprint: string;
  created_at: string;
}

async function fetchJSON<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
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

  getProject: (id: string) => fetchJSON<Project>(`/projects/${id}`),

  createProject: (data: { name: string; description?: string }) =>
    fetchJSON<Project>("/projects", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // Sessions
  listSessions: (projectId: string) =>
    fetchJSON<SessionSummary[]>(`/projects/${projectId}/sessions`),

  ingestSession: async (
    projectId: string,
    data: { label: string; video: File; notes?: File; idempotencyKey?: string },
  ): Promise<SessionIngestOut> => {
    const formData = new FormData();
    formData.append("label", data.label);
    formData.append("video", data.video);
    if (data.notes) formData.append("notes", data.notes);

    const res = await fetch(`${API_BASE}/projects/${projectId}/sessions`, {
      method: "POST",
      headers: data.idempotencyKey
        ? { "Idempotency-Key": data.idempotencyKey }
        : undefined,
      body: formData,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Ingest failed: ${text}`);
    }
    return res.json();
  },

  importNotes: async (
    projectId: string,
    sessionId: string,
    notes: File,
  ): Promise<SessionSummary> => {
    const formData = new FormData();
    formData.append("notes", notes);

    const res = await fetch(
      `${API_BASE}/projects/${projectId}/sessions/${sessionId}/notes`,
      {
        method: "POST",
        body: formData,
      },
    );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Import notes failed: ${text}`);
    }
    return res.json();
  },

  // Jobs
  getJob: (projectId: string, sessionId: string) =>
    fetchJSON<ProcessingJob>(
      `/projects/${projectId}/sessions/${sessionId}/job`,
    ),

  // Search
  searchSessions: (projectId: string, query: string, limit = 20) =>
    fetchJSON<MediaSearchResponse>(
      `/media-search/projects/${encodeURIComponent(projectId)}`,
      {
        method: "POST",
        body: JSON.stringify({ query, max_results: limit }),
      },
    ),

  // Semantic Timeline
  getSemanticTimeline: (
    sessionId: string,
    params: {
      temporal_index_id: string;
      alignment_run_id?: string;
      start_seconds?: number;
      end_seconds?: number;
      offset?: number;
      limit?: number;
    },
  ) => {
    const searchParams = new URLSearchParams();
    searchParams.set("temporal_index_id", params.temporal_index_id);
    if (params.alignment_run_id)
      searchParams.set("alignment_run_id", params.alignment_run_id);
    if (params.start_seconds !== undefined)
      searchParams.set("start_seconds", String(params.start_seconds));
    if (params.end_seconds !== undefined)
      searchParams.set("end_seconds", String(params.end_seconds));
    if (params.offset !== undefined)
      searchParams.set("offset", String(params.offset));
    if (params.limit !== undefined)
      searchParams.set("limit", String(params.limit));
    return fetchJSON<SemanticTimeline>(
      `/sessions/${sessionId}/semantic-timeline?${searchParams}`,
    );
  },

  // Temporal Indexes
  listTemporalIndexes: (sessionId: string) =>
    fetchJSON<TemporalIndex[]>(`/sessions/${sessionId}/temporal-indexes`),

  // Session details
  getSession: (sessionId: string) =>
    fetchJSON<Session>(`/sessions/${sessionId}`),
};
