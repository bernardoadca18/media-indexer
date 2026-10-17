import { useState } from "react";
import {
  Clock,
  MessageSquare,
  ChevronDown,
  ChevronUp,
  Check,
  ChevronRight,
} from "lucide-react";
import type {
  SemanticTimeline,
  TimelineNote,
  TimelineItem,
} from "../api/client";

function formatSeconds(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 10);
  return `${mins}:${secs.toString().padStart(2, "0")}.${ms}`;
}

function TimelineNoteCard({
  note,
  isSelected,
  onClick,
}: {
  note: TimelineNote;
  isSelected: boolean;
  onClick: () => void;
}) {
  const hasTimestamp = note.start_seconds !== null;
  const statusColor =
    note.alignment_status === "matched"
      ? "border-green-500"
      : note.alignment_status === "not_aligned"
        ? "border-slate-600"
        : "border-yellow-500";

  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={isSelected}
      onClick={onClick}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onClick();
        }
      }}
      className={`p-3 rounded-lg border cursor-pointer transition-all ${
        isSelected
          ? "bg-slate-700 border-blue-500"
          : `bg-slate-800 ${statusColor} hover:bg-slate-750`
      }`}
    >
      <div className="flex items-center gap-2 text-xs text-slate-500 mb-1">
        <span>#{note.order}</span>
        {hasTimestamp && (
          <>
            <Clock className="w-3 h-3" />
            <span>{formatSeconds(note.start_seconds!)}</span>
            {note.end_seconds && (
              <span>- {formatSeconds(note.end_seconds)}</span>
            )}
          </>
        )}
      </div>
      <p className="text-sm text-slate-300 line-clamp-3">{note.raw}</p>
      {note.description && (
        <p className="text-xs text-slate-400 mt-1">{note.description}</p>
      )}
      {note.tags && note.tags.length > 0 && (
        <div className="flex gap-1 mt-2 flex-wrap">
          {note.tags.map((tag, i) => (
            <span
              key={i}
              className="px-2 py-0.5 bg-slate-700 rounded text-xs text-slate-400"
            >
              {tag}
            </span>
          ))}
        </div>
      )}
      <div className="text-xs text-slate-500 mt-1 capitalize">
        {note.alignment_status.replace("_", " ")}
      </div>
    </div>
  );
}

function TimelineSegmentCard({
  item,
  isHighlighted,
  onHighlight,
}: {
  item: TimelineItem;
  isHighlighted: boolean;
  onHighlight: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasNotes = item.matched_note_ids.length > 0;

  return (
    <div
      className={`p-3 rounded-lg border ${isHighlighted ? "bg-slate-800 border-blue-500" : item.silent ? "bg-slate-850 border-slate-700" : "bg-slate-800 border-slate-600"}`}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-3">
          <button
            aria-label={`${isHighlighted ? "Remover destaque do" : "Destacar"} trecho ${formatSeconds(item.start_seconds)} – ${formatSeconds(item.end_seconds)}`}
            aria-pressed={isHighlighted}
            onClick={onHighlight}
            className="p-1 rounded hover:bg-slate-700 text-slate-400 hover:text-slate-200"
          >
            {isHighlighted ? (
              <Check className="w-4 h-4" />
            ) : (
              <ChevronRight className="w-4 h-4" />
            )}
          </button>
          <div className="text-xs text-slate-500">
            <span className="font-mono">
              {formatSeconds(item.start_seconds)}
            </span>
            <span className="mx-1">-</span>
            <span className="font-mono">{formatSeconds(item.end_seconds)}</span>
          </div>
          <span className="text-xs text-slate-600">#{item.sequence}</span>
        </div>
        {hasNotes && (
          <div className="flex items-center gap-1 text-xs text-blue-400">
            <MessageSquare className="w-3 h-3" />
            <span>{item.matched_note_ids.length}</span>
          </div>
        )}
      </div>

      <p
        className={`text-sm ${item.silent ? "text-slate-600 italic" : "text-slate-300"}`}
      >
        {item.silent ? "[silêncio]" : item.text}
      </p>

      {(item.evidence.transcript_segments.length > 0 ||
        item.evidence.scenes.length > 0) && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-400 mt-2"
        >
          {expanded ? (
            <ChevronUp className="w-3 h-3" />
          ) : (
            <ChevronDown className="w-3 h-3" />
          )}
          Evidências ({item.evidence.transcript_segments.length} transcrições,{" "}
          {item.evidence.scenes.length} cenas)
        </button>
      )}

      {expanded && (
        <div className="mt-2 space-y-2 pl-2 border-l-2 border-slate-700">
          {item.evidence.transcript_segments.map((seg) => (
            <div key={seg.id} className="text-xs text-slate-500">
              Transcrição #{seg.sequence}: {formatSeconds(seg.start_seconds)} -{" "}
              {formatSeconds(seg.end_seconds)}
            </div>
          ))}
          {item.evidence.scenes.map((scene) => (
            <div key={scene.id} className="text-xs text-slate-500">
              Cena #{scene.sequence}: {formatSeconds(scene.start_seconds)} -{" "}
              {formatSeconds(scene.end_seconds)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface TimelineViewerProps {
  timeline: SemanticTimeline;
}

export function TimelineViewer({ timeline }: TimelineViewerProps) {
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [highlightedSegmentId, setHighlightedSegmentId] = useState<
    string | null
  >(null);
  const [viewMode, setViewMode] = useState<"timeline" | "notes">("timeline");

  const selectedNote = timeline.notes.find((n) => n.id === selectedNoteId);
  const segmentsWithDuration = timeline.items.reduce(
    (sum, item) => sum + item.duration_seconds,
    0,
  );

  const handleHighlight = (segmentId: string) => {
    setHighlightedSegmentId((prev) => (prev === segmentId ? null : segmentId));
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-slate-500">
          {timeline.items.length} trechos • {timeline.notes.length} notas •
          Duração total: {formatSeconds(segmentsWithDuration)}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setViewMode("timeline")}
            className={`px-3 py-1 text-sm rounded ${
              viewMode === "timeline"
                ? "bg-blue-600 text-white"
                : "bg-slate-700 text-slate-400"
            }`}
          >
            Timeline
          </button>
          <button
            onClick={() => setViewMode("notes")}
            className={`px-3 py-1 text-sm rounded ${
              viewMode === "notes"
                ? "bg-blue-600 text-white"
                : "bg-slate-700 text-slate-400"
            }`}
          >
            Notas
          </button>
        </div>
      </div>

      {viewMode === "timeline" ? (
        <div className="space-y-2">
          {timeline.items.map((item) => (
            <TimelineSegmentCard
              key={item.temporal_segment_id}
              item={item}
              isHighlighted={highlightedSegmentId === item.temporal_segment_id}
              onHighlight={() => handleHighlight(item.temporal_segment_id)}
            />
          ))}
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {timeline.notes.map((note) => (
            <TimelineNoteCard
              key={note.id}
              note={note}
              isSelected={selectedNoteId === note.id}
              onClick={() =>
                setSelectedNoteId((prev) => (prev === note.id ? null : note.id))
              }
            />
          ))}
        </div>
      )}

      {selectedNote && selectedNote.selected_alignment && (
        <div className="p-4 bg-slate-800 border border-green-500/50 rounded-lg">
          <h4 className="text-sm font-semibold text-green-400 mb-2">
            Correspondência selecionada
          </h4>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <span className="text-slate-500">Confiança:</span>
              <span className="ml-2 text-slate-300">
                {(selectedNote.selected_alignment.confidence * 100).toFixed(1)}%
              </span>
            </div>
            <div>
              <span className="text-slate-500">Intervalo:</span>
              <span className="ml-2 text-slate-300">
                {formatSeconds(selectedNote.selected_alignment.start_seconds)} -{" "}
                {formatSeconds(selectedNote.selected_alignment.end_seconds)}
              </span>
            </div>
            <div>
              <span className="text-slate-500">Posição:</span>
              <span className="ml-2 text-slate-300">
                #{selectedNote.selected_alignment.retrieval_rank}
              </span>
            </div>
            <div>
              <span className="text-slate-500">Trechos:</span>
              <span className="ml-2 text-slate-300">
                {selectedNote.selected_alignment.temporal_segment_ids.length}
              </span>
            </div>
          </div>
          {selectedNote.selected_alignment.reasoning_summary && (
            <p className="text-xs text-slate-400 mt-2">
              {selectedNote.selected_alignment.reasoning_summary}
            </p>
          )}
        </div>
      )}

      {timeline.items.length === 0 && (
        <div className="text-center py-10 text-slate-500">
          Nenhum trecho disponível na timeline
        </div>
      )}
    </div>
  );
}
