from __future__ import annotations

from app.parsers import parse_input


def test_youtube_command_routes_to_gallerydl_ytdl() -> None:
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    parsed = parse_input(f"youtube {url}")

    assert parsed.source == "gallerydl"
    assert parsed.raw_input == f"youtube {url}"
    assert parsed.gallerydl_url == f"ytdl:{url}"


def test_yt_dlp_command_routes_to_gallerydl_ytdl() -> None:
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    parsed = parse_input(f"yt-dlp {url}")

    assert parsed.source == "gallerydl"
    assert parsed.raw_input == f"yt-dlp {url}"
    assert parsed.gallerydl_url == f"ytdl:{url}"


def test_yt_command_accepts_short_youtube_url() -> None:
    url = "https://youtu.be/dQw4w9WgXcQ"

    parsed = parse_input(f"yt {url}", target_subdir="videos/music")

    assert parsed.source == "gallerydl"
    assert parsed.target_subdir == "videos/music"
    assert parsed.gallerydl_url == f"ytdl:{url}"


def test_gallerydl_command_accepts_ytdl_scheme() -> None:
    url = "ytdl:https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    parsed = parse_input(f"gallery-dl {url}")

    assert parsed.source == "gallerydl"
    assert parsed.raw_input == f"gallery-dl {url}"
    assert parsed.gallerydl_url == url


def test_gallerydl_command_wraps_plain_youtube_url() -> None:
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    parsed = parse_input(f"gallery-dl {url}")

    assert parsed.source == "gallerydl"
    assert parsed.raw_input == f"gallery-dl {url}"
    assert parsed.gallerydl_url == f"ytdl:{url}"


def test_plain_youtube_url_routes_to_gallerydl_ytdl() -> None:
    url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"

    parsed = parse_input(url)

    assert parsed.source == "gallerydl"
    assert parsed.gallerydl_url == f"ytdl:{url}"


def test_plain_xhamster_numbered_url_routes_to_gallerydl_ytdl() -> None:
    url = "https://xhamster3.com/videos/sample-video-123456"

    parsed = parse_input(url)

    assert parsed.source == "gallerydl"
    assert parsed.gallerydl_url == f"ytdl:{url}"


def test_gallerydl_command_wraps_plain_xhamster_url() -> None:
    url = "https://it.xhamster.com/videos/sample-video-123456"

    parsed = parse_input(f"gallery-dl {url}")

    assert parsed.source == "gallerydl"
    assert parsed.raw_input == f"gallery-dl {url}"
    assert parsed.gallerydl_url == f"ytdl:{url}"


def test_yt_dlp_command_accepts_xhamster_url() -> None:
    url = "https://xhamster26.com/videos/sample-video-123456"

    parsed = parse_input(f"yt-dlp {url}")

    assert parsed.source == "gallerydl"
    assert parsed.raw_input == f"yt-dlp {url}"
    assert parsed.gallerydl_url == f"ytdl:{url}"


def test_plain_known_ytdlp_video_sites_route_to_gallerydl_ytdl() -> None:
    urls = [
        "https://www.pornhub.com/view_video.php?viewkey=abc123",
        "https://www.xvideos.com/video123/test",
        "https://www.xnxx.com/video-abc/test",
        "https://www.redtube.com/12345",
        "https://www.youporn.com/watch/12345/test/",
        "https://spankbang.com/abc/video/test",
    ]

    for url in urls:
        parsed = parse_input(url)

        assert parsed.source == "gallerydl"
        assert parsed.gallerydl_url == f"ytdl:{url}"


def test_non_youtube_http_url_still_routes_to_generic() -> None:
    url = "https://example.com/video.mp4"

    parsed = parse_input(url)

    assert parsed.source == "generic"
    assert parsed.url == url
