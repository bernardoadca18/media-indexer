import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Search, Clock, Loader } from "lucide-react";
import { api } from "../api/client";
import type { SearchResult } from "../api/client";

function formatTime(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}:${remainingSeconds.toString().padStart(2, "0")}`;
}

function SearchResultCard({
  result,
  projectId,
}: {
  result: SearchResult;
  projectId?: string;
}) {
  return (
    <div className="search-results p-4 bg-slate-800 border border-slate-700 rounded-lg">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-slate-500">
          {result.session_label || result.session_id}
        </span>
        <span className="text-sm text-blue-400">
          {(result.score * 100).toFixed(1)}% de similaridade
        </span>
      </div>

      <div className="flex items-center gap-2 text-sm text-slate-500 mb-2">
        <Clock className="w-3 h-3" />
        <span>
          {formatTime(Math.round(result.start_seconds * 1000))} -{" "}
          {formatTime(Math.round(result.end_seconds * 1000))}
        </span>
        <span className="text-slate-600">|</span>
        <span>Posição #{result.rank}</span>
      </div>

      <p className="text-slate-300 text-sm line-clamp-3">
        {result.transcript_preview ||
          "Este trecho não possui prévia de transcrição."}
      </p>

      {projectId && (
        <Link
          className="search-result-link"
          to={`/projects/${projectId}/sessions/${result.session_id}?start_ms=${Math.round(result.start_seconds * 1000)}&end_ms=${Math.round(result.end_seconds * 1000)}&temporal_index_id=${encodeURIComponent(result.temporal_index_id)}#timeline`}
        >
          Abrir sessão · {formatTime(Math.round(result.start_seconds * 1000))} →
        </Link>
      )}
      <p className="text-xs text-slate-500 mt-2">{result.media_filename}</p>
    </div>
  );
}

export function SearchPage() {
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [submittedProject, setSubmittedProject] = useState("");
  const [projectId, setProjectId] = useState("");

  const { data: projects, error: projectsError } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.listProjects(),
  });

  const {
    data: results,
    isFetching: isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ["search", submittedQuery, submittedProject],
    queryFn: () => {
      if (!submittedQuery.trim() || !submittedProject)
        throw new Error("Selecione um projeto para pesquisar.");
      return api.searchSessions(submittedProject, submittedQuery);
    },
    enabled: !!submittedQuery && !!submittedProject,
  });

  const searchResponse = results;
  const resultItems = searchResponse?.results;

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim() && projectId) {
      if (query.trim() === submittedQuery && projectId === submittedProject)
        void refetch();
      else {
        setSubmittedQuery(query.trim());
        setSubmittedProject(projectId);
      }
    }
  };

  return (
    <div className="max-w-4xl mx-auto">
      <h2 className="text-2xl font-bold mb-6">Buscar na biblioteca</h2>

      <p className="library-description">
        Descreva a cena ou o assunto que procura. A similaridade indica
        relevância e não confirma uma correspondência.
      </p>
      <form onSubmit={handleSearch} className="search-form space-y-4 mb-6">
        <div className="flex gap-2">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-500" />
            <input
              type="text"
              maxLength={2000}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Descreva o trecho que procura"
              placeholder="Ex.: uma conversa sobre a batalha final…"
              className="w-full pl-10 pr-4 py-3 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <button
            type="submit"
            disabled={isLoading || !query.trim() || !projectId}
            className="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {isLoading ? <Loader className="w-5 h-5 animate-spin" /> : "Buscar"}
          </button>
        </div>

        <div className="flex items-center gap-4">
          <label className="text-slate-400" htmlFor="search-project">
            Projeto da busca
          </label>
          <select
            id="search-project"
            aria-label="Projeto da busca"
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            className="px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">Selecione um projeto…</option>
            {projects?.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
      </form>

      {isLoading && (
        <p role="status" className="text-slate-400 mb-6">
          Buscando trechos na biblioteca…
        </p>
      )}
      {!submittedQuery && (
        <p className="text-slate-400 text-sm">
          Selecione um projeto e descreva o trecho. A API disponível pesquisa um
          projeto por vez.
        </p>
      )}
      {projectsError && (
        <p className="text-red-400" role="alert">
          Não foi possível carregar os projetos. Atualize a página para tentar
          novamente.
        </p>
      )}
      {error && (
        <div className="p-4 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400 mb-6">
          {error instanceof Error ? error.message : "Falha na busca"}
        </div>
      )}

      {resultItems && resultItems!.length > 0 && (
        <div className="space-y-4">
          <p className="text-slate-500 text-sm">
            {resultItems!.length} resultado(s) para “{submittedQuery}” ·{" "}
            {searchResponse?.searched_sessions} sessões consultadas ·{" "}
            {searchResponse?.skipped_sessions} não consultadas
          </p>
          {resultItems!.map((result) => (
            <SearchResultCard
              key={`${result.session_id}-${result.rank}`}
              result={result}
              projectId={submittedProject}
            />
          ))}
        </div>
      )}

      {results &&
        resultItems!.length === 0 &&
        submittedQuery &&
        !isLoading &&
        !error && (
          <div className="text-center py-10 text-slate-500">
            Nenhum resultado para “{submittedQuery}”. Tente descrever a cena com
            outras palavras.
          </div>
        )}
    </div>
  );
}
