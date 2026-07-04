from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import requests

from app import downloader
from app.models import ParsedDownload


def http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    response.url = f"https://civitai.com/api/v1/model-versions/{status_code}"
    return requests.HTTPError(f"{status_code} Client Error", response=response)


class FakeDb:
    def __init__(
        self,
        job: dict,
        secrets: dict[str, str] | None = None,
        settings: dict[str, str] | None = None,
        listed_jobs: list[dict] | None = None,
    ) -> None:
        self.job = job
        self.secrets = secrets or {}
        self.settings = settings or {}
        self.listed_jobs = listed_jobs
        self.logs: list[str] = []
        self.created_jobs: list[ParsedDownload] = []
        self.next_job_id = 500

    def get_job(self, job_id: int) -> dict:
        self.job["id"] = job_id
        return dict(self.job)

    def update_job(self, job_id: int, **fields: object) -> None:
        self.job["id"] = job_id
        self.job.update(fields)

    def append_log(self, job_id: int, message: str) -> None:
        self.logs.append(message)

    def get_secret(self, name: str) -> str | None:
        return self.secrets.get(name)

    def get_setting(self, name: str) -> str | None:
        return self.settings.get(name)

    def create_job(self, parsed: ParsedDownload) -> int:
        self.created_jobs.append(parsed)
        self.next_job_id += 1
        return self.next_job_id

    def list_jobs(self, limit: int = 100) -> list[dict]:
        if self.listed_jobs is not None:
            return self.listed_jobs[:limit]
        return [dict(self.job)]

    def library_route_settings(self) -> dict[str, str]:
        return {}


class DownloaderRuntimeTests(unittest.TestCase):
    def test_partial_download_path_includes_job_id_and_url_hash(self) -> None:
        final_path = Path("/data/models/model.safetensors")

        first = downloader.partial_download_path(final_path, 10, "https://example.test/a/model.safetensors")
        second = downloader.partial_download_path(final_path, 11, "https://example.test/a/model.safetensors")
        third = downloader.partial_download_path(final_path, 10, "https://mirror.test/a/model.safetensors")

        self.assertEqual(first.parent, final_path.parent)
        self.assertTrue(first.name.startswith("model.safetensors.job-10-"))
        self.assertTrue(first.name.endswith(".part"))
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, third)

    def test_directory_size_limited_reports_truncation_without_affecting_exact_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(5):
                (root / f"{index}.bin").write_bytes(b"x" * 10)

            limited_size, truncated = downloader.directory_size_limited(root, max_items=2)

            self.assertTrue(truncated)
            self.assertLessEqual(limited_size, 20)
            self.assertEqual(downloader.directory_size(root), 50)

    def test_gallery_progress_snapshot_obeys_scan_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(5):
                path = root / f"{index}.jpg"
                path.write_bytes(b"x" * 10)
                os.utime(path, (100 + index, 100 + index))

            _latest_name, limited_size = downloader.gallery_dl_progress_snapshot(root, max_items=2)

            self.assertLessEqual(limited_size, 20)

    def test_partial_metadata_registration_and_cleanup_are_job_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "models"
            target.mkdir()
            final_path = target / "model.bin"
            part_path = downloader.partial_download_path(final_path, 42, "https://example.test/model.bin")
            part_path.write_bytes(b"partial")
            other_job_part = target / "model.bin.job-43-deadbeef.part"
            other_job_part.write_bytes(b"other")
            fake_db = FakeDb({"metadata_json": json.dumps({"source": "unit"}), "target_dir": str(target)})

            with mock.patch.object(downloader, "db", fake_db), mock.patch.object(downloader, "DATA_ROOT", root):
                downloader.register_job_partial_path(42, part_path, final_path, "https://example.test/model.bin")
                metadata = json.loads(str(fake_db.job["metadata_json"]))
                runtime = metadata[downloader.DOWNLOAD_RUNTIME_METADATA_KEY]

                self.assertEqual(metadata["source"], "unit")
                self.assertEqual(runtime["partial_path"], str(part_path))
                self.assertIn(str(part_path), runtime["partial_paths"])

                removed = downloader.cleanup_job_partial_files(42)

            self.assertIn(part_path, removed)
            self.assertFalse(part_path.exists())
            self.assertTrue(other_job_part.exists())

    def test_local_cleanup_removes_owned_civitai_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "stable-diffusion" / "loras" / "base" / "model" / "version_1"
            target.mkdir(parents=True)
            model_file = target / "model.safetensors"
            model_file.write_bytes(b"model")
            (target / "_civitai_metadata.json").write_text("{}", encoding="utf-8")
            parsed = ParsedDownload(source="civitai", raw_input="https://civitai.com/models/1?modelVersionId=1")
            fake_db = FakeDb(
                {
                    "source": "civitai",
                    "parsed_json": json.dumps(parsed.to_dict()),
                    "target_dir": str(target),
                    "filename": model_file.name,
                }
            )

            with mock.patch.object(downloader, "db", fake_db), mock.patch.object(downloader, "DATA_ROOT", root):
                removed = downloader.cleanup_job_local_files(42)

            self.assertEqual(removed, [target])
            self.assertFalse(target.exists())

    def test_local_cleanup_removes_only_generic_download_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "generic"
            target.mkdir()
            downloaded = target / "file.bin"
            downloaded.write_bytes(b"downloaded")
            other_file = target / "other.bin"
            other_file.write_bytes(b"other")
            metadata = target / "_generic_metadata.json"
            metadata.write_text("{}", encoding="utf-8")
            parsed = ParsedDownload(source="generic", raw_input="https://example.test/file.bin", url="https://example.test/file.bin")
            fake_db = FakeDb(
                {
                    "source": "generic",
                    "parsed_json": json.dumps(parsed.to_dict()),
                    "target_dir": str(target),
                    "filename": downloaded.name,
                }
            )

            with mock.patch.object(downloader, "db", fake_db), mock.patch.object(downloader, "DATA_ROOT", root):
                removed = downloader.cleanup_job_local_files(42)

            self.assertEqual(removed, [downloaded])
            self.assertFalse(downloaded.exists())
            self.assertTrue(other_file.exists())
            self.assertTrue(metadata.exists())

    def test_local_cleanup_skips_directory_used_by_another_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "stable-diffusion" / "checkpoints" / "base" / "model" / "version_1"
            target.mkdir(parents=True)
            (target / "model.safetensors").write_bytes(b"model")
            parsed = ParsedDownload(source="civitai", raw_input="https://civitai.com/models/1?modelVersionId=1")
            job = {
                "id": 42,
                "source": "civitai",
                "parsed_json": json.dumps(parsed.to_dict()),
                "target_dir": str(target),
                "filename": "model.safetensors",
            }
            fake_db = FakeDb(job, listed_jobs=[job, {**job, "id": 43}])

            with mock.patch.object(downloader, "db", fake_db), mock.patch.object(downloader, "DATA_ROOT", root):
                removed = downloader.cleanup_job_local_files(42)

            self.assertEqual(removed, [])
            self.assertTrue(target.exists())
            self.assertTrue(any("referenced by another job" in message for message in fake_db.logs))

    def test_local_cleanup_rejects_paths_outside_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            root.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "model.safetensors").write_bytes(b"model")
            parsed = ParsedDownload(source="civitai", raw_input="https://civitai.com/models/1?modelVersionId=1")
            fake_db = FakeDb(
                {
                    "source": "civitai",
                    "parsed_json": json.dumps(parsed.to_dict()),
                    "target_dir": str(outside),
                    "filename": "model.safetensors",
                }
            )

            with mock.patch.object(downloader, "db", fake_db), mock.patch.object(downloader, "DATA_ROOT", root):
                removed = downloader.cleanup_job_local_files(42)

            self.assertEqual(removed, [])
            self.assertTrue(outside.exists())

    def test_gallery_dl_posix_processes_start_in_new_session(self) -> None:
        kwargs = downloader.gallery_dl_process_kwargs()
        if os.name == "posix":
            self.assertIs(kwargs.get("start_new_session"), True)
        else:
            self.assertIsInstance(kwargs, dict)

    def test_folder_thumbnail_path_picks_first_image_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "hitomi" / "gallery"
            target.mkdir(parents=True)
            (target / "_archive_metadata.json").write_text("{}", encoding="utf-8")
            (target / "001_page.webp.part").write_bytes(b"partial")
            (target / "010_page.webp").write_bytes(b"ten")
            (target / "002_page.webp").write_bytes(b"two")
            first_page = target / "001_page.jpg"
            first_page.write_bytes(b"one")

            self.assertEqual(downloader.folder_thumbnail_path(target), first_page)

    def test_thumbnail_url_for_path_exposes_existing_folder_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            target = root / "hitomi" / "123 Gallery"
            target.mkdir(parents=True)
            first_page = target / "001 page.jpg"
            first_page.write_bytes(b"image")

            with mock.patch.object(downloader, "DATA_ROOT", root):
                self.assertEqual(
                    downloader.thumbnail_url_for_path(target),
                    "/api/fs/preview?path=hitomi/123%20Gallery/001%20page.jpg",
                )
                self.assertEqual(
                    downloader.thumbnail_url_for_path(first_page),
                    "/api/fs/preview?path=hitomi/123%20Gallery/001%20page.jpg",
                )
                self.assertEqual(downloader.thumbnail_media_type(first_page), "image/jpeg")

    def test_civitai_model_download_writes_generation_metadata_sidecar(self) -> None:
        parsed = ParsedDownload(
            source="civitai",
            raw_input="https://civitai.com/models/123?modelVersionId=456",
            civitai_model_id="123",
            civitai_version_id="456",
        )
        version_metadata = {
            "id": 456,
            "name": "v1",
            "baseModel": "SDXL",
            "description": "<p>Version notes</p>",
            "trainedWords": ["trigger words", "style token"],
            "model": {"id": 123, "name": "Example Model", "type": "LORA"},
            "files": [
                {
                    "id": 77,
                    "name": "example.safetensors",
                    "type": "Model",
                    "primary": True,
                    "metadata": {"format": "SafeTensor", "fp": "fp16"},
                    "downloadUrl": "https://download.civitai.com/example.safetensors?token=file-secret",
                }
            ],
        }
        model_page_metadata = {
            "id": 123,
            "name": "Example Model",
            "type": "LORA",
            "description": "<p>Main body</p><p>Use this LoRA with trigger words.</p>",
            "tags": ["style", "character"],
            "creator": {"username": "Creator"},
            "stats": {"downloadCount": 10},
            "modelVersions": [{"id": 456, "name": "v1"}],
        }
        images_response = {
            "items": [
                {
                    "id": 999,
                    "url": "https://image.civitai.com/example/original=true/sample.jpeg?token=image-secret",
                    "width": 1024,
                    "height": 1024,
                    "username": "Artist",
                    "meta": {
                        "prompt": "sunset city",
                        "negativePrompt": "low quality",
                        "seed": 12345,
                        "steps": 30,
                        "sampler": "Euler a",
                        "cfgScale": 7,
                    },
                }
            ],
            "metadata": {"totalItems": 1, "currentPage": 1},
        }
        tensor_summary = {
            "format": "SafeTensor",
            "tensorCount": 453,
            "vramEstimate": {
                "estimatedMinimumVramBytes": 1647522611,
                "recommendedVramBytes": 13592900403,
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            fake_db = FakeDb({"status": "running"})

            def fake_stream_download(
                _job_id: int,
                _session: object,
                _url: str,
                target_dir: Path,
                filename_override: str | None = None,
            ) -> Path:
                target_dir.mkdir(parents=True, exist_ok=True)
                saved = target_dir / (filename_override or "example.safetensors")
                saved.write_bytes(b"model")
                return saved

            with (
                mock.patch.dict(os.environ, {"DOWNLOAD_ENABLE_HEAD_REQUESTS": "0"}),
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(
                    downloader,
                    "fetch_json",
                    side_effect=[version_metadata, model_page_metadata, tensor_summary, images_response],
                ) as fetch_json,
                mock.patch.object(downloader, "fetch_civitai_rendered_model_page_metadata", return_value=None),
                mock.patch.object(downloader, "stream_download", side_effect=fake_stream_download),
            ):
                downloader.download_civitai(99, parsed)

            target = root / "civitai" / "loras" / "sdxl" / "example-model" / "version_456"
            generation_sidecar = target / "_civitai_generation_metadata.json"
            model_sidecar = target / "_civitai_metadata.json"
            preview = target / "civitai_example_999.jpeg"
            self.assertTrue(generation_sidecar.exists())
            self.assertTrue(model_sidecar.exists())
            self.assertTrue(preview.exists())

            generation_payload = json.loads(generation_sidecar.read_text(encoding="utf-8"))
            model_payload = json.loads(model_sidecar.read_text(encoding="utf-8"))
            image = generation_payload["images"][0]

            self.assertEqual(fetch_json.call_count, 4)
            self.assertEqual(
                fetch_json.call_args_list[1].args[1],
                f"{downloader.CIVITAI_API_BASE}/models/123",
            )
            self.assertEqual(
                fetch_json.call_args_list[2].args[1],
                f"{downloader.CIVITAI_API_BASE}/model-files/77/tensor-metadata?summaryOnly=true",
            )
            self.assertEqual(
                fetch_json.call_args_list[3].args[1],
                f"{downloader.CIVITAI_API_BASE}/images?modelVersionId=456&limit=100&sort=Newest&withMeta=true",
            )
            self.assertEqual(generation_payload["kind"], "civitai_model_generation_metadata")
            self.assertEqual(generation_payload["model_id"], "123")
            self.assertEqual(generation_payload["version_id"], "456")
            self.assertEqual(generation_payload["image_count"], 1)
            self.assertEqual(generation_payload["generation_count"], 1)
            self.assertEqual(image["source_url"], "https://civitai.com/images/999")
            self.assertEqual(image["generation_data"]["prompt"]["text"], "sunset city")
            self.assertEqual(image["generation_data"]["negative_prompt"]["text"], "low quality")
            self.assertIn("Steps: 30", image["generation_data"]["copy_all_text"])
            self.assertEqual(image["raw_generation_meta"]["seed"], 12345)
            self.assertEqual(image["local_file"], "civitai_example_999.jpeg")
            self.assertEqual(generation_payload["local_files"]["preview_images"], ["civitai_example_999.jpeg"])
            self.assertEqual(generation_payload["model_details"]["model"]["description"], "Main body\nUse this LoRA with trigger words.")
            self.assertEqual(generation_payload["model_details"]["model"]["tags"], ["style", "character"])
            self.assertEqual(generation_payload["model_details"]["version"]["description"], "Version notes")
            self.assertEqual(generation_payload["model_details"]["version"]["trained_words"], ["trigger words", "style token"])
            self.assertEqual(generation_payload["model_details"]["version"]["files"][0]["format"], "SafeTensor")
            self.assertEqual(generation_payload["model_details"]["version"]["files"][0]["tensor_metadata"]["tensorCount"], 453)
            self.assertEqual(model_payload["tensor_metadata"]["vramEstimate"]["recommendedVramBytes"], 13592900403)
            self.assertEqual(model_payload["model_details"]["model"]["creator"], "Creator")
            self.assertEqual(model_payload["model_page_metadata"]["description"], model_page_metadata["description"])
            self.assertEqual(model_payload["generation_metadata"]["sidecar"], "_civitai_generation_metadata.json")
            self.assertEqual(model_payload["generation_metadata"]["image_count"], 1)
            self.assertEqual(
                model_payload["archive_info"]["thumbnail_url"],
                "/api/fs/preview?path=civitai/loras/sdxl/example-model/version_456/civitai_example_999.jpeg",
            )
            self.assertEqual(fake_db.job["filename"], "example.safetensors")
            self.assertEqual(
                fake_db.job["thumbnail_url"],
                "/api/fs/preview?path=civitai/loras/sdxl/example-model/version_456/civitai_example_999.jpeg",
            )

            sidecar_text = generation_sidecar.read_text(encoding="utf-8")
            self.assertNotIn("image-secret", sidecar_text)
            self.assertNotIn("file-secret", model_sidecar.read_text(encoding="utf-8"))

    def test_civitai_model_preview_prefers_model_version_images_over_gallery_images(self) -> None:
        parsed = ParsedDownload(
            source="civitai",
            raw_input="https://civitai.com/models/2342797/z-image-base",
            civitai_model_id="2342797",
            civitai_version_id="2635223",
        )
        version_metadata = {
            "id": 2635223,
            "name": "Base",
            "baseModel": "ZImageBase",
            "model": {"id": 2342797, "name": "Z Image Base", "type": "Checkpoint"},
            "files": [
                {
                    "id": 77,
                    "name": "z_image_bf16.safetensors",
                    "type": "Model",
                    "primary": True,
                    "metadata": {"format": "SafeTensor", "fp": "bf16"},
                    "downloadUrl": "https://download.civitai.com/z_image_bf16.safetensors",
                }
            ],
            "images": [
                {
                    "url": "https://image.civitai.com/example/original=true/119036219.jpeg",
                    "width": 832,
                    "height": 1216,
                    "type": "image",
                    "nsfwLevel": 1,
                }
            ],
        }
        model_page_metadata = {
            "id": 2342797,
            "name": "Z Image Base",
            "type": "Checkpoint",
            "description": "<p>Z-Image body</p>",
            "modelVersions": [{"id": 2635223, "name": "Base"}],
        }
        gallery_response = {
            "items": [
                {
                    "id": 135633584,
                    "url": "https://image.civitai.com/gallery/original=true/gallery.png",
                    "width": 832,
                    "height": 1216,
                    "meta": {"prompt": "gallery prompt"},
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            fake_db = FakeDb({"status": "running"})
            downloaded_urls: list[str] = []

            def fake_stream_download(
                _job_id: int,
                _session: object,
                url: str,
                target_dir: Path,
                filename_override: str | None = None,
                **_kwargs: object,
            ) -> Path:
                downloaded_urls.append(url)
                target_dir.mkdir(parents=True, exist_ok=True)
                saved = target_dir / (filename_override or "download.bin")
                saved.write_bytes(b"file")
                return saved

            with (
                mock.patch.dict(os.environ, {"DOWNLOAD_ENABLE_HEAD_REQUESTS": "0"}),
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "fetch_json", side_effect=[version_metadata, model_page_metadata, gallery_response]),
                mock.patch.object(downloader, "fetch_civitai_rendered_model_page_metadata", return_value=None),
                mock.patch.object(downloader, "attach_civitai_tensor_metadata_summary", return_value=None),
                mock.patch.object(downloader, "stream_download", side_effect=fake_stream_download),
            ):
                downloader.download_civitai(99, parsed)

            target = root / "civitai" / "checkpoints" / "zimagebase" / "z-image-base" / "version_2635223"
            generation_payload = json.loads((target / "_civitai_generation_metadata.json").read_text(encoding="utf-8"))

            self.assertEqual(downloaded_urls[0], "https://image.civitai.com/example/original=true/119036219.jpeg")
            self.assertTrue((target / "civitai_example_119036219.jpeg").exists())
            self.assertFalse((target / "civitai_example_135633584.png").exists())
            self.assertEqual(generation_payload["model_example_count"], 1)
            self.assertEqual(generation_payload["gallery_image_count"], 1)
            self.assertEqual(generation_payload["images"][0]["source_kind"], "model_version_example")
            self.assertEqual(generation_payload["images"][0]["source_url"], "https://civitai.com/images/119036219")
            self.assertEqual(generation_payload["images"][1]["source_kind"], "gallery")
            self.assertEqual(generation_payload["images"][1]["generation_data"]["prompt"]["text"], "gallery prompt")
            self.assertEqual(
                fake_db.job["thumbnail_url"],
                "/api/fs/preview?path=civitai/checkpoints/zimagebase/z-image-base/version_2635223/civitai_example_119036219.jpeg",
            )

    def test_civitai_model_downloads_required_component_files(self) -> None:
        parsed = ParsedDownload(
            source="civitai",
            raw_input="https://civitai.com/models/2342797?modelVersionId=2635223",
            civitai_model_id="2342797",
            civitai_version_id="2635223",
        )
        version_metadata = {
            "id": 2635223,
            "name": "Base",
            "baseModel": "ZImageBase",
            "model": {"id": 2342797, "name": "Z Image Base", "type": "Checkpoint"},
            "files": [
                {
                    "id": 1,
                    "name": "ae.safetensors",
                    "type": "VAE",
                    "sizeKB": 1,
                    "metadata": {"format": "SafeTensor", "isRequired": True},
                    "downloadUrl": "https://download.civitai.com/ae.safetensors",
                },
                {
                    "id": 2,
                    "name": "zImageBase_base_txt_nf4.safetensors",
                    "type": "Text Encoder",
                    "sizeKB": 1,
                    "metadata": {"format": "SafeTensor", "fp": "nf4", "isRequired": False},
                    "downloadUrl": "https://download.civitai.com/optional-text-encoder.safetensors",
                },
                {
                    "id": 3,
                    "name": "zImageBase_base_txt.safetensors",
                    "type": "Text Encoder",
                    "sizeKB": 1,
                    "metadata": {"format": "SafeTensor", "fp": "fp8", "isRequired": True},
                    "downloadUrl": "https://download.civitai.com/text-encoder.safetensors",
                },
                {
                    "id": 4,
                    "name": "zImageBase_base.safetensors",
                    "type": "Model",
                    "primary": True,
                    "sizeKB": 1,
                    "metadata": {"format": "SafeTensor", "fp": "bf16"},
                    "downloadUrl": "https://download.civitai.com/model.safetensors",
                },
            ],
        }
        model_page_metadata = {
            "id": 2342797,
            "name": "Z Image Base",
            "type": "Checkpoint",
            "description": "<p>Z-Image body</p>",
            "modelVersions": [{"id": 2635223, "name": "Base"}],
        }
        images_response = {"items": []}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            fake_db = FakeDb({"status": "running"})
            downloaded_urls: list[str] = []

            def fake_stream_download(
                _job_id: int,
                _session: object,
                url: str,
                target_dir: Path,
                filename_override: str | None = None,
                **_kwargs: object,
            ) -> Path:
                downloaded_urls.append(url)
                target_dir.mkdir(parents=True, exist_ok=True)
                saved = target_dir / (filename_override or "download.bin")
                saved.write_bytes(b"file")
                return saved

            with (
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "fetch_json", side_effect=[version_metadata, model_page_metadata, images_response]),
                mock.patch.object(downloader, "fetch_civitai_rendered_model_page_metadata", return_value=None),
                mock.patch.object(downloader, "attach_civitai_tensor_metadata_summary", return_value=None),
                mock.patch.object(downloader, "stream_download", side_effect=fake_stream_download),
            ):
                downloader.download_civitai(99, parsed)

            target = root / "civitai" / "checkpoints" / "zimagebase" / "z-image-base" / "version_2635223"
            model_payload = json.loads((target / "_civitai_metadata.json").read_text(encoding="utf-8"))
            generation_payload = json.loads((target / "_civitai_generation_metadata.json").read_text(encoding="utf-8"))
            model_exists = (target / "zImageBase_base.safetensors").exists()
            vae_exists = (target / "ae.safetensors").exists()
            text_encoder_exists = (target / "zImageBase_base_txt.safetensors").exists()
            optional_text_encoder_exists = (target / "zImageBase_base_txt_nf4.safetensors").exists()

        self.assertEqual(
            downloaded_urls,
            [
                "https://download.civitai.com/model.safetensors",
                "https://download.civitai.com/ae.safetensors",
                "https://download.civitai.com/text-encoder.safetensors",
            ],
        )
        self.assertTrue(model_exists)
        self.assertTrue(vae_exists)
        self.assertTrue(text_encoder_exists)
        self.assertFalse(optional_text_encoder_exists)
        self.assertEqual(fake_db.job["filename"], "3 files")
        self.assertEqual(fake_db.job["precision"], "3 files")
        self.assertEqual(
            [(item["role"], item["name"], item["status"]) for item in model_payload["component_downloads"]],
            [
                ("primary", "zImageBase_base.safetensors", "downloaded"),
                ("required_component", "ae.safetensors", "downloaded"),
                ("required_component", "zImageBase_base_txt.safetensors", "downloaded"),
            ],
        )
        self.assertEqual(generation_payload["component_downloads"][1]["type"], "VAE")

    def test_civitai_model_generation_metadata_failure_does_not_block_model_file(self) -> None:
        parsed = ParsedDownload(
            source="civitai",
            raw_input="https://civitai.com/models/123?modelVersionId=456",
            civitai_model_id="123",
            civitai_version_id="456",
        )
        version_metadata = {
            "id": 456,
            "name": "v1",
            "baseModel": "SDXL",
            "model": {"id": 123, "name": "Example Model", "type": "LORA"},
            "files": [
                {
                    "name": "example.safetensors",
                    "type": "Model",
                    "primary": True,
                    "downloadUrl": "https://download.civitai.com/example.safetensors",
                }
            ],
        }
        model_page_metadata = {
            "id": 123,
            "name": "Example Model",
            "type": "LORA",
            "description": "<p>Main body</p>",
            "modelVersions": [{"id": 456, "name": "v1"}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            fake_db = FakeDb({"status": "running"})

            def fake_stream_download(
                _job_id: int,
                _session: object,
                _url: str,
                target_dir: Path,
                filename_override: str | None = None,
            ) -> Path:
                target_dir.mkdir(parents=True, exist_ok=True)
                saved = target_dir / (filename_override or "example.safetensors")
                saved.write_bytes(b"model")
                return saved

            with (
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "fetch_json", side_effect=[version_metadata, model_page_metadata, http_error(500)]),
                mock.patch.object(downloader, "fetch_civitai_rendered_model_page_metadata", return_value=None),
                mock.patch.object(downloader, "attach_civitai_tensor_metadata_summary", return_value=None),
                mock.patch.object(downloader, "stream_download", side_effect=fake_stream_download),
            ):
                downloader.download_civitai(99, parsed)

            target = root / "civitai" / "loras" / "sdxl" / "example-model" / "version_456"
            self.assertFalse((target / "_civitai_generation_metadata.json").exists())
            self.assertTrue((target / "example.safetensors").exists())
            self.assertEqual(fake_db.job["filename"], "example.safetensors")
            self.assertTrue(any("civitai.model.generation_metadata.warning" in message for message in fake_db.logs))

    def test_civitai_refresh_keeps_existing_model_file_and_updates_sidecars(self) -> None:
        parsed = ParsedDownload(
            source="civitai",
            raw_input="https://civitai.com/models/123?modelVersionId=456",
            target_subdir="civitai/loras/sdxl/refresh-model/version_456",
            civitai_model_id="123",
            civitai_version_id="456",
            civitai_refresh=True,
        )
        version_metadata = {
            "id": 456,
            "name": "v2",
            "baseModel": "SDXL",
            "baseModelType": "Standard",
            "status": "Published",
            "publishedAt": "2026-01-27T19:01:33.766Z",
            "stats": {"downloadCount": 42, "thumbsUpCount": 9},
            "model": {"id": 123, "name": "Refresh Model", "type": "LORA"},
            "files": [
                {
                    "id": 700,
                    "name": "remote.safetensors",
                    "type": "Model",
                    "primary": True,
                    "sizeKB": 1024,
                    "metadata": {"format": "SafeTensor", "fp": "bf16", "size": "full", "isRequired": True},
                    "hashes": {"AutoV2": "ABC123", "SHA256": "ABC123DEF456"},
                    "pickleScanResult": "Success",
                    "virusScanResult": "Success",
                    "downloadUrl": "https://download.civitai.com/remote.safetensors",
                }
            ],
        }
        model_page_metadata = {
            "id": 123,
            "name": "Refresh Model",
            "type": "LORA",
            "description": "<p>Updated body</p>",
            "availability": "Public",
            "stats": {"downloadCount": 100, "thumbsUpCount": 20},
            "modelVersions": [{"id": 456, "name": "v2"}],
        }
        images_response = {
            "items": [
                {
                    "id": 999,
                    "url": "https://image.civitai.com/example/sample.jpeg",
                    "width": 512,
                    "height": 512,
                    "meta": {"prompt": "updated prompt"},
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            target = root / "civitai" / "loras" / "sdxl" / "refresh-model" / "version_456"
            target.mkdir(parents=True)
            existing = target / "existing.safetensors"
            existing.write_bytes(b"existing model")
            fake_db = FakeDb({"status": "running"})
            downloaded_urls: list[str] = []

            def fake_stream_download(
                _job_id: int,
                _session: object,
                url: str,
                target_dir: Path,
                filename_override: str | None = None,
            ) -> Path:
                downloaded_urls.append(url)
                target_dir.mkdir(parents=True, exist_ok=True)
                saved = target_dir / (filename_override or "downloaded.bin")
                saved.write_bytes(b"preview")
                return saved

            with (
                mock.patch.dict(os.environ, {"DOWNLOAD_ENABLE_HEAD_REQUESTS": "0"}),
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "fetch_json", side_effect=[version_metadata, model_page_metadata, images_response]),
                mock.patch.object(downloader, "fetch_civitai_rendered_model_page_metadata", return_value=None),
                mock.patch.object(downloader, "attach_civitai_tensor_metadata_summary", return_value=None),
                mock.patch.object(downloader, "stream_download", side_effect=fake_stream_download),
            ):
                downloader.download_civitai(99, parsed)

            self.assertEqual(downloaded_urls, ["https://image.civitai.com/example/sample.jpeg"])
            self.assertEqual(existing.read_bytes(), b"existing model")
            self.assertTrue((target / "civitai_example_999.jpeg").exists())
            generation_payload = json.loads((target / "_civitai_generation_metadata.json").read_text(encoding="utf-8"))
            model_payload = json.loads((target / "_civitai_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(generation_payload["model_details"]["model"]["description"], "Updated body")
            self.assertEqual(generation_payload["model_details"]["model"]["stats"]["downloadCount"], 100)
            self.assertEqual(generation_payload["model_details"]["version"]["status"], "Published")
            self.assertEqual(generation_payload["model_details"]["version"]["published_at"], "2026-01-27T19:01:33.766Z")
            self.assertEqual(generation_payload["model_details"]["version"]["base_model_type"], "Standard")
            self.assertEqual(generation_payload["model_details"]["version"]["files"][0]["hashes"]["AutoV2"], "ABC123")
            self.assertTrue(generation_payload["model_details"]["version"]["files"][0]["is_required"])
            self.assertEqual(generation_payload["model_details"]["version"]["files"][0]["pickle_scan_result"], "Success")
            self.assertEqual(generation_payload["images"][0]["generation_data"]["prompt"]["text"], "updated prompt")
            self.assertTrue(model_payload["generation_metadata"]["sidecar"])
            self.assertEqual(fake_db.job["filename"], "existing.safetensors")
            self.assertTrue(any("existing model file kept" in message for message in fake_db.logs))

    def test_civitai_image_page_archives_image_and_queues_unique_resources(self) -> None:
        raw_input = "https://civitai.com/images/135240496?token=secret-token"
        parsed = ParsedDownload(
            source="civitai",
            raw_input=raw_input,
            civitai_image_id="135240496",
            civitai_image_url=raw_input,
        )
        api_item = {
            "id": 135240496,
            "postId": 29477144,
            "url": "https://image.civitai.com/example/original=true/sample.jpeg?token=image-secret",
            "width": 800,
            "height": 1000,
            "username": "Creator Name",
            "createdAt": "2026-06-29T14:19:52.635Z",
            "nsfwLevel": 0,
            "meta": {
                "meta": {
                    "prompt": "a bright city",
                    "negativePrompt": "low quality",
                    "seed": 2484449105,
                    "steps": 25,
                    "sampler": "Euler a",
                    "cfgScale": 7,
                    "resources": [
                        {
                            "name": "Example LoRA",
                            "type": "LORA",
                            "modelId": 2061456,
                            "modelVersionId": 3059910,
                            "modelVersionName": "v4.0",
                            "weight": 0.44,
                            "baseModel": "Illustrious",
                        },
                        {
                            "name": "Duplicate LoRA",
                            "type": "LORA",
                            "modelId": 2061456,
                            "modelVersionId": 3059910,
                            "weight": 0.12,
                        },
                        {"name": "No version", "type": "Embedding"},
                    ],
                }
            },
        }
        version_metadata = {
            "id": 3059910,
            "name": "v4.0",
            "baseModel": "Illustrious",
            "model": {"id": 2061456, "name": "Example LoRA", "type": "LORA"},
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            fake_db = FakeDb({"status": "running"}, secrets={"CIVITAI_TOKEN": "unit-token"})
            original_create_job = fake_db.create_job
            fake_db.create_job = mock.Mock(side_effect=original_create_job)

            def fake_stream_download(
                _job_id: int,
                _session: object,
                _url: str,
                target_dir: Path,
                filename_override: str | None = None,
            ) -> Path:
                target_dir.mkdir(parents=True, exist_ok=True)
                saved = target_dir / str(filename_override)
                saved.write_bytes(b"image")
                return saved

            with (
                mock.patch.dict(os.environ, {"DOWNLOAD_ENABLE_HEAD_REQUESTS": "0"}),
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "fetch_json", side_effect=[{"items": [api_item]}, version_metadata]) as fetch_json,
                mock.patch.object(downloader, "stream_download", side_effect=fake_stream_download) as stream_download,
                mock.patch.object(downloader, "enqueue_job") as enqueue_job,
            ):
                downloader.download_civitai(99, parsed)

            target = root / "civitai" / "images" / "Creator_Name" / "image_135240496"
            sidecar = target / "_civitai_image_metadata.json"
            self.assertTrue(sidecar.exists())
            payload = json.loads(sidecar.read_text(encoding="utf-8"))

            self.assertEqual(fetch_json.call_count, 2)
            self.assertEqual(
                fetch_json.call_args_list[0].args[1],
                f"{downloader.CIVITAI_API_BASE}/images?imageId=135240496&withMeta=true",
            )
            self.assertEqual(
                fetch_json.call_args_list[1].args[1],
                f"{downloader.CIVITAI_API_BASE}/model-versions/3059910",
            )
            self.assertEqual(stream_download.call_args.kwargs["filename_override"], "image_135240496.jpeg")
            self.assertEqual(fake_db.job["target_dir"], str(target))
            self.assertEqual(fake_db.job["filename"], "image_135240496.jpeg")
            self.assertEqual(fake_db.job["model_category"], "Civitai Image Page")
            self.assertEqual(
                fake_db.job["thumbnail_url"],
                "/api/fs/preview?path=civitai/images/Creator_Name/image_135240496/image_135240496.jpeg",
            )

            generation = payload["generation_data"]
            self.assertEqual(generation["prompt"]["text"], "a bright city")
            self.assertEqual(generation["negative_prompt"]["text"], "low quality")
            self.assertIn("Negative prompt: low quality", generation["copy_all_text"])
            self.assertEqual(generation["model_version_ids"], ["3059910"])
            self.assertEqual(payload["resource_downloads"][0]["child_job_id"], 501)
            self.assertEqual(payload["resource_downloads"][0]["model_version_id"], "3059910")

            self.assertEqual(len(fake_db.created_jobs), 1)
            fake_db.create_job.assert_called_once()
            child = fake_db.created_jobs[0]
            self.assertEqual(child.source, "civitai")
            self.assertEqual(child.civitai_model_id, "2061456")
            self.assertEqual(child.civitai_version_id, "3059910")
            self.assertIsNone(child.target_subdir)
            enqueue_job.assert_called_once_with(501)

            sidecar_text = sidecar.read_text(encoding="utf-8")
            self.assertNotIn("secret-token", sidecar_text)
            self.assertNotIn("image-secret", sidecar_text)
            self.assertNotIn("unit-token", sidecar_text)

    def test_civitai_image_page_enriches_model_version_only_resources(self) -> None:
        parsed = ParsedDownload(
            source="civitai",
            raw_input="https://civitai.com/images/135240496",
            civitai_image_id="135240496",
            civitai_image_url="https://civitai.com/images/135240496",
        )
        api_item = {
            "id": 135240496,
            "url": "https://image.civitai.com/example/sample.png",
            "username": "creator",
            "modelVersionIds": [222],
            "meta": {"prompt": "neon dusk"},
        }
        version_metadata = {
            "id": 222,
            "name": "v2.0",
            "baseModel": "SDXL 1.0",
            "model": {"id": 111, "name": "Enriched Checkpoint", "type": "Checkpoint"},
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            fake_db = FakeDb({"status": "running"})
            original_create_job = fake_db.create_job
            fake_db.create_job = mock.Mock(side_effect=original_create_job)

            def fake_stream_download(
                _job_id: int,
                _session: object,
                _url: str,
                target_dir: Path,
                filename_override: str | None = None,
            ) -> Path:
                target_dir.mkdir(parents=True, exist_ok=True)
                saved = target_dir / str(filename_override)
                saved.write_bytes(b"image")
                return saved

            with (
                mock.patch.dict(os.environ, {"DOWNLOAD_ENABLE_HEAD_REQUESTS": "0"}),
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "fetch_json", side_effect=[{"items": [api_item]}, version_metadata]) as fetch_json,
                mock.patch.object(downloader, "stream_download", side_effect=fake_stream_download),
                mock.patch.object(downloader, "enqueue_job") as enqueue_job,
            ):
                downloader.download_civitai(99, parsed)

            target = root / "civitai" / "images" / "creator" / "image_135240496"
            payload = json.loads((target / "_civitai_image_metadata.json").read_text(encoding="utf-8"))
            resource = payload["generation_data"]["resources"][0]

            self.assertEqual(fetch_json.call_count, 2)
            self.assertEqual(resource["name"], "Enriched Checkpoint")
            self.assertEqual(resource["type"], "Checkpoint")
            self.assertEqual(resource["model_id"], "111")
            self.assertEqual(resource["model_version_id"], "222")
            self.assertEqual(resource["href"], "https://civitai.com/models/111?modelVersionId=222")
            self.assertEqual(payload["generation_data"]["model_version_ids"], ["222"])
            child = fake_db.created_jobs[0]
            self.assertEqual(child.raw_input, "https://civitai.com/models/111?modelVersionId=222")
            self.assertEqual(child.civitai_model_id, "111")
            self.assertEqual(child.civitai_version_id, "222")
            enqueue_job.assert_called_once_with(501)

    def test_civitai_image_page_skips_resource_that_is_already_downloaded(self) -> None:
        parsed = ParsedDownload(
            source="civitai",
            raw_input="https://civitai.com/images/135240496",
            civitai_image_id="135240496",
            civitai_image_url="https://civitai.com/images/135240496",
        )
        api_item = {
            "id": 135240496,
            "url": "https://image.civitai.com/example/sample.png",
            "username": "creator",
            "meta": {
                "resources": [
                    {
                        "name": "Existing LoRA",
                        "type": "LORA",
                        "modelId": 123,
                        "modelVersionId": 456,
                    }
                ]
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            existing_target = root / "stable-diffusion" / "loras" / "existing" / "version_456"
            existing_target.mkdir(parents=True)
            (existing_target / "existing.safetensors").write_bytes(b"model")
            existing_parsed = ParsedDownload(
                source="civitai",
                raw_input="https://civitai.com/models/123?modelVersionId=456",
                civitai_model_id="123",
                civitai_version_id="456",
            )
            fake_db = FakeDb(
                {"status": "running"},
                listed_jobs=[
                    {
                        "id": 44,
                        "source": "civitai",
                        "status": "done",
                        "target_dir": str(existing_target),
                        "parsed_json": json.dumps(existing_parsed.to_dict()),
                        "metadata_json": "",
                    }
                ],
            )
            original_create_job = fake_db.create_job
            fake_db.create_job = mock.Mock(side_effect=original_create_job)

            def fake_stream_download(
                _job_id: int,
                _session: object,
                _url: str,
                target_dir: Path,
                filename_override: str | None = None,
            ) -> Path:
                target_dir.mkdir(parents=True, exist_ok=True)
                saved = target_dir / str(filename_override)
                saved.write_bytes(b"image")
                return saved

            with (
                mock.patch.dict(os.environ, {"DOWNLOAD_ENABLE_HEAD_REQUESTS": "0"}),
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "fetch_json", return_value={"items": [api_item]}) as fetch_json,
                mock.patch.object(downloader, "stream_download", side_effect=fake_stream_download),
                mock.patch.object(downloader, "enqueue_job") as enqueue_job,
            ):
                downloader.download_civitai(99, parsed)

            payload = json.loads(
                (root / "civitai" / "images" / "creator" / "image_135240496" / "_civitai_image_metadata.json")
                .read_text(encoding="utf-8")
            )
            resource_download = payload["resource_downloads"][0]

            fetch_json.assert_called_once()
            self.assertEqual(resource_download["status"], "present")
            self.assertEqual(resource_download["existing_job_id"], 44)
            self.assertEqual(resource_download["target_path"], "stable-diffusion/loras/existing/version_456")
            fake_db.create_job.assert_not_called()
            enqueue_job.assert_not_called()

    def test_civitai_image_page_skips_resource_with_existing_active_job(self) -> None:
        parsed = ParsedDownload(
            source="civitai",
            raw_input="https://civitai.com/images/135240496",
            civitai_image_id="135240496",
            civitai_image_url="https://civitai.com/images/135240496",
        )
        api_item = {
            "id": 135240496,
            "url": "https://image.civitai.com/example/sample.png",
            "username": "creator",
            "meta": {"resources": [{"name": "Queued LoRA", "modelId": 123, "modelVersionId": 456}]},
        }
        existing_parsed = ParsedDownload(
            source="civitai",
            raw_input="https://civitai.com/models/123?modelVersionId=456",
            civitai_model_id="123",
            civitai_version_id="456",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            fake_db = FakeDb(
                {"status": "running"},
                listed_jobs=[
                    {
                        "id": 45,
                        "source": "civitai",
                        "status": "running",
                        "target_dir": "",
                        "parsed_json": json.dumps(existing_parsed.to_dict()),
                        "metadata_json": "",
                    }
                ],
            )
            original_create_job = fake_db.create_job
            fake_db.create_job = mock.Mock(side_effect=original_create_job)

            def fake_stream_download(
                _job_id: int,
                _session: object,
                _url: str,
                target_dir: Path,
                filename_override: str | None = None,
            ) -> Path:
                target_dir.mkdir(parents=True, exist_ok=True)
                saved = target_dir / str(filename_override)
                saved.write_bytes(b"image")
                return saved

            with (
                mock.patch.dict(os.environ, {"DOWNLOAD_ENABLE_HEAD_REQUESTS": "0"}),
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "fetch_json", return_value={"items": [api_item]}),
                mock.patch.object(downloader, "stream_download", side_effect=fake_stream_download),
                mock.patch.object(downloader, "enqueue_job") as enqueue_job,
            ):
                downloader.download_civitai(99, parsed)

            payload = json.loads(
                (root / "civitai" / "images" / "creator" / "image_135240496" / "_civitai_image_metadata.json")
                .read_text(encoding="utf-8")
            )
            resource_download = payload["resource_downloads"][0]

            self.assertEqual(resource_download["status"], "already_queued")
            self.assertEqual(resource_download["existing_job_id"], 45)
            fake_db.create_job.assert_not_called()
            enqueue_job.assert_not_called()

    def test_civitai_image_page_skips_resource_after_permanent_failure(self) -> None:
        parsed = ParsedDownload(
            source="civitai",
            raw_input="https://civitai.com/images/135240496",
            civitai_image_id="135240496",
            civitai_image_url="https://civitai.com/images/135240496",
        )
        api_item = {
            "id": 135240496,
            "url": "https://image.civitai.com/example/sample.png",
            "username": "creator",
            "meta": {"resources": [{"name": "Gone LoRA", "modelId": 123, "modelVersionId": 456}]},
        }
        failed_parsed = ParsedDownload(
            source="civitai",
            raw_input="https://civitai.com/models/123?modelVersionId=456",
            civitai_model_id="123",
            civitai_version_id="456",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            fake_db = FakeDb(
                {"status": "running"},
                listed_jobs=[
                    {
                        "id": 46,
                        "source": "civitai",
                        "status": "failed",
                        "error": "404 Client Error: Not Found",
                        "updated_at": "2026-07-01T00:00:00+00:00",
                        "target_dir": "",
                        "parsed_json": json.dumps(failed_parsed.to_dict()),
                        "metadata_json": "",
                    }
                ],
            )
            original_create_job = fake_db.create_job
            fake_db.create_job = mock.Mock(side_effect=original_create_job)

            def fake_stream_download(
                _job_id: int,
                _session: object,
                _url: str,
                target_dir: Path,
                filename_override: str | None = None,
            ) -> Path:
                target_dir.mkdir(parents=True, exist_ok=True)
                saved = target_dir / str(filename_override)
                saved.write_bytes(b"image")
                return saved

            with (
                mock.patch.dict(os.environ, {"DOWNLOAD_ENABLE_HEAD_REQUESTS": "0"}),
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "fetch_json", return_value={"items": [api_item]}),
                mock.patch.object(downloader, "stream_download", side_effect=fake_stream_download),
                mock.patch.object(downloader, "enqueue_job") as enqueue_job,
            ):
                downloader.download_civitai(99, parsed)

            payload = json.loads(
                (root / "civitai" / "images" / "creator" / "image_135240496" / "_civitai_image_metadata.json")
                .read_text(encoding="utf-8")
            )
            resource_download = payload["resource_downloads"][0]

            self.assertEqual(resource_download["status"], "unavailable")
            self.assertEqual(resource_download["existing_job_id"], 46)
            fake_db.create_job.assert_not_called()
            enqueue_job.assert_not_called()

    def test_civitai_image_page_skips_resource_when_preflight_is_private_or_deleted(self) -> None:
        parsed = ParsedDownload(
            source="civitai",
            raw_input="https://civitai.com/images/135240496",
            civitai_image_id="135240496",
            civitai_image_url="https://civitai.com/images/135240496",
        )
        api_item = {
            "id": 135240496,
            "url": "https://image.civitai.com/example/sample.png",
            "username": "creator",
            "meta": {"resources": [{"name": "Private LoRA", "modelId": 123, "modelVersionId": 456}]},
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            fake_db = FakeDb({"status": "running"}, listed_jobs=[])
            original_create_job = fake_db.create_job
            fake_db.create_job = mock.Mock(side_effect=original_create_job)

            def fake_stream_download(
                _job_id: int,
                _session: object,
                _url: str,
                target_dir: Path,
                filename_override: str | None = None,
            ) -> Path:
                target_dir.mkdir(parents=True, exist_ok=True)
                saved = target_dir / str(filename_override)
                saved.write_bytes(b"image")
                return saved

            with (
                mock.patch.dict(os.environ, {"DOWNLOAD_ENABLE_HEAD_REQUESTS": "0"}),
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "fetch_json", side_effect=[{"items": [api_item]}, http_error(404)]) as fetch_json,
                mock.patch.object(downloader, "stream_download", side_effect=fake_stream_download),
                mock.patch.object(downloader, "enqueue_job") as enqueue_job,
            ):
                downloader.download_civitai(99, parsed)

            payload = json.loads(
                (root / "civitai" / "images" / "creator" / "image_135240496" / "_civitai_image_metadata.json")
                .read_text(encoding="utf-8")
            )
            resource_download = payload["resource_downloads"][0]
            resource = payload["generation_data"]["resources"][0]

            self.assertEqual(fetch_json.call_count, 2)
            self.assertEqual(resource_download["status"], "unavailable")
            self.assertIn("not found", resource_download["reason"].lower())
            self.assertEqual(resource["availability"], "unavailable")
            fake_db.create_job.assert_not_called()
            enqueue_job.assert_not_called()

    def test_civitai_image_page_rejects_mismatched_api_image_id(self) -> None:
        parsed = ParsedDownload(
            source="civitai",
            raw_input="https://civitai.com/images/135240496",
            civitai_image_id="135240496",
            civitai_image_url="https://civitai.com/images/135240496",
        )
        fake_db = FakeDb({"status": "running"})
        original_create_job = fake_db.create_job
        fake_db.create_job = mock.Mock(side_effect=original_create_job)

        with (
            mock.patch.object(downloader, "db", fake_db),
            mock.patch.object(downloader, "fetch_json", return_value={"items": [{"id": 1}]}),
            mock.patch.object(downloader, "stream_download") as stream_download,
            mock.patch.object(downloader, "enqueue_job") as enqueue_job,
        ):
            with self.assertRaisesRegex(ValueError, "requested imageId=135240496"):
                downloader.download_civitai(100, parsed)

        stream_download.assert_not_called()
        enqueue_job.assert_not_called()
        fake_db.create_job.assert_not_called()
        self.assertEqual(fake_db.created_jobs, [])

    def test_gallerydl_folder_download_sets_local_thumbnail_url(self) -> None:
        parsed = ParsedDownload(
            source="gallerydl",
            raw_input="https://example.com/gallery/123",
            gallerydl_url="https://example.com/gallery/123",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            fake_db = FakeDb({"status": "running"})

            def fake_run_gallery_dl_process(_job_id: int, _command: list[str], target: Path) -> None:
                target.mkdir(parents=True, exist_ok=True)
                (target / "002_page.webp").write_bytes(b"two")
                (target / "001_page.jpg").write_bytes(b"one")

            with (
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "gallery_dl_available", return_value=True),
                mock.patch.object(downloader, "gallery_dl_version", return_value="test-gallery-dl"),
                mock.patch.object(downloader, "run_gallery_dl_process", side_effect=fake_run_gallery_dl_process),
            ):
                downloader.download_gallerydl(55, parsed)

        self.assertEqual(
            fake_db.job["thumbnail_url"],
            "/api/fs/preview?path=gallery-dl/example.com/123/001_page.jpg",
        )

    def test_ytdl_download_uses_direct_ytdlp_process(self) -> None:
        parsed = ParsedDownload(
            source="gallerydl",
            raw_input="https://xhamster3.com/videos/sample-video-123456",
            gallerydl_url="ytdl:https://xhamster3.com/videos/sample-video-123456",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            fake_db = FakeDb({"status": "running"})

            def fake_run_ytdlp_process(_job_id: int, _command: list[str], target: Path) -> None:
                target.mkdir(parents=True, exist_ok=True)
                (target / "sample-video-123456.mp4").write_bytes(b"video")

            with (
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "gallery_dl_available", return_value=True),
                mock.patch.object(downloader, "yt_dlp_available", return_value=True),
                mock.patch.object(downloader, "gallery_dl_version", return_value="test-gallery-dl"),
                mock.patch.object(downloader, "yt_dlp_version", return_value="test-yt-dlp"),
                mock.patch.object(downloader, "run_ytdlp_process", side_effect=fake_run_ytdlp_process) as ytdlp_run,
                mock.patch.object(downloader, "run_gallery_dl_process") as gallery_run,
            ):
                downloader.download_gallerydl(56, parsed)

        ytdlp_run.assert_called_once()
        gallery_run.assert_not_called()
        self.assertEqual(fake_db.job["filename"], "1 files")

    def test_youtube_video_download_groups_by_channel_name(self) -> None:
        parsed = ParsedDownload(
            source="gallerydl",
            raw_input="https://www.youtube.com/watch?v=abc123",
            gallerydl_url="ytdl:https://www.youtube.com/watch?v=abc123",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            target = root / "gallery-dl" / "youtube.com" / "channel" / "테스트_채널"
            fake_db = FakeDb({"status": "running"})

            def fake_run_ytdlp_process(_job_id: int, _command: list[str], target_dir: Path) -> None:
                self.assertEqual(target_dir, target)
                target_dir.mkdir(parents=True, exist_ok=True)
                (target_dir / "sample [abc123].mp4").write_bytes(b"video")

            with (
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "yt_dlp_available", return_value=True),
                mock.patch.object(downloader, "yt_dlp_version", return_value="test-yt-dlp"),
                mock.patch.object(downloader, "yt_dlp_metadata_info", return_value={"channel": "테스트 채널"}),
                mock.patch.object(downloader, "yt_dlp_subtitle_info", return_value={}),
                mock.patch.object(downloader, "run_ytdlp_process", side_effect=fake_run_ytdlp_process),
            ):
                downloader.download_gallerydl(156, parsed)
            self.assertEqual(json.loads((target / "_archive_metadata.json").read_text())["archive_kind"], "channel")

        self.assertEqual(fake_db.job["target_dir"], str(target))

    def test_youtube_playlist_download_groups_by_playlist_id(self) -> None:
        parsed = ParsedDownload(
            source="gallerydl",
            raw_input="https://www.youtube.com/playlist?list=PLabc123",
            gallerydl_url="ytdl:https://www.youtube.com/playlist?list=PLabc123",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            target = root / "gallery-dl" / "youtube.com" / "playlist" / "PLabc123"
            fake_db = FakeDb({"status": "running"})

            def fake_run_ytdlp_process(_job_id: int, _command: list[str], target_dir: Path) -> None:
                self.assertEqual(target_dir, target)
                target_dir.mkdir(parents=True, exist_ok=True)
                (target_dir / "sample [abc123].mp4").write_bytes(b"video")

            with (
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "yt_dlp_available", return_value=True),
                mock.patch.object(downloader, "yt_dlp_version", return_value="test-yt-dlp"),
                mock.patch.object(downloader, "yt_dlp_metadata_info") as metadata_probe,
                mock.patch.object(downloader, "yt_dlp_subtitle_info", return_value={}),
                mock.patch.object(downloader, "run_ytdlp_process", side_effect=fake_run_ytdlp_process),
            ):
                downloader.download_gallerydl(157, parsed)
            self.assertEqual(json.loads((target / "_archive_metadata.json").read_text())["archive_kind"], "playlist")

        metadata_probe.assert_not_called()
        self.assertEqual(fake_db.job["target_dir"], str(target))

    def test_ytdl_download_fails_when_no_media_files_are_created(self) -> None:
        parsed = ParsedDownload(
            source="gallerydl",
            raw_input="https://xhamster3.com/videos/sample-video-123456",
            gallerydl_url="ytdl:https://xhamster3.com/videos/sample-video-123456",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            fake_db = FakeDb({"status": "running"})

            with (
                mock.patch.object(downloader, "DATA_ROOT", root),
                mock.patch.object(downloader, "db", fake_db),
                mock.patch.object(downloader, "gallery_dl_available", return_value=True),
                mock.patch.object(downloader, "yt_dlp_available", return_value=True),
                mock.patch.object(downloader, "gallery_dl_version", return_value="test-gallery-dl"),
                mock.patch.object(downloader, "yt_dlp_version", return_value="test-yt-dlp"),
                mock.patch.object(downloader, "run_ytdlp_process", return_value=None),
            ):
                with self.assertRaisesRegex(RuntimeError, "No media files"):
                    downloader.download_gallerydl(57, parsed)
            self.assertFalse((root / "gallery-dl" / "xhamster3.com" / "sample-video-123456").exists())

    def test_gallery_dl_downloaded_files_ignores_subtitle_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "sample [abc123].en.srt").write_text("subtitle", encoding="utf-8")
            (target / "sample [abc123].ko.vtt").write_text("WEBVTT", encoding="utf-8")
            (target / "sample [abc123].info.json").write_text("{}", encoding="utf-8")

            self.assertEqual(downloader.gallery_dl_downloaded_files(target), [])

            media = target / "sample [abc123].mp4"
            media.write_bytes(b"video")

            self.assertEqual(downloader.gallery_dl_downloaded_files(target), [media])

    def test_empty_gallery_archive_cleanup_keeps_real_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            target = root / "gallery-dl" / "example.com" / "archive"
            target.mkdir(parents=True)
            (target / "_archive_metadata.json").write_text("{}", encoding="utf-8")
            media = target / "video.mp4"
            media.write_bytes(b"video")
            fake_db = FakeDb({"status": "running"})

            with mock.patch.object(downloader, "DATA_ROOT", root), mock.patch.object(downloader, "db", fake_db):
                downloader.cleanup_empty_gallery_archive_target(58, target)

            self.assertTrue(target.exists())
            self.assertTrue(media.exists())

    def test_remove_pending_job_drops_queued_id(self) -> None:
        with downloader._SCHEDULER_CONDITION:
            downloader._PENDING_JOB_IDS[:] = [1, 2, 3, 2]

        downloader.remove_pending_job(2)

        with downloader._SCHEDULER_CONDITION:
            self.assertEqual(downloader._PENDING_JOB_IDS, [1, 3])

    def test_stop_workers_wakes_and_stops_waiting_scheduler(self) -> None:
        picker_entered = threading.Event()

        def no_schedulable_job() -> tuple[None, None]:
            picker_entered.set()
            return None, None

        with mock.patch.object(downloader, "pick_next_schedulable_job_locked", side_effect=no_schedulable_job):
            with downloader._WORKERS_LOCK:
                downloader._SCHEDULER_STOP_EVENT.clear()
                downloader._WORKERS_STARTED = True
                thread = threading.Thread(target=downloader.scheduler_loop, name="test-download-scheduler")
                downloader._SCHEDULER_THREAD = thread
            thread.start()
            self.assertTrue(picker_entered.wait(timeout=1.0))

            try:
                self.assertTrue(downloader.stop_workers(timeout_seconds=1.0))
                self.assertFalse(thread.is_alive())
            finally:
                downloader._SCHEDULER_STOP_EVENT.set()
                with downloader._SCHEDULER_CONDITION:
                    downloader._SCHEDULER_CONDITION.notify_all()
                thread.join(timeout=1.0)
                with downloader._WORKERS_LOCK:
                    downloader._SCHEDULER_THREAD = None
                    downloader._WORKERS_STARTED = False
                    downloader._SCHEDULER_STOP_EVENT.clear()
                with downloader._SCHEDULER_CONDITION:
                    downloader._PENDING_JOB_IDS.clear()

    def test_scheduler_does_not_start_job_when_stop_arrives_after_slot_reservation(self) -> None:
        provider = "generic:example.test"
        original_reserve = downloader.reserve_provider_slot_locked

        def reserve_and_stop(reserved_provider: str) -> None:
            original_reserve(reserved_provider)
            downloader._SCHEDULER_STOP_EVENT.set()

        with (
            mock.patch.object(downloader, "pick_next_schedulable_job_locked", return_value=((7, provider), None)),
            mock.patch.object(downloader, "reserve_provider_slot_locked", side_effect=reserve_and_stop),
            mock.patch.object(downloader.threading, "Thread") as thread_factory,
        ):
            try:
                with downloader._SCHEDULER_CONDITION:
                    downloader._SCHEDULER_STOP_EVENT.clear()
                    downloader._ACTIVE_GLOBAL_JOBS = 0
                    downloader._ACTIVE_PROVIDER_JOBS.clear()
                    downloader._PROVIDER_COOLDOWN_UNTIL.clear()

                downloader.scheduler_loop()

                thread_factory.assert_not_called()
                with downloader._SCHEDULER_CONDITION:
                    self.assertEqual(downloader._ACTIVE_GLOBAL_JOBS, 0)
                    self.assertEqual(downloader._ACTIVE_PROVIDER_JOBS, {})
            finally:
                with downloader._SCHEDULER_CONDITION:
                    downloader._SCHEDULER_STOP_EVENT.clear()
                    downloader._ACTIVE_GLOBAL_JOBS = 0
                    downloader._ACTIVE_PROVIDER_JOBS.clear()
                    downloader._PROVIDER_COOLDOWN_UNTIL.clear()

    def test_download_scheduler_skips_internal_job_rows(self) -> None:
        job = {"id": 7, "status": "queued", "job_kind": "archive_zip", "parsed_json": "{}"}

        with (
            mock.patch.object(downloader.db, "get_job", return_value=job),
            mock.patch.object(downloader, "queue_global_limit", return_value=3),
            mock.patch.object(downloader, "queue_per_provider_limit", return_value=1),
            mock.patch.object(downloader, "provider_key_for_job") as provider_key,
        ):
            with downloader._SCHEDULER_CONDITION:
                downloader._PENDING_JOB_IDS[:] = [7]
                downloader._ACTIVE_GLOBAL_JOBS = 0
                downloader._ACTIVE_PROVIDER_JOBS.clear()

                selected, wait_seconds = downloader.pick_next_schedulable_job_locked()

                self.assertIsNone(selected)
                self.assertIsNone(wait_seconds)
                self.assertEqual(downloader._PENDING_JOB_IDS, [])

            provider_key.assert_not_called()

    def test_same_provider_cooldown_delays_next_schedulable_job(self) -> None:
        parsed = ParsedDownload(source="generic", raw_input="https://example.test/a.bin", url="https://example.test/a.bin")
        job = {"status": "queued", "parsed_json": json.dumps(parsed.to_dict())}

        with (
            mock.patch.object(downloader.db, "get_job", return_value=job),
            mock.patch.object(downloader, "queue_global_limit", return_value=3),
            mock.patch.object(downloader, "queue_per_provider_limit", return_value=1),
            mock.patch.object(downloader, "queue_provider_cooldown_range_seconds", return_value=(2, 2)),
        ):
            with downloader._SCHEDULER_CONDITION:
                downloader._PENDING_JOB_IDS[:] = [1]
                downloader._ACTIVE_GLOBAL_JOBS = 0
                downloader._ACTIVE_PROVIDER_JOBS.clear()
                downloader._PROVIDER_COOLDOWN_UNTIL.clear()
                downloader._PROVIDER_COOLDOWN_UNTIL["generic:example.test"] = downloader.time.monotonic() + 2

                selected, wait_seconds = downloader.pick_next_schedulable_job_locked()

                self.assertIsNone(selected)
                self.assertIsNotNone(wait_seconds)
                self.assertGreater(wait_seconds, 0)
                self.assertLessEqual(wait_seconds, 2)
                self.assertEqual(downloader._PENDING_JOB_IDS, [1])

            with downloader._SCHEDULER_CONDITION:
                downloader._PENDING_JOB_IDS.clear()
                downloader._PROVIDER_COOLDOWN_UNTIL.clear()

    def test_queue_limits_are_clamped_by_hard_caps(self) -> None:
        fake_db = FakeDb({}, settings={"MAX_CONCURRENT_DOWNLOADS": "99", "QUEUE_PER_PROVIDER_LIMIT": "99"})

        with (
            mock.patch.object(downloader, "db", fake_db),
            mock.patch.dict(
                os.environ,
                {
                    "MAX_CONCURRENT_DOWNLOADS_HARD_LIMIT": "5",
                    "QUEUE_PER_PROVIDER_LIMIT_HARD_LIMIT": "2",
                },
            ),
        ):
            self.assertEqual(downloader.queue_global_limit(), 5)
            self.assertEqual(downloader.queue_per_provider_limit(), 2)

    def test_same_provider_cooldown_can_be_disabled(self) -> None:
        parsed = ParsedDownload(source="generic", raw_input="https://example.test/a.bin", url="https://example.test/a.bin")
        job = {"status": "queued", "parsed_json": json.dumps(parsed.to_dict())}

        with (
            mock.patch.object(downloader.db, "get_job", return_value=job),
            mock.patch.object(downloader, "queue_global_limit", return_value=3),
            mock.patch.object(downloader, "queue_per_provider_limit", return_value=1),
            mock.patch.object(downloader, "queue_provider_cooldown_range_seconds", return_value=(0, 0)),
        ):
            with downloader._SCHEDULER_CONDITION:
                downloader._PENDING_JOB_IDS[:] = [1]
                downloader._ACTIVE_GLOBAL_JOBS = 0
                downloader._ACTIVE_PROVIDER_JOBS.clear()
                downloader._PROVIDER_COOLDOWN_UNTIL.clear()
                downloader._PROVIDER_COOLDOWN_UNTIL["generic:example.test"] = downloader.time.monotonic() + 20

                selected, wait_seconds = downloader.pick_next_schedulable_job_locked()

                self.assertEqual(selected, (1, "generic:example.test"))
                self.assertIsNone(wait_seconds)
                self.assertEqual(downloader._PENDING_JOB_IDS, [])

            with downloader._SCHEDULER_CONDITION:
                downloader._PENDING_JOB_IDS.clear()
                downloader._PROVIDER_COOLDOWN_UNTIL.clear()

    def test_provider_cooldown_randomizes_once_when_slot_is_released(self) -> None:
        provider = "generic:example.test"
        with (
            mock.patch.object(downloader, "queue_provider_cooldown_range_seconds", return_value=(2, 7)),
            mock.patch.object(downloader.random, "uniform", return_value=4.5) as uniform,
        ):
            with downloader._SCHEDULER_CONDITION:
                downloader._ACTIVE_GLOBAL_JOBS = 1
                downloader._ACTIVE_PROVIDER_JOBS.clear()
                downloader._ACTIVE_PROVIDER_JOBS[provider] = 1
                downloader._PROVIDER_COOLDOWN_UNTIL.clear()
                before = downloader.time.monotonic()

            downloader.release_provider_slot(provider)

            with downloader._SCHEDULER_CONDITION:
                cooldown_until = downloader._PROVIDER_COOLDOWN_UNTIL[provider]
                downloader._ACTIVE_GLOBAL_JOBS = 0
                downloader._ACTIVE_PROVIDER_JOBS.clear()
                downloader._PROVIDER_COOLDOWN_UNTIL.clear()

        uniform.assert_called_once_with(2.0, 7.0)
        self.assertGreaterEqual(cooldown_until, before + 4.0)
        self.assertLessEqual(cooldown_until, before + 5.0)

    def test_huggingface_process_stops_child_when_job_is_deleted(self) -> None:
        class CapturingStdin:
            def __init__(self) -> None:
                self.value = ""
                self.closed = False

            def write(self, value: str) -> int:
                self.value += value
                return len(value)

            def close(self) -> None:
                self.closed = True

        fake_stdin = CapturingStdin()
        fake_process = mock.Mock()
        fake_process.stdin = fake_stdin
        fake_process.stdout = None
        fake_process.poll.return_value = None
        spec = {
            "mode": "snapshot",
            "repo_id": "owner/repo",
            "repo_type": "model",
            "revision": None,
            "local_dir": "/data/hf",
            "token": "secret-token",
            "allow_patterns": None,
            "ignore_patterns": None,
            "max_workers": 1,
        }

        with (
            mock.patch.object(downloader.subprocess, "Popen", return_value=fake_process) as popen,
            mock.patch.object(downloader, "check_job_control", side_effect=downloader.JobControlStop("deleted")),
            mock.patch.object(downloader, "stop_controlled_process") as stop_process,
        ):
            with self.assertRaises(downloader.JobControlStop):
                downloader.run_huggingface_download_process(77, spec, Path("/data/hf"))

        command = popen.call_args.args[0]
        self.assertEqual(
            command,
            [downloader.sys.executable, "-m", "app.downloader", "huggingface-download"],
        )
        self.assertNotIn("secret-token", command)
        self.assertTrue(fake_stdin.closed)
        self.assertEqual(json.loads(fake_stdin.value)["token"], "secret-token")
        stop_process.assert_called_once_with(77, fake_process, "HF download")

    def test_gallery_dl_process_stops_child_when_job_is_deleted(self) -> None:
        fake_process = mock.Mock()
        fake_process.stdout = None
        fake_process.poll.return_value = None

        with (
            mock.patch.object(downloader.subprocess, "Popen", return_value=fake_process),
            mock.patch.object(downloader, "check_job_control", side_effect=downloader.JobControlStop("deleted")),
            mock.patch.object(downloader, "stop_controlled_process") as stop_process,
        ):
            with self.assertRaises(downloader.JobControlStop):
                downloader.run_gallery_dl_process(88, ["gallery-dl", "ytdl:https://xhamster3.com/videos/1"], Path("/tmp"))

        stop_process.assert_called_once_with(88, fake_process, "gallery-dl")

    def test_external_process_stops_child_when_progress_update_fails(self) -> None:
        fake_process = mock.Mock()
        fake_process.stdout = None
        fake_process.poll.return_value = None

        with (
            mock.patch.object(downloader.subprocess, "Popen", return_value=fake_process),
            mock.patch.object(downloader, "check_job_control", return_value=None),
            mock.patch.object(downloader, "update_gallery_dl_progress", side_effect=RuntimeError("progress failed")),
            mock.patch.object(downloader, "stop_controlled_process") as stop_process,
        ):
            with self.assertRaisesRegex(RuntimeError, "progress failed"):
                downloader.run_external_download_process(89, ["yt-dlp", "https://example.test/video"], Path("/tmp"), "yt-dlp")

        stop_process.assert_called_once_with(89, fake_process, "yt-dlp")

    def test_request_throttle_sleep_obeys_job_control(self) -> None:
        session = mock.Mock()
        host = "example.test"
        with downloader._HOST_THROTTLE_LOCK:
            downloader._HOST_NEXT_REQUEST_AT.clear()
            downloader._HOST_NEXT_REQUEST_AT[host] = downloader.time.monotonic() + 10

        with (
            mock.patch.object(downloader, "request_interval_for_url", return_value=1.0),
            mock.patch.object(
                downloader,
                "check_job_control",
                side_effect=[None, downloader.JobControlStop("deleted")],
            ),
        ):
            with self.assertRaises(downloader.JobControlStop):
                downloader.request_with_safety(session, "GET", f"https://{host}/video", job_id=90)

        session.request.assert_not_called()
        with downloader._HOST_THROTTLE_LOCK:
            downloader._HOST_NEXT_REQUEST_AT.clear()

    def test_retry_after_header_is_not_shortened_by_max_retry_sleep(self) -> None:
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "3600"

        with mock.patch.dict(os.environ, {"DOWNLOAD_MAX_RETRY_SLEEP_SECONDS": "10"}):
            self.assertEqual(downloader.retry_delay(response, 0), 3600.0)

    def test_retry_backoff_without_header_still_uses_max_retry_sleep(self) -> None:
        response = requests.Response()
        response.status_code = 503

        with (
            mock.patch.dict(
                os.environ,
                {
                    "DOWNLOAD_RETRY_BACKOFF_SECONDS": "20",
                    "DOWNLOAD_MAX_RETRY_SLEEP_SECONDS": "10",
                },
            ),
            mock.patch.object(downloader.random, "uniform", return_value=0.0),
        ):
            self.assertEqual(downloader.retry_delay(response, 0), 10.0)

    def test_process_output_queue_is_bounded(self) -> None:
        with mock.patch.dict(os.environ, {"PROCESS_OUTPUT_QUEUE_MAX_LINES": "7"}):
            self.assertEqual(downloader.process_output_queue().maxsize, 7)

    def test_civitai_image_resource_job_creation_obeys_limit(self) -> None:
        fake_db = FakeDb({})
        resources = [
            {"name": "A", "model_id": "10", "model_version_id": "101"},
            {"name": "B", "model_id": "20", "model_version_id": "202"},
            {"name": "C", "model_id": "30", "model_version_id": "303"},
        ]

        with (
            mock.patch.object(downloader, "db", fake_db),
            mock.patch.object(downloader, "civitai_existing_resource_state", return_value=None),
            mock.patch.object(downloader, "enqueue_job") as enqueue_job,
            mock.patch.dict(os.environ, {"CIVITAI_IMAGE_MAX_RESOURCE_JOBS": "2"}),
        ):
            entries = downloader.create_civitai_image_resource_jobs(55, resources)

        self.assertEqual([entry["status"] for entry in entries], ["queued", "queued", "skipped_limit"])
        self.assertEqual([job.civitai_version_id for job in fake_db.created_jobs], ["101", "202"])
        self.assertEqual([call.args[0] for call in enqueue_job.call_args_list], [501, 502])

    def test_gallery_dl_ytdl_target_parts_unwrap_youtube_url(self) -> None:
        host, slug = downloader.gallery_dl_target_parts("ytdl:https://www.youtube.com/watch?v=abc123&t=30")

        self.assertEqual(host, "youtube.com")
        self.assertEqual(slug, "video-abc123")

    def test_gallery_dl_ytdl_target_parts_canonicalizes_youtu_be_host(self) -> None:
        host, slug = downloader.gallery_dl_target_parts("ytdl:https://youtu.be/abc123?t=30")

        self.assertEqual(host, "youtube.com")
        self.assertEqual(slug, "video-abc123")

    def test_youtube_provider_key_uses_canonical_youtube_bucket(self) -> None:
        parsed = ParsedDownload(
            source="gallerydl",
            raw_input="https://youtu.be/abc123",
            gallerydl_url="ytdl:https://youtu.be/abc123",
        )

        self.assertEqual(downloader.provider_key_for_parsed(parsed), "gallerydl:youtube.com")

    def test_gallery_dl_command_passes_ytdlp_cookies_without_gallery_auth_collision(self) -> None:
        fake_db = FakeDb(
            {},
            secrets={
                "GALLERY_DL_COOKIES_FILE": "/config/gallery-cookies.txt",
                "YT_DLP_COOKIES_FILE": "/config/yt-dlp-cookies.txt",
                "YT_DLP_COOKIES_FROM_BROWSER": "firefox:default",
                "YT_DLP_EXTRA_OPTIONS": "--format bestaudio/best\nraw-options.writesubtitles=true",
            },
        )
        with mock.patch.object(downloader, "db", fake_db):
            command = downloader.gallery_dl_command(
                "ytdl:https://www.youtube.com/watch?v=abc123",
                Path("/downloads/youtube"),
            )

        options = {
            command[index + 1].split("=", 1)[0]: command[index + 1].split("=", 1)[1]
            for index, value in enumerate(command)
            if value == "-o"
        }
        self.assertIn("--cookies", command)
        self.assertIn("/config/gallery-cookies.txt", command)
        self.assertNotEqual(
            command[command.index("--cookies") + 1],
            "/config/yt-dlp-cookies.txt",
        )
        self.assertEqual(json.loads(options["extractor.ytdl.enabled"]), True)
        self.assertEqual(options["extractor.ytdl.module"], "yt_dlp")
        self.assertEqual(options["downloader.ytdl.module"], "yt_dlp")
        cmdline_args = json.loads(options["extractor.ytdl.cmdline-args"])
        self.assertIn("/config/yt-dlp-cookies.txt", cmdline_args)
        self.assertIn("firefox:default", cmdline_args)
        self.assertIn("bestaudio/best", cmdline_args)
        self.assertEqual(json.loads(options["extractor.ytdl.raw-options.writesubtitles"]), True)

    def test_gallery_dl_command_enables_ytdlp_for_direct_youtube_url(self) -> None:
        with mock.patch.object(downloader, "db", FakeDb({})):
            command = downloader.gallery_dl_command("https://www.youtube.com/watch?v=abc123", Path("/downloads/youtube"))

        self.assertIn("extractor.ytdl.enabled=true", command)
        self.assertEqual(command[-1], "ytdl:https://www.youtube.com/watch?v=abc123")

    def test_gallery_dl_command_enables_ytdlp_for_direct_xhamster_url(self) -> None:
        url = "https://xhamster3.com/videos/sample-video-123456"

        with mock.patch.object(downloader, "db", FakeDb({})):
            command = downloader.gallery_dl_command(url, Path("/downloads/xhamster"))

        self.assertIn("extractor.ytdl.enabled=true", command)
        options = {
            command[index + 1].split("=", 1)[0]: command[index + 1].split("=", 1)[1]
            for index, value in enumerate(command)
            if value == "-o"
        }
        cmdline_args = json.loads(options["extractor.ytdl.cmdline-args"])
        self.assertIn("--impersonate", cmdline_args)
        self.assertIn("chrome", cmdline_args)
        self.assertIn("--referer", cmdline_args)
        self.assertIn("https://xhamster3.com/", cmdline_args)
        self.assertIn("--playlist-items", cmdline_args)
        self.assertIn("1", cmdline_args)
        self.assertIn("--force-ipv4", cmdline_args)
        self.assertEqual(command[-1], f"ytdl:{url}")

    def test_gallery_dl_command_passes_dedicated_ytdlp_proxy(self) -> None:
        fake_db = FakeDb({}, secrets={"YT_DLP_PROXY": "socks5://192.168.200.100:1080"})

        with mock.patch.object(downloader, "db", fake_db):
            command = downloader.gallery_dl_command(
                "ytdl:https://www.youtube.com/watch?v=abc123",
                Path("/downloads/youtube"),
            )

        options = {
            command[index + 1].split("=", 1)[0]: command[index + 1].split("=", 1)[1]
            for index, value in enumerate(command)
            if value == "-o"
        }
        cmdline_args = json.loads(options["extractor.ytdl.cmdline-args"])
        self.assertIn("--proxy", cmdline_args)
        self.assertEqual(cmdline_args[cmdline_args.index("--proxy") + 1], "socks5://192.168.200.100:1080")

    def test_ytdlp_extra_proxy_prevents_dedicated_proxy_duplicate(self) -> None:
        fake_db = FakeDb(
            {},
            secrets={
                "YT_DLP_PROXY": "socks5://192.168.200.100:1080",
                "YT_DLP_EXTRA_OPTIONS": "cmdline-args=--proxy socks5://legacy-proxy:1080",
            },
        )

        with mock.patch.object(downloader, "db", fake_db):
            args = downloader.ytdlp_direct_cmdline_args()

        self.assertEqual(args.count("--proxy"), 1)
        self.assertEqual(args[args.index("--proxy") + 1], "socks5://legacy-proxy:1080")

    def test_yt_dlp_command_uses_direct_cli_with_auth_and_format(self) -> None:
        fake_db = FakeDb(
            {},
            secrets={
                "YT_DLP_COOKIES_FILE": "/config/yt-dlp-cookies.txt",
                "YT_DLP_COOKIES_FROM_BROWSER": "firefox:default",
                "YT_DLP_PROXY": "socks5://192.168.200.100:1080",
                "YT_DLP_FORMAT": "best[ext=mp4]/best",
                "YT_DLP_EXTRA_OPTIONS": "cmdline-args=--playlist-items 1",
            },
        )
        with (
            mock.patch.object(downloader, "db", fake_db),
            mock.patch.object(downloader.shutil, "which", return_value=None),
        ):
            command = downloader.yt_dlp_command(
                "ytdl:https://xhamster3.com/videos/sample-video-123456",
                Path("/downloads/xhamster"),
            )

        self.assertEqual(command[:3], [downloader.sys.executable, "-m", "yt_dlp"])
        self.assertIn("--no-config", command)
        self.assertIn("--socket-timeout", command)
        self.assertEqual(command[command.index("--socket-timeout") + 1], "30")
        self.assertIn("--extractor-retries", command)
        self.assertEqual(command[command.index("--extractor-retries") + 1], "3")
        self.assertIn("--retries", command)
        self.assertEqual(command[command.index("--retries") + 1], "3")
        self.assertIn("--impersonate", command)
        self.assertEqual(command[command.index("--impersonate") + 1], "chrome")
        self.assertIn("--referer", command)
        self.assertEqual(command[command.index("--referer") + 1], "https://xhamster3.com/")
        self.assertEqual(command.count("--playlist-items"), 1)
        self.assertIn("--force-ipv4", command)
        self.assertIn("--paths", command)
        self.assertIn("/downloads/xhamster", command)
        self.assertIn("--format", command)
        self.assertEqual(command[command.index("--format") + 1], "best[ext=mp4]/best")
        self.assertIn("--cookies", command)
        self.assertIn("/config/yt-dlp-cookies.txt", command)
        self.assertIn("--cookies-from-browser", command)
        self.assertIn("firefox:default", command)
        self.assertIn("--proxy", command)
        self.assertEqual(command[command.index("--proxy") + 1], "socks5://192.168.200.100:1080")
        self.assertIn("--playlist-items", command)
        self.assertIn("1", command)
        self.assertEqual(command[-1], "https://xhamster3.com/videos/sample-video-123456")

    def test_yt_dlp_command_downloads_manual_youtube_subtitles_first(self) -> None:
        with (
            mock.patch.object(downloader, "db", FakeDb({})),
            mock.patch.object(downloader.shutil, "which", return_value=None),
            mock.patch.object(
                downloader,
                "yt_dlp_subtitle_info",
                return_value={
                    "subtitles": {"en": [], "ko": []},
                    "automatic_captions": {"en": [], "ko": []},
                },
            ),
        ):
            command = downloader.yt_dlp_command(
                "ytdl:https://www.youtube.com/watch?v=abc123",
                Path("/downloads/youtube"),
            )

        self.assertIn("--write-subs", command)
        self.assertNotIn("--write-auto-subs", command)
        self.assertIn("--sub-langs", command)
        self.assertEqual(command[command.index("--sub-langs") + 1], "ko,en")
        self.assertIn("--convert-subs", command)
        self.assertEqual(command[command.index("--convert-subs") + 1], "srt")
        self.assertIn("--ignore-errors", command)

    def test_yt_dlp_command_falls_back_to_auto_english_subtitles(self) -> None:
        with (
            mock.patch.object(downloader, "db", FakeDb({})),
            mock.patch.object(downloader.shutil, "which", return_value=None),
            mock.patch.object(
                downloader,
                "yt_dlp_subtitle_info",
                return_value={"subtitles": {}, "automatic_captions": {"en": [], "ko": []}},
            ),
        ):
            command = downloader.yt_dlp_command(
                "https://youtu.be/abc123",
                Path("/downloads/youtube"),
            )

        self.assertIn("--write-auto-subs", command)
        self.assertNotIn("--write-subs", command)
        self.assertEqual(command[command.index("--sub-langs") + 1], "en")
        self.assertIn("--ignore-errors", command)

    def test_yt_dlp_command_keeps_explicit_subtitle_options(self) -> None:
        fake_db = FakeDb({}, secrets={"YT_DLP_EXTRA_OPTIONS": "cmdline-args=--write-auto-subs --sub-langs ja"})

        with (
            mock.patch.object(downloader, "db", fake_db),
            mock.patch.object(downloader.shutil, "which", return_value=None),
            mock.patch.object(downloader, "yt_dlp_subtitle_info") as probe,
        ):
            command = downloader.yt_dlp_command(
                "https://www.youtube.com/watch?v=abc123",
                Path("/downloads/youtube"),
            )

        probe.assert_not_called()
        self.assertEqual(command.count("--write-auto-subs"), 1)
        self.assertEqual(command[command.index("--sub-langs") + 1], "ja")

    def test_yt_dlp_command_keeps_explicit_error_policy_for_default_subtitles(self) -> None:
        fake_db = FakeDb({}, secrets={"YT_DLP_EXTRA_OPTIONS": "cmdline-args=--abort-on-error"})

        with (
            mock.patch.object(downloader, "db", fake_db),
            mock.patch.object(downloader.shutil, "which", return_value=None),
            mock.patch.object(
                downloader,
                "yt_dlp_subtitle_info",
                return_value={"subtitles": {"en": []}, "automatic_captions": {}},
            ),
        ):
            command = downloader.yt_dlp_command(
                "https://www.youtube.com/watch?v=abc123",
                Path("/downloads/youtube"),
            )

        self.assertIn("--write-subs", command)
        self.assertIn("--abort-on-error", command)
        self.assertNotIn("--ignore-errors", command)

    def test_stall_watchdog_defaults_to_enabled(self) -> None:
        with mock.patch.object(downloader, "db", FakeDb({})):
            self.assertEqual(
                downloader.queue_provider_cooldown_range_seconds(),
                (
                    downloader.QUEUE_PROVIDER_COOLDOWN_MIN_DEFAULT_SECONDS,
                    downloader.QUEUE_PROVIDER_COOLDOWN_MAX_DEFAULT_SECONDS,
                ),
            )
            self.assertEqual(downloader.queue_stall_timeout_seconds(), downloader.DOWNLOAD_STALL_TIMEOUT_DEFAULT_SECONDS)

    def test_ytdl_command_uses_yt_dlp_module_and_settings(self) -> None:
        fake_db = mock.Mock()
        fake_db.get_setting.return_value = "worst[ext=mp4]/worst"
        fake_db.get_secret.side_effect = lambda key: {
            "YT_DLP_COOKIES_FILE": "/config/yt-dlp/cookies.txt",
            "YT_DLP_COOKIES_FROM_BROWSER": "",
            "YT_DLP_FORMAT": "worst[ext=mp4]/worst",
            "YT_DLP_EXTRA_OPTIONS": "filesize-max=100M\ncmdline-args=--playlist-items 1",
        }.get(key)

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(downloader, "db", fake_db),
            mock.patch.object(downloader.shutil, "which", return_value=None),
        ):
            command = downloader.gallery_dl_command("ytdl:https://www.youtube.com/watch?v=abc123", Path(tmp))

        self.assertIn("extractor.ytdl.module=yt_dlp", command)
        self.assertIn("extractor.ytdl.format=worst[ext=mp4]/worst", command)
        self.assertIn("extractor.ytdl.raw-options.filesize-max=100M", command)
        self.assertIn('extractor.ytdl.cmdline-args=["--cookies", "/config/yt-dlp/cookies.txt", "--playlist-items", "1"]', command)
        self.assertTrue(any("/config/yt-dlp/cookies.txt" in item for item in command))
        self.assertEqual(command[-1], "ytdl:https://www.youtube.com/watch?v=abc123")

    def test_ytdl_command_adds_deno_js_runtime_when_available(self) -> None:
        with (
            mock.patch.object(downloader, "db", FakeDb({})),
            mock.patch.object(downloader.shutil, "which", return_value="/usr/local/bin/deno"),
        ):
            command = downloader.gallery_dl_command("ytdl:https://www.youtube.com/watch?v=abc123", Path("/downloads"))

        options = {
            command[index + 1].split("=", 1)[0]: command[index + 1].split("=", 1)[1]
            for index, value in enumerate(command)
            if value == "-o"
        }
        cmdline_args = json.loads(options["extractor.ytdl.cmdline-args"])
        self.assertIn("--js-runtimes", cmdline_args)
        self.assertIn("deno", cmdline_args)

    def test_ytdl_command_keeps_explicit_js_runtime(self) -> None:
        fake_db = FakeDb({}, secrets={"YT_DLP_EXTRA_OPTIONS": "cmdline-args=--js-runtimes deno:/opt/deno"})
        with (
            mock.patch.object(downloader, "db", fake_db),
            mock.patch.object(downloader.shutil, "which", return_value="/usr/bin/node"),
        ):
            command = downloader.gallery_dl_command("ytdl:https://www.youtube.com/watch?v=abc123", Path("/downloads"))

        options = {
            command[index + 1].split("=", 1)[0]: command[index + 1].split("=", 1)[1]
            for index, value in enumerate(command)
            if value == "-o"
        }
        cmdline_args = json.loads(options["extractor.ytdl.cmdline-args"])
        self.assertIn("deno:/opt/deno", cmdline_args)
        self.assertEqual(cmdline_args.count("--js-runtimes"), 1)

    def test_ytdl_extra_options_block_config_overrides(self) -> None:
        for option in (
            "cmdline-args=--config-locations /tmp/evil.conf",
            "config-file=/tmp/evil.conf",
            "extractor.ytdl.config-file=/tmp/evil.conf",
        ):
            fake_db = FakeDb({}, secrets={"YT_DLP_EXTRA_OPTIONS": option})
            with mock.patch.object(downloader, "db", fake_db):
                with self.assertRaises(ValueError):
                    downloader.gallery_dl_command("ytdl:https://www.youtube.com/watch?v=abc123", Path("/downloads"))

    def test_ytdl_extra_options_block_path_exec_and_loader_overrides(self) -> None:
        for option in (
            "cmdline-args=--output /tmp/%(id)s.%(ext)s",
            "cmdline-args=--paths /tmp",
            "cmdline-args=--exec echo %(filepath)q",
            "cmdline-args=--plugin-dirs /tmp/plugins",
            "cmdline-args=--external-downloader /bin/sh",
            "cmdline-args=--postprocessor-args ffmpeg:-y",
            "cmdline-args=--ffmpeg-location /tmp/ffmpeg",
            "raw-options.outtmpl=/tmp/%(id)s.%(ext)s",
            "raw-options.paths={\"home\":\"/tmp\"}",
            "raw-options.exec_cmd=touch /tmp/pwned",
            "downloader.ytdl.outtmpl=/tmp/%(id)s.%(ext)s",
        ):
            fake_db = FakeDb({}, secrets={"YT_DLP_EXTRA_OPTIONS": option})
            with mock.patch.object(downloader, "db", fake_db):
                with self.assertRaises(ValueError):
                    downloader.gallery_dl_command("ytdl:https://www.youtube.com/watch?v=abc123", Path("/downloads"))

    def test_youtube_slug_handles_watch_short_and_embed_urls(self) -> None:
        self.assertEqual(downloader.youtube_slug(downloader.urlparse("https://www.youtube.com/watch?v=abc123")), "video-abc123")
        self.assertEqual(downloader.youtube_slug(downloader.urlparse("https://youtu.be/shortid")), "video-shortid")
        self.assertEqual(downloader.youtube_slug(downloader.urlparse("https://youtube.com/embed/embedid")), "embed-embedid")


if __name__ == "__main__":
    unittest.main()
