import pytest

from app.services.operator_timeline_adapter import (
    parse_operator_timeline,
    parse_operator_timestamp,
)


def test_parses_markdown_table_brackets_and_simple_ranges() -> None:
    content = """
# Timeline
| Timestamp | Descrição |
| --- | --- |
| **00:00–00:05** | Rockstar logo and black screen. |

[00:00:05–00:00:08.250] A character enters the street.
00:08.250–00:12 — A vehicle crosses the frame.
"""
    result = parse_operator_timeline(content, media_duration_seconds=12)
    assert not result.issues
    assert [(item.start_seconds, item.end_seconds) for item in result.observations] == [
        (0, 5),
        (5, 8.25),
        (8.25, 12),
    ]
    assert result.observations[0].description == "Rockstar logo and black screen."


def test_parses_fixed_column_and_joins_wrapped_description() -> None:
    content = """
**02:09:08--02:09:20**   A protagonist leaves the vehicle and crosses the road.
The description continues on the following physical line.
**02:09:20--02:09:25**   The camera cuts to another explicit event.
"""
    result = parse_operator_timeline(content)
    assert not result.issues
    assert result.continuation_line_count == 1
    assert result.observations[0].description == (
        "A protagonist leaves the vehicle and crosses the road. "
        "The description continues on the following physical line."
    )


@pytest.mark.parametrize("value", ["00:60", "01:60:00", "abc", "1:2:3:4"])
def test_rejects_invalid_timestamp(value: str) -> None:
    with pytest.raises(ValueError):
        parse_operator_timestamp(value)


def test_reports_invalid_duration_duplicate_ranges_and_ambiguous_lines() -> None:
    result = parse_operator_timeline(
        """
[00:00–00:05] first
[00:00–00:05] duplicate
[00:05–00:05] zero
00:06 maybe temporal
[00:09–00:11] past physical duration
""",
        media_duration_seconds=10,
    )
    assert {item.code for item in result.issues} == {
        "duplicate_range",
        "invalid_range",
        "ambiguous_temporal_line",
        "range_exceeds_media",
    }


def test_reports_out_of_order_without_reordering() -> None:
    result = parse_operator_timeline("[00:10–00:12] later\n[00:02–00:04] earlier")
    assert [item.start_seconds for item in result.observations] == [10, 2]
    assert [item.code for item in result.issues] == ["out_of_order"]
