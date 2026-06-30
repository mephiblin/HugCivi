from __future__ import annotations

from app.main import source_url_for_job
from app.models import ParsedDownload


def test_source_url_for_ytdl_gallerydl_job_unwraps_youtube_url() -> None:
    parsed = ParsedDownload(
        source="gallerydl",
        raw_input="https://www.youtube.com/watch?v=abc123",
        gallerydl_url="ytdl:https://www.youtube.com/watch?v=abc123",
    )

    assert source_url_for_job({}, parsed) == "https://www.youtube.com/watch?v=abc123"
