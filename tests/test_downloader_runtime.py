from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import downloader
from app.models import ParsedDownload


class FakeDb:
    def __init__(
        self,
        job: dict,
        secrets: dict[str, str] | None = None,
        settings: dict[str, str] | None = None,
    ) -> None:
        self.job = job
        self.secrets = secrets or {}
        self.settings = settings or {}
        self.logs: list[str] = []

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

    def test_gallery_dl_ytdl_target_parts_unwrap_youtube_url(self) -> None:
        host, slug = downloader.gallery_dl_target_parts("ytdl:https://www.youtube.com/watch?v=abc123&t=30")

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
        self.assertEqual(command[-1], f"ytdl:{url}")

    def test_yt_dlp_command_uses_direct_cli_with_auth_and_format(self) -> None:
        fake_db = FakeDb(
            {},
            secrets={
                "YT_DLP_COOKIES_FILE": "/config/yt-dlp-cookies.txt",
                "YT_DLP_COOKIES_FROM_BROWSER": "firefox:default",
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
        self.assertIn("--paths", command)
        self.assertIn("/downloads/xhamster", command)
        self.assertIn("--format", command)
        self.assertEqual(command[command.index("--format") + 1], "best[ext=mp4]/best")
        self.assertIn("--cookies", command)
        self.assertIn("/config/yt-dlp-cookies.txt", command)
        self.assertIn("--cookies-from-browser", command)
        self.assertIn("firefox:default", command)
        self.assertIn("--playlist-items", command)
        self.assertIn("1", command)
        self.assertEqual(command[-1], "https://xhamster3.com/videos/sample-video-123456")

    def test_stall_watchdog_defaults_to_enabled(self) -> None:
        with mock.patch.object(downloader, "db", FakeDb({})):
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
