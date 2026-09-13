"""Deterministic adapter for explicitly ranged operator-authored timelines."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.domain import TimelineNoteObservation

OPERATOR_TIMELINE_ADAPTER_VERSION = "operator_timeline_adapter_v1"

_TIMESTAMP = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d{1,3})?"
_RANGE_SEPARATOR = r"(?:--|[\-\u2013\u2014])"
_BRACKET_RANGE = re.compile(
    rf"^\[\s*(?P<start>{_TIMESTAMP})\s*{_RANGE_SEPARATOR}\s*"
    rf"(?P<end>{_TIMESTAMP})\s*\]\s*(?P<description>.*)$"
)
_BOLD_RANGE = re.compile(
    rf"^\*\*\s*(?P<start>{_TIMESTAMP})\s*{_RANGE_SEPARATOR}\s*"
    rf"(?P<end>{_TIMESTAMP})\s*\*\*\s*(?P<description>.*)$"
)
_TABLE_RANGE = re.compile(
    rf"^\|\s*(?:\*\*)?\s*(?P<start>{_TIMESTAMP})\s*{_RANGE_SEPARATOR}\s*"
    rf"(?P<end>{_TIMESTAMP})\s*(?:\*\*)?\s*\|\s*(?P<description>.*?)\s*\|?\s*$"
)
_PLAIN_RANGE = re.compile(
    rf"^(?P<start>{_TIMESTAMP})\s*{_RANGE_SEPARATOR}\s*(?P<end>{_TIMESTAMP})"
    rf"\s*(?:[\u2013\u2014|:]|\s+-\s+)\s*(?P<description>.*)$"
)
_TEMPORAL_PREFIX = re.compile(rf"^\s*(?:\||\[|\*\*)?\s*{_TIMESTAMP}")
_TABLE_SEPARATOR = re.compile(r"^\|?[\s:|-]+\|[\s:|-]+\|?$")
_HEADING = re.compile(r"^#{1,6}\s+\S.*$")
_METADATA_BULLET = re.compile(r"^\s*[-*]\s+(?:\*\*[^*]+:\*\*|[^:]{1,80}:)\s*.*$")


@dataclass(frozen=True, slots=True)
class OperatorTimelineIssue:
    line_number: int
    line: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class OperatorTimelineParseResult:
    observations: tuple[TimelineNoteObservation, ...]
    issues: tuple[OperatorTimelineIssue, ...]
    ignored_line_count: int
    continuation_line_count: int


def parse_operator_timestamp(value: str) -> float:
    parts = value.split(":")
    if len(parts) == 2:
        hours_text = "0"
        minutes_text, seconds_text = parts
    elif len(parts) == 3:
        hours_text, minutes_text, seconds_text = parts
    else:
        raise ValueError("timestamp must use MM:SS or HH:MM:SS")
    try:
        hours = int(hours_text)
        minutes = int(minutes_text)
        seconds = float(seconds_text)
    except ValueError as exc:
        raise ValueError("timestamp contains a non-numeric component") from exc
    if hours < 0 or minutes < 0 or seconds < 0:
        raise ValueError("timestamp values must be non-negative")
    if seconds >= 60 or (len(parts) == 3 and minutes >= 60):
        raise ValueError("timestamp minutes/seconds are out of range")
    return hours * 3600.0 + minutes * 60.0 + seconds


def _structural_line(line: str) -> bool:
    if _HEADING.fullmatch(line) or _TABLE_SEPARATOR.fullmatch(line):
        return True
    if _METADATA_BULLET.fullmatch(line):
        return True
    lowered = line.casefold().replace("*", "").strip(" |:")
    if line.startswith("|") and any(
        marker in lowered for marker in ("timestamp", "timecode", "descrição", "descricao")
    ):
        return True
    return lowered in {
        "timestamp",
        "timestamps",
        "timecode",
        "intervalo",
        "descrição",
        "descricao",
        "timestamp descrição",
        "timestamp descricao",
    }


def _match_range(line: str) -> re.Match[str] | None:
    for pattern in (_TABLE_RANGE, _BRACKET_RANGE, _BOLD_RANGE, _PLAIN_RANGE):
        match = pattern.fullmatch(line)
        if match is not None:
            return match
    return None


def parse_operator_timeline(
    content: str,
    *,
    media_duration_seconds: float | None = None,
    duration_tolerance_seconds: float = 0.001,
) -> OperatorTimelineParseResult:
    """Parse known operator formats without guessing missing temporal ranges."""
    if duration_tolerance_seconds < 0:
        raise ValueError("duration tolerance must be non-negative")
    observations: list[TimelineNoteObservation] = []
    observation_lines: list[int] = []
    issues: list[OperatorTimelineIssue] = []
    ignored = 0
    continuations = 0
    pending_range: tuple[float, float, int, str] | None = None
    continuation_allowed = False

    def report_unresolved_pending() -> None:
        nonlocal pending_range
        if pending_range is None:
            return
        _, _, pending_line, pending_raw = pending_range
        issues.append(
            OperatorTimelineIssue(
                pending_line,
                pending_raw,
                "empty_description",
                "temporal observation has no following description line",
            )
        )
        pending_range = None

    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            ignored += 1
            continue
        if _structural_line(line):
            report_unresolved_pending()
            if _HEADING.fullmatch(line) and observations:
                continuation_allowed = False
            ignored += 1
            continue
        match = _match_range(line)
        if match is None:
            if _TEMPORAL_PREFIX.match(line):
                report_unresolved_pending()
                issues.append(
                    OperatorTimelineIssue(
                        line_number,
                        raw_line,
                        "ambiguous_temporal_line",
                        "line looks temporal but has no supported explicit range",
                    )
                )
            elif pending_range is not None:
                start, end, pending_line, _ = pending_range
                observations.append(
                    TimelineNoteObservation(
                        start_seconds=start,
                        end_seconds=end,
                        description=line,
                        tags=[],
                    )
                )
                observation_lines.append(pending_line)
                pending_range = None
                continuations += 1
                continuation_allowed = True
            elif observations and continuation_allowed:
                previous = observations[-1]
                joined = f"{previous.description.rstrip()} {line}".strip()
                observations[-1] = previous.model_copy(update={"description": joined})
                continuations += 1
            elif observations:
                ignored += 1
            else:
                issues.append(
                    OperatorTimelineIssue(
                        line_number,
                        raw_line,
                        "unattached_text",
                        "non-structural text appears before the first temporal observation",
                    )
                )
            continue

        report_unresolved_pending()
        try:
            start = parse_operator_timestamp(match.group("start"))
            end = parse_operator_timestamp(match.group("end"))
        except ValueError as exc:
            issues.append(
                OperatorTimelineIssue(line_number, raw_line, "invalid_timestamp", str(exc))
            )
            continue
        if end <= start:
            issues.append(
                OperatorTimelineIssue(
                    line_number,
                    raw_line,
                    "invalid_range",
                    "end must be greater than start",
                )
            )
            continue
        if start < 0:
            issues.append(
                OperatorTimelineIssue(
                    line_number,
                    raw_line,
                    "invalid_range",
                    "start must be non-negative",
                )
            )
            continue
        if (
            media_duration_seconds is not None
            and end > media_duration_seconds + duration_tolerance_seconds
        ):
            issues.append(
                OperatorTimelineIssue(
                    line_number,
                    raw_line,
                    "range_exceeds_media",
                    f"end {end:.3f}s exceeds media duration {media_duration_seconds:.3f}s",
                )
            )
            continue
        description = match.group("description").strip().strip("|").strip()
        if not description:
            pending_range = (start, end, line_number, raw_line)
            continuation_allowed = False
            continue
        observations.append(
            TimelineNoteObservation(
                start_seconds=start,
                end_seconds=end,
                description=description,
                tags=[],
            )
        )
        observation_lines.append(line_number)
        continuation_allowed = True

    report_unresolved_pending()

    seen_ranges: dict[tuple[float, float], int] = {}
    previous_start: float | None = None
    for observation, line_number in zip(observations, observation_lines, strict=True):
        temporal_range = (observation.start_seconds, observation.end_seconds)
        if temporal_range in seen_ranges:
            issues.append(
                OperatorTimelineIssue(
                    line_number,
                    "",
                    "duplicate_range",
                    f"duplicates temporal range from line {seen_ranges[temporal_range]}",
                )
            )
        else:
            seen_ranges[temporal_range] = line_number
        if previous_start is not None and observation.start_seconds < previous_start:
            issues.append(
                OperatorTimelineIssue(
                    line_number,
                    "",
                    "out_of_order",
                    "observation starts before the preceding observation",
                )
            )
        previous_start = observation.start_seconds

    return OperatorTimelineParseResult(tuple(observations), tuple(issues), ignored, continuations)
