import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search, Clock, Loader } from 'lucide-react';
import { api } from '../api/client';
import type { SearchResult } from '../api/client';

function formatTime(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
}

function SearchResultCard({ result }: { result: SearchResult }) {
  return (
    <div className="p-4 bg-slate-800 border border-slate-700 rounded-lg">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-slate-500">
          Session: {result.session_id.slice(0, 8)}...
        </span>
        <span className="text-sm text-blue-400">
          {(result.similarity * 100).toFixed(1)}% match
        </span>
      </div>

      <div className="flex items-center gap-2 text-sm text-slate-500 mb-2">
        <Clock className="w-3 h-3" />
        <span>
          {formatTime(result.start_ms)} - {formatTime(result.end_ms)}
        </span>
        <span className="text-slate-600">|</span>
        <span>Segment #{result.segment_index}</span>
      </div>

      <p className="text-slate-300 text-sm line-clamp-3">{result.search_text}</p>

      {result.evidence.length > 0 && (
        <div className="flex flex-wrap gap-2 mt-3">
          {result.evidence.map((ev, i) => (
            <span
              key={i}
              className="px-2 py-1 bg-slate-700 text-slate-400 text-xs rounded"
            >
              {ev.type}: {ev.ref.slice(0, 20)}...
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function SearchPage() {
  const [query, setQuery] = useState('');
  const [searchType, setSearchType] = useState<'global' | 'project'>('global');
  const [projectId, setProjectId] = useState('');

  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.listProjects(),
  });

  const {
    data: results,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['search', query, searchType, projectId],
    queryFn: () => {
      if (!query.trim()) return Promise.resolve([]);
      if (searchType === 'project' && projectId) {
        return api.searchSessions(projectId, query);
      }
      return api.searchGlobal(query);
    },
    enabled: false,
  });

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) {
      refetch();
    }
  };

  return (
    <div className="max-w-4xl mx-auto">
      <h2 className="text-2xl font-bold mb-6">Search Playground</h2>

      <form onSubmit={handleSearch} className="space-y-4 mb-6">
        <div className="flex gap-2">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-500" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search across all media..."
              className="w-full pl-10 pr-4 py-3 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <button
            type="submit"
            disabled={isLoading || !query.trim()}
            className="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {isLoading ? <Loader className="w-5 h-5 animate-spin" /> : 'Search'}
          </button>
        </div>

        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="searchType"
              checked={searchType === 'global'}
              onChange={() => setSearchType('global')}
              className="text-blue-600"
            />
            <span className="text-slate-400">Global</span>
          </label>

          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="searchType"
              checked={searchType === 'project'}
              onChange={() => setSearchType('project')}
              className="text-blue-600"
            />
            <span className="text-slate-400">Project</span>
          </label>

          {searchType === 'project' && (
            <select
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              className="px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">Select project...</option>
              {projects?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          )}
        </div>
      </form>

      {error && (
        <div className="p-4 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400 mb-6">
          {error instanceof Error ? error.message : 'Search failed'}
        </div>
      )}

      {results && results.length > 0 && (
        <div className="space-y-4">
          <p className="text-slate-500 text-sm">
            Found {results.length} result{results.length !== 1 ? 's' : ''}
          </p>
          {results.map((result) => (
            <SearchResultCard key={`${result.session_id}-${result.segment_index}`} result={result} />
          ))}
        </div>
      )}

      {results && results.length === 0 && query && !isLoading && (
        <div className="text-center py-10 text-slate-500">
          No results found for "{query}"
        </div>
      )}
    </div>
  );
}
