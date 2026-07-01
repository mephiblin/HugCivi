from __future__ import annotations

import pytest

from app.parsers import parse_input


@pytest.mark.parametrize(
    "host",
    [
        "civitai.com",
        "www.civitai.com",
        "civitai.red",
        "civitai.green",
    ],
)
def test_civitai_image_url_parses_as_civitai_image_job(host: str) -> None:
    url = f"https://{host}/images/135240496?foo=bar"

    parsed = parse_input(url, target_subdir="archive/images")

    assert parsed.source == "civitai"
    assert parsed.raw_input == url
    assert parsed.target_subdir == "archive/images"
    assert parsed.civitai_image_id == "135240496"
    assert parsed.civitai_image_url == url
    assert parsed.civitai_model_id is None
    assert parsed.civitai_version_id is None
    assert parsed.civitai_download_url is None


def test_civitai_model_url_regression() -> None:
    url = "https://civitai.com/models/12345/example-model?modelVersionId=67890"

    parsed = parse_input(url)

    assert parsed.source == "civitai"
    assert parsed.raw_input == url
    assert parsed.civitai_model_id == "12345"
    assert parsed.civitai_version_id == "67890"
    assert parsed.civitai_image_id is None
    assert parsed.civitai_image_url is None


def test_civitai_model_version_api_url_regression() -> None:
    url = "https://civitai.com/api/v1/model-versions/67890?fileId=abc"

    parsed = parse_input(url)

    assert parsed.source == "civitai"
    assert parsed.raw_input == url
    assert parsed.civitai_version_id == "67890"
    assert parsed.civitai_file_id == "abc"
    assert parsed.civitai_image_id is None
    assert parsed.civitai_image_url is None


def test_civitai_download_url_regression() -> None:
    url = "https://civitai.com/api/download/models/67890?type=Model&format=SafeTensor&primary=true"

    parsed = parse_input(url)

    assert parsed.source == "civitai"
    assert parsed.raw_input == url
    assert parsed.civitai_version_id == "67890"
    assert parsed.civitai_download_url == url
    assert parsed.civitai_file_type == "Model"
    assert parsed.civitai_file_format == "SafeTensor"
    assert parsed.civitai_file_primary is True
    assert parsed.civitai_image_id is None
    assert parsed.civitai_image_url is None
