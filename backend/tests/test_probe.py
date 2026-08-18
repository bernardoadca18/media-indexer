from app.services.probe import parse_probe


def test_normalizes_ffprobe_payload() -> None:
    result = parse_probe(
        {
            "format": {"duration": "12.5", "format_name": "mov,mp4"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "avg_frame_rate": "60000/1001",
                    "width": 1920,
                    "height": 1080,
                },
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
    )
    assert result.duration_seconds == 12.5
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"
    assert result.fps == 60000 / 1001
    assert (result.width, result.height) == (1920, 1080)
