"""Deterministic parser for explicitly ranged human-authored timeline lines."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.domain import TimelineNoteObservation

_TIMESTAMP = r"(?:\d{1,2}:)?\d{1,2}:\d{2}"
_RANGE_LINE = re.compile(
    rf"^\s*(?P<start>{_TIMESTAMP})\s*[\-\u2013\u2014]\s*"
    rf"(?P<end>{_TIMESTAMP})(?:(?:[ \t]*[\-\u2013\u2014:|][ \t]*)|\t+)"
    rf"(?P<description>\S.*)\s*$"
)
_TEMPORAL_PREFIX = re.compile(rf"^\s*{_TIMESTAMP}\s*(?:[\-\u2013\u2014]|$)")


@dataclass(frozen=True, slots=True)
class TimelineTextParseError:
    line_number: int
    line: str
    reason: str


@dataclass(frozen=True, slots=True)
class TimelineTextParseResult:
    observations: tuple[TimelineNoteObservation, ...]
    invalid_lines: tuple[TimelineTextParseError, ...]
    ignored_line_count: int


def parse_timestamp(value: str) -> float:
    parts = value.split(":")
    if len(parts) == 2:
        hours_text = "0"
        minutes, seconds = parts
    elif len(parts) == 3:
        hours_text, minutes, seconds = parts
    else:
        raise ValueError("timestamp must use MM:SS or HH:MM:SS")
    numeric = [int(hours_text), int(minutes), int(seconds)]
    if numeric[0] < 0 or numeric[1] < 0 or numeric[2] < 0:
        raise ValueError("timestamp values must be non-negative")
    if numeric[2] >= 60 or (len(parts) == 3 and numeric[1] >= 60):
        raise ValueError("timestamp minutes/seconds are out of range")
    return float(numeric[0] * 3600 + numeric[1] * 60 + numeric[2])


def parse_timeline_text(
    content: str, *, media_duration_seconds: float | None = None
) -> TimelineTextParseResult:
    observations: list[TimelineNoteObservation] = []
    invalid: list[TimelineTextParseError] = []
    ignored = 0
    tolerance = 0.001
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            ignored += 1
            continue
        match = _RANGE_LINE.fullmatch(line)
        if match is None:
            if _TEMPORAL_PREFIX.match(line):
                invalid.append(
                    TimelineTextParseError(line_number, raw_line, "invalid temporal range")
                )
            else:
                ignored += 1
            continue
        try:
            start = parse_timestamp(match.group("start"))
            end = parse_timestamp(match.group("end"))
        except ValueError as exc:
            invalid.append(TimelineTextParseError(line_number, raw_line, str(exc)))
            continue
        description = match.group("description").strip()
        if end <= start:
            invalid.append(
                TimelineTextParseError(line_number, raw_line, "end must be greater than start")
            )
            continue
        if media_duration_seconds is not None and end > media_duration_seconds + tolerance:
            invalid.append(
                TimelineTextParseError(line_number, raw_line, "end exceeds media duration")
            )
            continue
        observations.append(
            TimelineNoteObservation(
                start_seconds=start,
                end_seconds=end,
                description=description,
                tags=[],
            )
        )
    return TimelineTextParseResult(tuple(observations), tuple(invalid), ignored)
