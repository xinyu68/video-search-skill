"""本机 HLS 播放器的离线单元测试。"""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = (
    Path(__file__).parents[1]
    / "video-search"
    / "scripts"
    / "local_hls_player.py"
)
SPEC = importlib.util.spec_from_file_location("local_hls_player", SCRIPT)
PLAYER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLAYER)


class LocalHlsPlayerTest(unittest.TestCase):
    """验证 URL 边界和播放清单改写。"""

    def test_zip0_watch_url_rejects_external_host(self):
        with self.assertRaisesRegex(ValueError, "ZIP0"):
            PLAYER.validate_zip0_watch_url(
                "https://example.com/watch?source=test&id=1&episode=1"
            )

    def test_rewrite_manifest_uses_local_tokens(self):
        guard = PLAYER.PublicUrlGuard()
        registry = PLAYER.UrlRegistry(guard)
        manifest = (
            "#EXTM3U\n"
            "#EXT-X-KEY:METHOD=AES-128,URI=\"keys/key.bin\"\n"
            "segment-1.ts\n"
        )
        with patch.object(guard, "validate", side_effect=lambda url: url):
            rewritten = PLAYER.rewrite_manifest(
                manifest, "https://media.example.com/path/index.m3u8", registry
            )

        self.assertNotIn("media.example.com", rewritten)
        self.assertNotIn("segment-1.ts", rewritten)
        self.assertEqual(2, rewritten.count("/media/"))
        self.assertIn(".bin", rewritten)
        self.assertIn(".ts", rewritten)

    def test_render_player_loads_local_manifest(self):
        page = PLAYER.render_player("测试影片").decode("utf-8")
        script = PLAYER.render_player_script().decode("utf-8")

        self.assertIn("/player.js", page)
        self.assertIn("hls.min.js", page)
        self.assertIn("测试影片", page)
        self.assertIn("/master.m3u8", script)
        self.assertIn("enableWorker: false", script)
        self.assertIn("视频已缓冲", script)
        self.assertNotIn("播放器已就绪", script)
        self.assertIn("HOLD_DELAY_MS = 450", script)
        self.assertIn("已前进 10 秒", script)
        self.assertIn("长按倒退：2 倍速", script)
        self.assertIn("长按快进：2 倍速", script)
        self.assertIn("短按后退/前进 10 秒", page)
        self.assertIn("window.addEventListener('keydown'", script)
        self.assertIn("event.stopPropagation()", script)
        self.assertIn("快进 2×", script)
        self.assertIn("event.keyCode === 39", script)
        self.assertIn("seekBy(direction * SEEK_SECONDS)", script)
        self.assertLess(
            script.index("window.Hls && Hls.isSupported()"),
            script.index("video.canPlayType('application/vnd.apple.mpegurl')"),
        )

    def test_open_system_browser_uses_default_browser(self):
        with patch.object(PLAYER.webbrowser, "open", return_value=True) as browser_open:
            opened = PLAYER.open_system_browser("http://127.0.0.1:12345/")

        self.assertTrue(opened)
        browser_open.assert_called_once_with("http://127.0.0.1:12345/", new=2)


if __name__ == "__main__":
    unittest.main()
