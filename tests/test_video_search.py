"""视频搜索脚本的离线单元测试。"""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = (
    Path(__file__).parents[1]
    / "video-search"
    / "scripts"
    / "video_search.py"
)
SPEC = importlib.util.spec_from_file_location("video_search", SCRIPT)
VIDEO_SEARCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VIDEO_SEARCH)


class VideoSearchTest(unittest.TestCase):
    """验证编号、许可过滤和播放器生成。"""

    def test_parse_multiple_maccms_routes(self):
        item = {
            "vod_play_from": "线路甲$$$线路乙",
            "vod_play_url": (
                "第1集$https://example.com/1.m3u8#第2集$https://example.com/2.m3u8"
                "$$$正片$https://example.com/movie.mp4"
            ),
        }

        routes = VIDEO_SEARCH.parse_play_routes(item)

        self.assertEqual([1, 2], [route["index"] for route in routes])
        self.assertEqual("第2集", routes[0]["episodes"][1]["name"])
        self.assertEqual("线路乙", routes[1]["name"])

    def test_archive_files_requires_license(self):
        payload = {"metadata": {"title": "未标注许可"}, "files": []}

        with patch.object(VIDEO_SEARCH, "request_json", return_value=payload):
            with self.assertRaisesRegex(ValueError, "开放许可"):
                VIDEO_SEARCH.archive_files("valid-item")

    def test_archive_files_filters_and_numbers_media(self):
        payload = {
            "metadata": {
                "title": "测试视频",
                "licenseurl": "https://creativecommons.org/licenses/by/4.0/",
            },
            "files": [
                {"name": "readme.txt", "size": "10"},
                {"name": "private.mp4", "private": True, "size": "20"},
                {"name": "video.webm", "size": "2048", "format": "WebM"},
                {"name": "video.mp4", "size": "1024", "format": "MPEG4"},
            ],
        }

        with patch.object(VIDEO_SEARCH, "request_json", return_value=payload):
            result = VIDEO_SEARCH.archive_files("valid-item")

        self.assertEqual([1, 2], [item["index"] for item in result["files"]])
        self.assertEqual("video.mp4", result["files"][0]["name"])
        self.assertEqual("video.webm", result["files"][1]["name"])

    def test_player_escapes_title_and_embeds_url_as_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "player.html"
            VIDEO_SEARCH.render_player(
                "<测试>", "https://archive.org/download/item/video.mp4", str(output)
            )
            content = output.read_text(encoding="utf-8")

        self.assertIn("<title>&lt;测试&gt;</title>", content)
        self.assertIn('const title = "<测试>";', content)
        self.assertIn("const isHls", content)

    def test_files_uses_displayed_search_index(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "search.json"
            catalog.write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "index": 1,
                                "source": "Internet Archive",
                                "source_id": "first-item",
                            },
                            {
                                "index": 2,
                                "source": "Internet Archive",
                                "source_id": "second-item",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(
                VIDEO_SEARCH, "archive_files", return_value={"source_id": "second-item"}
            ) as mocked:
                result = VIDEO_SEARCH.archive_files_from_search(str(catalog), 2)

        mocked.assert_called_once_with("second-item", None)
        self.assertEqual("second-item", result["source_id"])

    def test_remote_cms_requires_https(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            VIDEO_SEARCH.validate_cms_api_url(
                "http://media.example.com/api.php/provide/vod/"
            )

    def test_cms_config_skips_source_without_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "sources.json"
            config.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "name": "本地测试",
                                "api_url": "http://127.0.0.1:8766/api.php/provide/vod/",
                                "authorized": True,
                            },
                            {
                                "name": "未授权",
                                "api_url": "https://example.com/api.php/provide/vod/",
                                "authorized": False,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            sources, skipped = VIDEO_SEARCH.load_cms_sources(str(config))

        self.assertEqual(["本地测试"], [item["name"] for item in sources])
        self.assertEqual("未授权", skipped[0]["source"])

    def test_resolve_cms_config_uses_environment_variable(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "sources.json"
            config.write_text('{"sources": []}', encoding="utf-8")
            with patch.dict(os.environ, {"VIDEO_SEARCH_SOURCES": str(config)}):
                resolved = VIDEO_SEARCH.resolve_cms_config()

        self.assertEqual(str(config.resolve()), resolved)

    def test_search_command_defaults_to_zip0(self):
        args = VIDEO_SEARCH.build_parser().parse_args(["search", "测试影片"])

        self.assertEqual("zip0", args.source)

    def test_zip0_search_numbers_results(self):
        payload = {
            "success": True,
            "data": [
                {
                    "title": "测试电影",
                    "year": "2026",
                    "category": "剧情片",
                    "episodeCount": 1,
                    "url": "https://zip0.com/watch?source=test&id=42&episode=1",
                }
            ],
            "pagination": {"total": 1},
            "sources": {"completed": 1, "total": 1},
        }
        with patch.object(VIDEO_SEARCH, "request_json", return_value=payload):
            result = VIDEO_SEARCH.search_zip0("测试电影")

        self.assertEqual(1, result["results"][0]["index"])
        self.assertEqual("test", result["results"][0]["line"])
        self.assertEqual(1, result["results"][0]["episode_count"])

    def test_zip0_search_rejects_external_watch_url(self):
        payload = {
            "success": True,
            "data": [
                {
                    "title": "异常结果",
                    "url": "https://example.com/watch?source=test&id=42",
                }
            ],
        }
        with patch.object(VIDEO_SEARCH, "request_json", return_value=payload):
            result = VIDEO_SEARCH.search_zip0("异常结果")

        self.assertEqual([], result["results"])
        self.assertEqual(1, len(result["skipped"]))

    def test_select_zip0_result_changes_episode(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "search.json"
            catalog.write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "index": 1,
                                "source": "ZIP0",
                                "line": "test",
                                "title": "测试剧",
                                "episode_count": 3,
                                "url": "https://zip0.com/watch?source=test&id=42&episode=1",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = VIDEO_SEARCH.select_zip0_result(str(catalog), 1, 2)

        self.assertIn("episode=2", result["open_url"])
        self.assertEqual("测试剧", result["title"])

    def test_cms_detail_keeps_route_and_episode_numbers(self):
        search_payload = {
            "results": [
                {
                    "index": 1,
                    "source": "测试源",
                    "source_id": 101,
                    "title": "示例剧",
                    "api_url": "https://example.com/api.php/provide/vod/",
                }
            ]
        }
        detail_payload = {
            "list": [
                {
                    "vod_id": 101,
                    "vod_name": "示例剧",
                    "vod_play_from": "线路一$$$线路二",
                    "vod_play_url": (
                        "第1集$https://example.com/1.m3u8"
                        "$$$正片$https://example.com/movie.mp4"
                    ),
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "search.json"
            catalog.write_text(json.dumps(search_payload), encoding="utf-8")
            with patch.object(VIDEO_SEARCH, "request_json", return_value=detail_payload):
                result = VIDEO_SEARCH.cms_detail_from_search(str(catalog), 1)

        self.assertEqual([1, 2], [route["index"] for route in result["routes"]])
        self.assertEqual(1, result["routes"][1]["episodes"][0]["index"])


if __name__ == "__main__":
    unittest.main()
