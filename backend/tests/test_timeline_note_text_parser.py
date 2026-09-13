import pytest

from app.services.timeline_note_text_parser import parse_timeline_text


def test_parses_supported_timestamp_and_separator_variants() -> None:
    result = parse_timeline_text(
        "\n".join(
            [
                "00:01–00:14 — descrição um",
                " 00:14-00:22 - descrição dois ",
                "01:02:03—01:02:09: descrição três",
                "02:00–02:10\tdescrição tabular",
            ]
        )
    )
    assert [
        (item.start_seconds, item.end_seconds, item.description) for item in result.observations
    ] == [
        (1.0, 14.0, "descrição um"),
        (14.0, 22.0, "descrição dois"),
        (3723.0, 3729.0, "descrição três"),
        (120.0, 130.0, "descrição tabular"),
    ]
    assert result.invalid_lines == ()


def test_ignores_headings_tables_and_blank_lines_without_inventing_ranges() -> None:
    result = parse_timeline_text(
        "Timeline detalhada\n\nEventos particularmente bons para indexação\nIntervalo Conteúdo"
    )
    assert result.observations == ()
    assert result.invalid_lines == ()
    assert result.ignored_line_count == 4


def test_reports_ambiguous_and_invalid_temporal_lines() -> None:
    result = parse_timeline_text(
        "00:10 — evento pontual\n00:20–00:10 — invertido\n00:70–00:80 — inválido"
    )
    assert result.observations == ()
    assert [item.line_number for item in result.invalid_lines] == [1, 2, 3]


def test_rejects_range_past_media_duration() -> None:
    result = parse_timeline_text(
        "00:59–01:01 — passou",
        media_duration_seconds=60.0,
    )
    assert result.observations == ()
    assert result.invalid_lines[0].reason == "end exceeds media duration"


@pytest.mark.external
def test_real_timeline_produces_many_structured_observations() -> None:
    path = "/media/bernardo/HDD500GB1/gameFootage/gta_iv/000.txt"
    with open(path, encoding="utf-8") as handle:
        result = parse_timeline_text(handle.read(), media_duration_seconds=1200.0)
    assert len(result.observations) >= 100
