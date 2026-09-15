#!/usr/bin/env python3
"""搜索第三方影视网站，并用系统默认浏览器打开站内播放页。"""

import argparse
import html as html_utils
import json
import re
import ssl
import sys
import time
import webbrowser
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener, urlopen


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


HEADERS = {"User-Agent": "video-search-skill/0.4"}
ZIP0_SEARCH_URL = "https://zip0.com/api/videos/search"
ZIP0_HOSTS = {"zip0.com", "www.zip0.com"}
JUZONG_BASE_URL = "https://www.juzong01.me"
JUZONG_HOSTS = {"juzong01.me", "www.juzong01.me"}
JUZONG_SEARCH_DELAY_SECONDS = 6.2
JUZONG_DETAIL_PATTERN = re.compile(r"^/voddetail/\d+/$")
JUZONG_PLAY_PATTERN = re.compile(r"^/vodplay/(\d+)-(\d+)-(\d+)/$")


def create_ssl_context() -> ssl.SSLContext:
    """优先使用 certifi，同时保持 HTTPS 证书校验。"""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def request_json(url: str, params: Dict[str, Any] = None) -> Any:
    """请求公开 JSON 接口。"""
    query = f"?{urlencode(params, doseq=True)}" if params else ""
    request = Request(f"{url}{query}", headers=HEADERS)
    with urlopen(request, timeout=20, context=create_ssl_context()) as response:
        return json.load(response)


def normalize_text(value: Any) -> str:
    """把接口字段转换为便于展示的文本。"""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value or "")


def strip_html(value: Any) -> str:
    """移除页面文本中的简单 HTML 标记。"""
    text = re.sub(r"<[^>]+>", " ", normalize_text(value))
    return re.sub(r"\s+", " ", html_utils.unescape(text)).strip()


def write_catalog(path: str, payload: Dict[str, Any]) -> None:
    """保存候选，供后续按用户可见编号选择。"""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_catalog(path: str) -> Dict[str, Any]:
    """读取搜索或详情目录。"""
    with Path(path).open("r", encoding="utf-8-sig") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("目录文件格式无效")
    return payload


def validate_zip0_watch_url(url: str) -> str:
    """只允许打开 ZIP0 自己的 HTTPS 观看页。"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ZIP0_HOSTS:
        raise ValueError("ZIP0 搜索结果包含非本站 HTTPS 地址")
    if parsed.path != "/watch":
        raise ValueError("ZIP0 搜索结果不是观看页")
    query = parse_qs(parsed.query)
    if not query.get("source") or not query.get("id"):
        raise ValueError("ZIP0 观看页缺少来源或条目编号")
    return url


def search_zip0(
    keyword: str, page: int = 1, limit: int = 10, catalog: str = None
) -> Dict[str, Any]:
    """调用 ZIP0 公开发现接口并列出站内观看候选。"""
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("搜索关键词不能为空")
    payload = request_json(
        ZIP0_SEARCH_URL, {"query": keyword, "page": page, "limit": limit}
    )
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("ZIP0 搜索接口返回失败")
    items = payload.get("data") or []
    if not isinstance(items, list):
        raise ValueError("ZIP0 搜索结果格式无效")

    results = []
    skipped = []
    for item in items:
        try:
            watch_url = validate_zip0_watch_url(normalize_text(item.get("url")))
        except ValueError as error:
            skipped.append({"title": normalize_text(item.get("title")), "reason": str(error)})
            continue
        watch_query = parse_qs(urlparse(watch_url).query)
        results.append(
            {
                "index": len(results) + 1,
                "source": "ZIP0",
                "line": normalize_text((watch_query.get("source") or [""])[0]),
                "title": normalize_text(item.get("title")),
                "year": normalize_text(item.get("year")),
                "category": normalize_text(item.get("category")),
                "area": normalize_text(item.get("area")),
                "language": normalize_text(item.get("language")),
                "score": normalize_text(item.get("score")),
                "remarks": normalize_text(item.get("remarks")),
                "episode_count": int(item.get("episodeCount") or 0),
                "url": watch_url,
            }
        )

    result = {
        "keyword": keyword,
        "source": "ZIP0",
        "results": results,
        "skipped": skipped,
    }
    if catalog:
        write_catalog(catalog, result)
        result["catalog"] = str(Path(catalog).resolve())
    return result


def select_zip0_result(
    search_catalog: str, item_index: int, episode: int = 1
) -> Dict[str, Any]:
    """选择 ZIP0 候选并返回指定集数的站内观看页。"""
    catalog = load_catalog(search_catalog)
    if catalog.get("source") != "ZIP0":
        raise ValueError("目录不是 ZIP0 搜索结果")
    selected = next(
        (item for item in catalog.get("results") or [] if item.get("index") == item_index),
        None,
    )
    if selected is None:
        raise ValueError("候选编号不存在")
    episode_count = int(selected.get("episode_count") or 0)
    if episode < 1 or (episode_count and episode > episode_count):
        raise ValueError(f"剧集编号超出范围：1-{episode_count or '未知'}")

    parsed = urlparse(validate_zip0_watch_url(selected.get("url") or ""))
    query = parse_qs(parsed.query)
    query["episode"] = [str(episode)]
    return {
        "source": "ZIP0",
        "title": selected.get("title"),
        "episode": episode,
        "open_url": urlunparse(parsed._replace(query=urlencode(query, doseq=True))),
    }


class AnchorCollector(HTMLParser):
    """提取页面锚点，避免引入 HTML 解析依赖。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._current = {"attrs": dict(attrs), "text": []}

    def handle_data(self, data):
        if self._current is not None:
            self._current["text"].append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._current is not None:
            self._current["text"] = " ".join("".join(self._current["text"]).split())
            self.anchors.append(self._current)
            self._current = None


def parse_juzong_search_html(
    page: str, keyword: str, limit: int
) -> List[Dict[str, Any]]:
    """从剧踪搜索页提取匹配的站内详情页。"""
    parser = AnchorCollector()
    parser.feed(page)
    normalized_keyword = re.sub(r"\s+", "", keyword).lower()
    results = []
    seen = set()
    for anchor in parser.anchors:
        path = anchor["attrs"].get("href") or ""
        if not JUZONG_DETAIL_PATTERN.fullmatch(urlparse(path).path):
            continue
        title_attr = strip_html(anchor["attrs"].get("title"))
        anchor_text = strip_html(anchor.get("text"))
        title = title_attr or anchor_text
        normalized_title = re.sub(r"\s+", "", title).lower()
        if not normalized_title or (
            normalized_keyword not in normalized_title
            and normalized_title not in normalized_keyword
        ):
            continue
        if path in seen:
            continue
        seen.add(path)
        results.append(
            {
                "index": len(results) + 1,
                "source": "剧踪影院",
                "title": title,
                "remarks": anchor_text if title_attr and anchor_text != title_attr else None,
                "detail_url": urljoin(JUZONG_BASE_URL, path),
            }
        )
        if len(results) >= limit:
            break
    return results


def parse_juzong_detail_html(page: str) -> List[Dict[str, Any]]:
    """从剧踪详情页提取站内播放选项。"""
    parser = AnchorCollector()
    parser.feed(page)
    options = []
    seen = set()
    for anchor in parser.anchors:
        path = urlparse(anchor["attrs"].get("href") or "").path
        match = JUZONG_PLAY_PATTERN.fullmatch(path)
        if not match or path in seen:
            continue
        label = strip_html(anchor.get("text"))
        if not label or label == "立即播放":
            continue
        seen.add(path)
        options.append(
            {
                "index": len(options) + 1,
                "route": int(match.group(2)),
                "episode": int(match.group(3)),
                "label": label,
                "open_url": urljoin(JUZONG_BASE_URL, path),
            }
        )
    return options


def validate_juzong_url(url: str, kind: str) -> str:
    """只允许打开剧踪自己的 HTTPS 详情页或播放页。"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in JUZONG_HOSTS:
        raise ValueError("剧踪结果包含非本站 HTTPS 地址")
    pattern = JUZONG_DETAIL_PATTERN if kind == "detail" else JUZONG_PLAY_PATTERN
    if not pattern.fullmatch(parsed.path):
        raise ValueError("剧踪站内地址格式无效")
    return url


def create_juzong_opener():
    """创建只在当前命令内存活的匿名 Cookie 会话。"""
    return build_opener(
        HTTPCookieProcessor(CookieJar()),
        HTTPSHandler(context=create_ssl_context()),
    )


def fetch_juzong_html(opener, url: str, allow_forbidden: bool = False) -> str:
    """读取剧踪页面；首次搜索的 403 仍可用于接收匿名 Cookie。"""
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 video-search-skill/0.4"})
    try:
        with opener.open(request, timeout=25) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        if allow_forbidden and error.code == 403:
            return error.read().decode("utf-8", errors="replace")
        raise


def search_juzong(
    keyword: str, limit: int = 10, catalog: str = None
) -> Dict[str, Any]:
    """按站点正常匿名会话流程搜索剧踪。"""
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("搜索关键词不能为空")
    opener = create_juzong_opener()
    fetch_juzong_html(opener, f"{JUZONG_BASE_URL}/")
    search_url = f"{JUZONG_BASE_URL}/vodsearch/-------------/?{urlencode({'wd': keyword})}"
    first_page = fetch_juzong_html(opener, search_url, allow_forbidden=True)
    if "window.location.href" in first_page or "请不要频繁搜索" in first_page:
        time.sleep(JUZONG_SEARCH_DELAY_SECONDS)
        page = fetch_juzong_html(opener, search_url)
    else:
        page = first_page
    result = {
        "keyword": keyword,
        "source": "剧踪影院",
        "results": parse_juzong_search_html(page, keyword, limit),
    }
    if catalog:
        write_catalog(catalog, result)
        result["catalog"] = str(Path(catalog).resolve())
    return result


def juzong_detail_from_search(
    search_catalog: str, item_index: int, detail_catalog: str = None
) -> Dict[str, Any]:
    """读取剧踪候选详情并列出站内播放选项。"""
    catalog = load_catalog(search_catalog)
    if catalog.get("source") != "剧踪影院":
        raise ValueError("目录不是剧踪搜索结果")
    selected = next(
        (item for item in catalog.get("results") or [] if item.get("index") == item_index),
        None,
    )
    if selected is None:
        raise ValueError("候选编号不存在")
    detail_url = validate_juzong_url(selected.get("detail_url") or "", "detail")
    page = fetch_juzong_html(create_juzong_opener(), detail_url)
    result = {
        "source": "剧踪影院",
        "title": selected.get("title"),
        "options": parse_juzong_detail_html(page),
    }
    if detail_catalog:
        write_catalog(detail_catalog, result)
        result["catalog"] = str(Path(detail_catalog).resolve())
    return result


def select_juzong_option(detail_catalog: str, option_index: int) -> Dict[str, Any]:
    """按用户可见编号返回剧踪站内播放页。"""
    catalog = load_catalog(detail_catalog)
    if catalog.get("source") != "剧踪影院":
        raise ValueError("目录不是剧踪详情结果")
    selected = next(
        (item for item in catalog.get("options") or [] if item.get("index") == option_index),
        None,
    )
    if selected is None:
        raise ValueError("播放选项编号不存在")
    return {
        "source": "剧踪影院",
        "title": catalog.get("title"),
        "label": selected.get("label"),
        "open_url": validate_juzong_url(selected.get("open_url") or "", "play"),
    }


def open_default_browser(result: Dict[str, Any]) -> Dict[str, Any]:
    """使用操作系统默认浏览器打开经过来源校验的站内地址。"""
    if not webbrowser.open_new_tab(result["open_url"]):
        raise OSError("系统默认浏览器未能打开播放页")
    result["browser_opened"] = True
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="搜索第三方影视网站并打开站内播放页")
    commands = parser.add_subparsers(dest="command", required=True)

    search = commands.add_parser("search", help="搜索第三方影视网站")
    search.add_argument("keyword")
    search.add_argument("--source", choices=("zip0", "juzong"), default="zip0")
    search.add_argument("--limit", type=int, default=10, choices=range(1, 21))
    search.add_argument("--page", type=int, default=1)
    search.add_argument("--catalog")

    select = commands.add_parser("select", help="选择 ZIP0 候选并在默认浏览器打开")
    select.add_argument("search_catalog")
    select.add_argument("--index", type=int, required=True)
    select.add_argument("--episode", type=int, default=1)

    detail = commands.add_parser("juzong-detail", help="列出剧踪播放选项")
    detail.add_argument("search_catalog")
    detail.add_argument("--index", type=int, required=True)
    detail.add_argument("--catalog", required=True)

    select_juzong = commands.add_parser(
        "juzong-select", help="选择剧踪播放项并在默认浏览器打开"
    )
    select_juzong.add_argument("detail_catalog")
    select_juzong.add_argument("--index", type=int, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "search":
            if args.source == "zip0":
                result = search_zip0(args.keyword, args.page, args.limit, args.catalog)
            else:
                result = search_juzong(args.keyword, args.limit, args.catalog)
        elif args.command == "select":
            result = open_default_browser(
                select_zip0_result(args.search_catalog, args.index, args.episode)
            )
        elif args.command == "juzong-detail":
            result = juzong_detail_from_search(
                args.search_catalog, args.index, args.catalog
            )
        elif args.command == "juzong-select":
            result = open_default_browser(
                select_juzong_option(args.detail_catalog, args.index)
            )
        else:
            raise ValueError(f"不支持的命令：{args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (HTTPError, URLError, TimeoutError, KeyError, ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
