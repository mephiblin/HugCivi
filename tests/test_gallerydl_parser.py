from __future__ import annotations

from app.parsers import parse_input


def test_plain_pawchive_urls_route_to_gallerydl() -> None:
    urls = (
        "https://pawchive.pw/posts",
        "https://www.pawchive.pw/patreon/user/12345",
        "https://pawchive.st/fanbox/user/67890/post/abc",
        "https://www.pawchive.st/artists",
    )

    for url in urls:
        parsed = parse_input(url, target_subdir="archives/pawchive")

        assert parsed.source == "gallerydl"
        assert parsed.raw_input == url
        assert parsed.target_subdir == "archives/pawchive"
        assert parsed.gallerydl_url == url
        assert parsed.url is None


def test_unrecognized_pawchive_subdomain_stays_generic() -> None:
    url = "https://cdn.pawchive.pw/file.jpg"

    parsed = parse_input(url)

    assert parsed.source == "generic"
    assert parsed.url == url
    assert parsed.gallerydl_url is None
