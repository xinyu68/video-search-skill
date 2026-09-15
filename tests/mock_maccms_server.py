"""用于端到端测试的本地苹果 CMS V10 模拟服务。"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


SEARCH_ITEMS = [
    {
        "vod_id": 101,
        "vod_name": "示例科幻剧",
        "vod_year": "2026",
        "type_name": "连续剧",
        "vod_remarks": "全2集",
    },
    {
        "vod_id": 102,
        "vod_name": "示例科幻剧：幕后",
        "vod_year": "2026",
        "type_name": "纪录片",
        "vod_remarks": "正片",
    },
]


DETAIL_ITEMS = {
    "101": {
        **SEARCH_ITEMS[0],
        "vod_play_from": "公开HLS$$$公开MP4",
        "vod_play_url": (
            "第1集$https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"
            "#第2集$https://test-streams.mux.dev/test_001/stream.m3u8"
            "$$$正片$https://archive.org/download/BigBuckBunny_124/"
            "Content/big_buck_bunny_720p_surround.mp4"
        ),
    },
    "102": {
        **SEARCH_ITEMS[1],
        "vod_play_from": "公开HLS",
        "vod_play_url": "正片$https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
    },
}


class Handler(BaseHTTPRequestHandler):
    """返回最小苹果 CMS 搜索和详情响应。"""

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api.php/provide/vod/":
            self.send_error(404)
            return
        query = parse_qs(parsed.query)
        ids = (query.get("ids") or [None])[0]
        keyword = (query.get("wd") or [""])[0]
        if ids:
            items = [DETAIL_ITEMS[ids]] if ids in DETAIL_ITEMS else []
        else:
            items = [item for item in SEARCH_ITEMS if keyword in item["vod_name"]]
        body = json.dumps(
            {
                "code": 1,
                "msg": "数据列表",
                "page": 1,
                "pagecount": 1,
                "limit": 20,
                "total": len(items),
                "list": items,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message, *args):
        return


def main():
    parser = argparse.ArgumentParser(description="启动本地苹果 CMS 模拟服务")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
