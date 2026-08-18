import pytest

from app.services.visual_tools import jpeg_qscale, parse_scdet_output


def test_scdet_output_becomes_contiguous_scenes() -> None:
    output = """
lavfi.scd.score=19.50
lavfi.scd.time=5.000000
lavfi.scd.score=22.25
lavfi.scd.time=12.000000
"""
    scenes = parse_scdet_output(output, 20.0)
    assert [(item.start_seconds, item.end_seconds) for item in scenes] == [
        (0.0, 5.0),
        (5.0, 12.0),
        (12.0, 20.0),
    ]
    assert scenes[1].detector_score == 19.5
    assert scenes[2].detector_score == 22.25


def test_scdet_parser_ignores_out_of_range_and_duplicate_cuts() -> None:
    output = """
lavfi.scd.score=9
lavfi.scd.time=-1
lavfi.scd.score=10
lavfi.scd.time=4
lavfi.scd.score=11
lavfi.scd.time=4
lavfi.scd.score=12
lavfi.scd.time=99
"""
    scenes = parse_scdet_output(output, 8.0)
    assert [(item.start_seconds, item.end_seconds) for item in scenes] == [
        (0.0, 4.0),
        (4.0, 8.0),
    ]


def test_jpeg_quality_is_mapped_to_ffmpeg_qscale() -> None:
    assert jpeg_qscale(100) == 2
    assert jpeg_qscale(1) == 31
    assert jpeg_qscale(85) < jpeg_qscale(50)
    with pytest.raises(ValueError, match="between 1 and 100"):
        jpeg_qscale(0)
