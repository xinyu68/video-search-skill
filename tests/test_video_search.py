"""视频搜索脚本的离线单元测试。"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "video-search" / "scripts" / "video_search.py"
SPEC = importlib.util.spec_from_file_location("video_search", SCRIPT)
VIDEO_SEARCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VIDEO_SEARCH)


class VideoSearchTest(unittest.TestCase):
    """验证搜索结果、编号选择和站内地址校验。"""

    def test_search_command_defaults_to_zip0(self):
        parser = VIDEO_SEARCH.build_parser()
        self.assertEqual("zip0", parser.parse_args(["search", "测试影片"]).source)

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
        }
        with patch.object(VIDEO_SEARCH, "request_json", return_value=payload):
            result = VIDEO_SEARCH.search_zip0("测试电影")

        self.assertEqual(1, result["results"][0]["index"])
        self.assertEqual("test", result["results"][0]["line"])

    def test_zip0_search_rejects_external_url(self):
        payload = {
            "success": True,
            "data": [{"title": "异常结果", "url": "https://example.com/watch?id=42"}],
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
                        "source": "ZIP0",
                        "results": [
                            {
                                "index": 1,
                                "source": "ZIP0",
                                "title": "测试剧",
                                "episode_count": 3,
                                "url": "https://zip0.com/watch?source=test&id=42&episode=1",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = VIDEO_SEARCH.select_zip0_result(str(catalog), 1, 2)

        self.assertIn("episode=2", result["open_url"])

    def test_parse_juzong_search_filters_unrelated_results(self):
        page = """
        <a href="/voddetail/5182/" title="疯狂动物城2"><span>1080P</span></a>
        <a href="/voddetail/99/" title="无关影片">HD</a>
        """
        results = VIDEO_SEARCH.parse_juzong_search_html(page, "疯狂动物城2", 10)

        self.assertEqual(1, len(results))
        self.assertEqual("疯狂动物城2", results[0]["title"])

    def test_parse_juzong_detail_numbers_options(self):
        page = """
        <a href="/vodplay/5182-1-1/">英语</a>
        <a href="/vodplay/5182-1-2/">国语</a>
        <a href="https://example.com/video">外部链接</a>
        """
        options = VIDEO_SEARCH.parse_juzong_detail_html(page)

        self.assertEqual([1, 2], [item["index"] for item in options])
        self.assertEqual(["英语", "国语"], [item["label"] for item in options])

    def test_select_juzong_rejects_external_url(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "detail.json"
            catalog.write_text(
                json.dumps(
                    {
                        "source": "剧踪影院",
                        "options": [
                            {
                                "index": 1,
                                "label": "正片",
                                "open_url": "https://example.com/vodplay/1-1-1/",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "非本站"):
                VIDEO_SEARCH.select_juzong_option(str(catalog), 1)

    def test_open_default_browser_uses_system_browser(self):
        result = {"open_url": "https://zip0.com/watch?source=test&id=42"}
        with patch.object(VIDEO_SEARCH.webbrowser, "open_new_tab", return_value=True) as mocked:
            opened = VIDEO_SEARCH.open_default_browser(result)

        mocked.assert_called_once_with(result["open_url"])
        self.assertTrue(opened["browser_opened"])


if __name__ == "__main__":
    unittest.main()
