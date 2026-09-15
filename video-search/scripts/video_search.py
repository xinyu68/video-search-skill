#!/usr/bin/env python3
"""搜索 ZIP0 等视频来源，并返回经过校验的站内观看页。"""

import argparse
import html as html_utils
import json
import os
import re
import ssl
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


TVMAZE_SEARCH_URL = "https://api.tvmaze.com/search/shows"
ZIP0_SEARCH_URL = "https://zip0.com/api/videos/search"
ARCHIVE_SEARCH_URL = "https://archive.org/advancedsearch.php"
ARCHIVE_METADATA_URL = "https://archive.org/metadata"
ARCHIVE_DOWNLOAD_URL = "https://archive.org/download"
HEADERS = {"User-Agent": "video-search-skill/0.1"}
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
ARCHIVE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
PLAYABLE_SUFFIXES = {".mp4", ".m4v", ".webm", ".ogv", ".m3u8"}
MAX_CMS_SOURCES = 8
CMS_CONFIG_ENV = "VIDEO_SEARCH_SOURCES"
ZIP0_HOSTS = {"zip0.com", "www.zip0.com"}


def create_ssl_context() -> ssl.SSLContext:
    """优先使用 certifi，仍然保持 HTTPS 证书校验。"""
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
    """把多值元数据转换为便于展示的文本。"""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value or "")


def strip_html(value: Any) -> str:
    """移除元数据中的简单 HTML 标记。"""
    text = re.sub(r"<[^>]+>", " ", normalize_text(value))
    return re.sub(r"\s+", " ", html_utils.unescape(text)).strip()


def write_catalog(path: str, payload: Dict[str, Any]) -> None:
    """把候选保存为后续编号选择使用的目录文件。"""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_zip0_watch_url(url: str) -> str:
    """只允许 ZIP0 返回自己的 HTTPS 观看页，避免打开任意外部地址。"""
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
    keyword: str, page: int = 1, limit: int = 20, catalog: str = None
) -> Dict[str, Any]:
    """调用 ZIP0 公开发现 API，统一候选编号并保留站内观看页。"""
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
                "updated_at": normalize_text(item.get("updatedAt")),
                "url": watch_url,
            }
        )

    result = {
        "keyword": keyword,
        "source": "ZIP0",
        "results": results,
        "pagination": payload.get("pagination") or {},
        "sources": payload.get("sources") or {},
        "skipped": skipped,
    }
    if catalog:
        write_catalog(catalog, result)
        result["catalog"] = str(Path(catalog).resolve())
    return result


def select_zip0_result(search_catalog: str, item_index: int, episode: int = 1) -> Dict[str, Any]:
    """按用户可见编号选择 ZIP0 结果，并生成对应集数的站内观看页。"""
    catalog = load_json(search_catalog)
    selected = next(
        (item for item in catalog.get("results") or [] if item.get("index") == item_index),
        None,
    )
    if selected is None:
        raise ValueError("候选编号不存在")
    episode_count = int(selected.get("episode_count") or 0)
    if episode < 1 or (episode_count and episode > episode_count):
        upper = episode_count or "未知"
        raise ValueError(f"剧集编号超出范围：1-{upper}")

    watch_url = validate_zip0_watch_url(selected.get("url") or "")
    parsed = urlparse(watch_url)
    query = parse_qs(parsed.query)
    query["episode"] = [str(episode)]
    open_url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    return {
        "index": selected.get("index"),
        "source": selected.get("source"),
        "line": selected.get("line"),
        "title": selected.get("title"),
        "year": selected.get("year"),
        "episode": episode,
        "episode_count": episode_count,
        "open_url": open_url,
    }


def search_archive(keyword: str, limit: int, catalog: str = None) -> Dict[str, Any]:
    """搜索 Internet Archive 中明确带开放许可标记的视频。"""
    escaped_keyword = keyword.replace("\\", "\\\\").replace('"', '\\"')
    query = f'mediatype:movies AND licenseurl:* AND title:"{escaped_keyword}"'
    payload = request_json(
        ARCHIVE_SEARCH_URL,
        {
            "q": query,
            "fl[]": [
                "identifier",
                "title",
                "creator",
                "date",
                "description",
                "licenseurl",
                "downloads",
            ],
            "sort[]": "downloads desc",
            "rows": limit,
            "page": 1,
            "output": "json",
        },
    )
    docs = ((payload.get("response") or {}).get("docs") or [])[:limit]
    results = []
    for index, item in enumerate(docs, start=1):
        identifier = normalize_text(item.get("identifier"))
        results.append(
            {
                "index": index,
                "source": "Internet Archive",
                "source_id": identifier,
                "title": normalize_text(item.get("title")),
                "creator": normalize_text(item.get("creator")) or None,
                "year": normalize_text(item.get("date"))[:4] or None,
                "description": strip_html(item.get("description"))[:240] or None,
                "license": normalize_text(item.get("licenseurl")) or None,
                "downloads": item.get("downloads"),
                "details_url": f"https://archive.org/details/{quote(identifier, safe='')}",
            }
        )
    result = {"keyword": keyword, "source": "Internet Archive", "results": results}
    if catalog:
        write_catalog(catalog, result)
        result["catalog"] = str(Path(catalog).resolve())
    return result


def require_archive_identifier(identifier: str) -> str:
    """校验 Internet Archive 条目标识符。"""
    if not ARCHIVE_IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValueError("Internet Archive 条目标识符格式无效")
    return identifier


def format_size(value: Any) -> str:
    """格式化文件大小。"""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return None
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.2f} MB"
    return f"{size / 1024:.2f} KB"


def archive_files(identifier: str, catalog: str = None) -> Dict[str, Any]:
    """读取开放许可条目的可播放文件。"""
    identifier = require_archive_identifier(identifier)
    payload = request_json(f"{ARCHIVE_METADATA_URL}/{quote(identifier, safe='')}")
    metadata = payload.get("metadata") or {}
    license_url = normalize_text(metadata.get("licenseurl"))
    if not license_url:
        raise ValueError("该条目没有明确的开放许可标记，拒绝生成播放地址")
    candidates = []
    for item in payload.get("files") or []:
        name = normalize_text(item.get("name"))
        suffix = Path(name).suffix.lower()
        if suffix not in PLAYABLE_SUFFIXES or item.get("private") is True:
            continue
        candidates.append(
            {
                "name": name,
                "format": normalize_text(item.get("format")) or suffix.lstrip(".").upper(),
                "size_bytes": int(item["size"]) if str(item.get("size", "")).isdigit() else None,
                "size": format_size(item.get("size")),
                "source": item.get("source"),
                "width": item.get("width"),
                "height": item.get("height"),
                "url": f"{ARCHIVE_DOWNLOAD_URL}/{quote(identifier, safe='')}/{quote(name)}",
            }
        )
    candidates.sort(
        key=lambda item: (
            0 if Path(item["name"]).suffix.lower() == ".mp4" else 1,
            item["size_bytes"] or 0,
        )
    )
    for index, item in enumerate(candidates, start=1):
        item["index"] = index
    result = {
        "source": "Internet Archive",
        "source_id": identifier,
        "title": normalize_text(metadata.get("title")),
        "creator": normalize_text(metadata.get("creator")) or None,
        "license": license_url,
        "files": candidates,
    }
    if catalog:
        write_catalog(catalog, result)
        result["catalog"] = str(Path(catalog).resolve())
    return result


def search_tvmaze(keyword: str, limit: int) -> Dict[str, Any]:
    """调用公开影视元数据接口并规范化候选。"""
    payload = request_json(TVMAZE_SEARCH_URL, {"q": keyword})
    results: List[Dict[str, Any]] = []
    for index, item in enumerate(payload[:limit], start=1):
        show = item.get("show") or {}
        premiered = show.get("premiered") or ""
        web_channel = show.get("webChannel") or {}
        network = show.get("network") or {}
        results.append(
            {
                "index": index,
                "source": "TVmaze",
                "source_id": show.get("id"),
                "title": show.get("name"),
                "year": premiered[:4] or None,
                "type": show.get("type"),
                "language": show.get("language"),
                "genres": show.get("genres") or [],
                "status": show.get("status"),
                "platform": web_channel.get("name") or network.get("name"),
                "rating": (show.get("rating") or {}).get("average"),
                "metadata_url": show.get("url"),
                "official_url": show.get("officialSite"),
            }
        )
    return {"keyword": keyword, "results": results}


def normalize_maccms(payload: Dict[str, Any], source_name: str) -> Dict[str, Any]:
    """规范化苹果 CMS V10 搜索响应。"""
    results = []
    for index, item in enumerate(payload.get("list") or [], start=1):
        results.append(
            {
                "index": index,
                "source": source_name,
                "source_id": item.get("vod_id"),
                "title": item.get("vod_name"),
                "year": item.get("vod_year"),
                "type": item.get("type_name"),
                "remarks": item.get("vod_remarks"),
                "poster": item.get("vod_pic"),
            }
        )
    return {
        "page": payload.get("page"),
        "page_count": payload.get("pagecount"),
        "total": payload.get("total"),
        "results": results,
    }


def parse_maccms(path: str, source_name: str) -> Dict[str, Any]:
    """读取本地苹果 CMS 示例响应。"""
    with Path(path).open("r", encoding="utf-8") as stream:
        return normalize_maccms(json.load(stream), source_name)


def load_json(path: str) -> Dict[str, Any]:
    """读取本地 JSON 文件。"""
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)


def resolve_cms_config(config_path: str = None) -> str:
    """按显式参数、环境变量和用户配置目录的顺序寻找采集站配置。"""
    candidates = []
    if config_path:
        candidates.append(Path(config_path).expanduser())

    env_path = os.environ.get(CMS_CONFIG_ENV)
    if env_path:
        candidates.append(Path(env_path).expanduser())

    app_data = os.environ.get("APPDATA")
    if app_data:
        candidates.append(Path(app_data) / "video-search" / "sources.json")
    candidates.append(Path.home() / ".config" / "video-search" / "sources.json")

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())

    expected = "、".join(str(item) for item in candidates)
    raise ValueError(
        "没有找到采集站配置。请使用 --config 指定 sources.json，"
        f"或设置 {CMS_CONFIG_ENV}；已检查：{expected}"
    )


def validate_cms_api_url(url: str) -> str:
    """校验苹果 CMS API 地址，普通 HTTP 仅允许本机测试。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"苹果 CMS API 地址无效：{url}")
    if parsed.username or parsed.password:
        raise ValueError("苹果 CMS API 地址不得内嵌账号或密码")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程苹果 CMS API 必须使用 HTTPS")
    return url


def load_cms_sources(config_path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """读取已授权的在线苹果 CMS 来源配置。"""
    payload = load_json(config_path)
    configured = payload.get("sources") or []
    if not isinstance(configured, list):
        raise ValueError("sources 必须是数组")
    active = []
    skipped = []
    names = set()
    for item in configured:
        name = normalize_text(item.get("name")).strip()
        if not name:
            raise ValueError("来源名称不能为空")
        if name in names:
            raise ValueError(f"来源名称重复：{name}")
        names.add(name)
        if not item.get("enabled", True):
            skipped.append({"source": name, "reason": "来源未启用"})
            continue
        if item.get("authorized") is not True:
            skipped.append({"source": name, "reason": "未声明访问授权"})
            continue
        active.append({"name": name, "api_url": validate_cms_api_url(item.get("api_url") or "")})
    if len(active) > MAX_CMS_SOURCES:
        raise ValueError(f"一次最多启用 {MAX_CMS_SOURCES} 个苹果 CMS 来源")
    return active, skipped


def search_one_cms_source(source: Dict[str, Any], keyword: str, limit: int) -> List[Dict[str, Any]]:
    """搜索单个已授权苹果 CMS 来源。"""
    payload = request_json(
        source["api_url"], {"ac": "detail", "wd": keyword, "pg": 1}
    )
    results = []
    for item in (payload.get("list") or [])[:limit]:
        results.append(
            {
                "source": source["name"],
                "source_id": item.get("vod_id"),
                "title": item.get("vod_name"),
                "year": item.get("vod_year"),
                "type": item.get("type_name"),
                "remarks": item.get("vod_remarks"),
                "poster": item.get("vod_pic"),
                "api_url": source["api_url"],
            }
        )
    return results


def search_cms(
    keyword: str, config_path: str = None, limit_per_source: int = 5, catalog: str = None
) -> Dict[str, Any]:
    """并行搜索多个已授权苹果 CMS 来源并统一编号。"""
    resolved_config = resolve_cms_config(config_path)
    sources, skipped = load_cms_sources(resolved_config)
    results_by_name = {}
    errors = list(skipped)
    if sources:
        with ThreadPoolExecutor(max_workers=min(4, len(sources))) as executor:
            futures = {
                executor.submit(search_one_cms_source, source, keyword, limit_per_source): source
                for source in sources
            }
            for future in as_completed(futures):
                source = futures[future]
                try:
                    results_by_name[source["name"]] = future.result()
                except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
                    errors.append({"source": source["name"], "reason": str(error)})
    results = []
    for source in sources:
        results.extend(results_by_name.get(source["name"], []))
    for index, item in enumerate(results, start=1):
        item["index"] = index
    result = {
        "keyword": keyword,
        "config": resolved_config,
        "source_count": len(sources),
        "results": results,
        "source_errors": errors,
    }
    if catalog:
        write_catalog(catalog, result)
        result["catalog"] = str(Path(catalog).resolve())
    return result


def cms_detail_from_search(
    search_catalog: str, item_index: int, detail_catalog: str = None
) -> Dict[str, Any]:
    """按搜索结果编号获取苹果 CMS 详情、线路和剧集。"""
    catalog = load_json(search_catalog)
    results = catalog.get("results") or []
    if item_index < 1 or item_index > len(results):
        raise ValueError(f"候选编号超出范围：1-{len(results)}")
    selected = results[item_index - 1]
    api_url = validate_cms_api_url(selected.get("api_url") or "")
    source_id = selected.get("source_id")
    if source_id is None or str(source_id).strip() == "":
        raise ValueError("候选缺少来源条目编号")
    payload = request_json(api_url, {"ac": "detail", "ids": source_id})
    items = payload.get("list") or []
    if not items:
        raise ValueError("详情接口没有返回条目")
    item = items[0]
    result = {
        "source": selected.get("source"),
        "source_id": item.get("vod_id"),
        "title": item.get("vod_name") or selected.get("title"),
        "year": item.get("vod_year"),
        "type": item.get("type_name"),
        "remarks": item.get("vod_remarks"),
        "routes": parse_play_routes(item),
    }
    if detail_catalog:
        write_catalog(detail_catalog, result)
        result["catalog"] = str(Path(detail_catalog).resolve())
    return result


def choose_cms_episode(
    detail_catalog: str, route_index: int, episode_index: int
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """按用户可见编号选择在线苹果 CMS 线路和剧集。"""
    detail = load_json(detail_catalog)
    route = next(
        (item for item in detail.get("routes") or [] if item.get("index") == route_index),
        None,
    )
    if route is None:
        raise ValueError("线路编号不存在")
    episode = next(
        (item for item in route.get("episodes") or [] if item.get("index") == episode_index),
        None,
    )
    if episode is None:
        raise ValueError("剧集编号不存在")
    if not episode.get("direct_http"):
        raise ValueError("该剧集不是可直接播放的 HTTP(S) 地址")
    return detail, route, episode


def make_cms_player(
    detail_catalog: str, route_index: int, episode_index: int, output: str
) -> Dict[str, Any]:
    """为在线苹果 CMS 的用户选中剧集生成播放页。"""
    detail, route, episode = choose_cms_episode(
        detail_catalog, route_index, episode_index
    )
    output_path = render_player(
        f'{detail.get("title") or "视频"} - {episode["name"]}',
        episode["target"],
        output,
    )
    return {
        "title": detail.get("title"),
        "source": detail.get("source"),
        "route": route.get("name"),
        "episode": episode.get("name"),
        "output": output_path,
    }


def parse_episode_item(raw_item: str, episode_index: int) -> Dict[str, Any]:
    """解析苹果 CMS 的单集“名称$地址”格式。"""
    parts = raw_item.strip().split("$", 1)
    if len(parts) == 1:
        name, target = f"第{episode_index}集", parts[0]
    else:
        name, target = parts
    target = target.strip()
    scheme = urlparse(target).scheme.lower()
    return {
        "index": episode_index,
        "name": name.strip() or f"第{episode_index}集",
        "target": target,
        "direct_http": scheme in {"http", "https"},
    }


def parse_play_routes(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把苹果 CMS 的线路和剧集字段转换为结构化列表。"""
    route_names = str(item.get("vod_play_from") or "").split("$$$")
    route_groups = str(item.get("vod_play_url") or "").split("$$$")
    routes: List[Dict[str, Any]] = []
    for route_index, raw_group in enumerate(route_groups, start=1):
        if not raw_group.strip():
            continue
        episodes = [
            parse_episode_item(raw_item, episode_index)
            for episode_index, raw_item in enumerate(raw_group.split("#"), start=1)
            if raw_item.strip()
        ]
        route_name = route_names[route_index - 1].strip() if route_index <= len(route_names) else ""
        routes.append(
            {
                "index": route_index,
                "name": route_name or f"线路{route_index}",
                "episodes": episodes,
            }
        )
    return routes


def list_episodes(path: str, item_index: int) -> Dict[str, Any]:
    """列出详情响应中指定条目的播放线路与剧集。"""
    payload = load_json(path)
    items = payload.get("list") or []
    if item_index < 1 or item_index > len(items):
        raise ValueError(f"条目编号超出范围：1-{len(items)}")
    item = items[item_index - 1]
    return {
        "index": item_index,
        "source_id": item.get("vod_id"),
        "title": item.get("vod_name"),
        "routes": parse_play_routes(item),
    }


def choose_episode(
    path: str, item_index: int, route_index: int, episode_index: int
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """按用户可见编号选中一个条目、线路和剧集。"""
    detail = list_episodes(path, item_index)
    routes = detail["routes"]
    route = next((value for value in routes if value["index"] == route_index), None)
    if route is None:
        raise ValueError("线路编号不存在")
    episode = next(
        (value for value in route["episodes"] if value["index"] == episode_index), None
    )
    if episode is None:
        raise ValueError("剧集编号不存在")
    if not episode["direct_http"]:
        raise ValueError("该条目不是可直接访问的 HTTP(S) 地址")
    return detail, route, episode


def probe_stream(path: str, item_index: int, route_index: int, episode_index: int) -> Dict[str, Any]:
    """只读取播放清单，不下载视频内容。"""
    detail, route, episode = choose_episode(path, item_index, route_index, episode_index)
    request = Request(episode["target"], headers=HEADERS)
    with urlopen(request, timeout=20, context=create_ssl_context()) as response:
        raw = response.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("响应超过播放清单大小上限，拒绝继续读取")
        content_type = response.headers.get("Content-Type")
        final_url = response.geturl()
    text = raw.decode("utf-8-sig", errors="replace")
    is_hls = text.lstrip().startswith("#EXTM3U")
    return {
        "title": detail["title"],
        "route": route["name"],
        "episode": episode["name"],
        "requested_url": episode["target"],
        "final_url": final_url,
        "content_type": content_type,
        "manifest_bytes": len(raw),
        "is_hls": is_hls,
        "playlist_kind": (
            "master" if is_hls and "#EXT-X-STREAM-INF" in text else "media" if is_hls else None
        ),
        "variant_count": text.count("#EXT-X-STREAM-INF") if is_hls else 0,
        "segment_count": text.count("#EXTINF:") if is_hls else 0,
    }


def render_player(title: str, source_url: str, output: str) -> str:
    """生成支持普通视频文件和 HLS 的本地播放页。"""
    title_json = json.dumps(title, ensure_ascii=False).replace("</", "<\\/")
    url_json = json.dumps(source_url, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html_utils.escape(title)}</title>
  <style>
    body {{ margin: 0; padding: 24px; color: #eee; background: #111; font-family: sans-serif; }}
    main {{ max-width: 960px; margin: auto; }}
    video {{ width: 100%; background: #000; }}
    #status {{ color: #aaa; }}
  </style>
</head>
<body>
  <main><h1 id="title"></h1><video id="video" controls playsinline></video><p id="status">正在加载视频…</p></main>
  <script src="https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"></script>
  <script>
    const title = {title_json};
    const source = {url_json};
    const video = document.getElementById('video');
    const status = document.getElementById('status');
    const isHls = new URL(source).pathname.toLowerCase().endsWith('.m3u8');
    document.getElementById('title').textContent = title;
    if (!isHls) {{
      video.src = source;
      status.textContent = '视频地址已加载，可以播放';
    }} else if (window.Hls && Hls.isSupported()) {{
      const hls = new Hls();
      hls.loadSource(source);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => status.textContent = 'HLS 清单已加载，可以播放');
      hls.on(Hls.Events.ERROR, (_, data) => status.textContent = `播放错误：${{data.type}} / ${{data.details}}`);
    }} else if (video.canPlayType('application/vnd.apple.mpegurl')) {{
      video.src = source;
      status.textContent = '已使用浏览器原生 HLS';
    }} else {{
      status.textContent = '当前浏览器不支持 HLS';
    }}
  </script>
</body>
</html>
"""
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return str(output_path)


def make_player(
    path: str,
    item_index: int,
    route_index: int,
    episode_index: int,
    output: str,
) -> Dict[str, Any]:
    """为已授权的苹果 CMS 响应生成本地播放页。"""
    detail, route, episode = choose_episode(path, item_index, route_index, episode_index)
    title = f'{detail["title"]} - {episode["name"]}'
    output_path = render_player(title, episode["target"], output)
    return {
        "title": detail["title"],
        "route": route["name"],
        "episode": episode["name"],
        "output": output_path,
    }


def archive_files_from_search(
    search_catalog: str, item_index: int, files_catalog: str = None
) -> Dict[str, Any]:
    """根据搜索目录中的编号读取 Internet Archive 文件。"""
    catalog = load_json(search_catalog)
    results = catalog.get("results") or []
    if item_index < 1 or item_index > len(results):
        raise ValueError(f"候选编号超出范围：1-{len(results)}")
    item = results[item_index - 1]
    if item.get("source") != "Internet Archive":
        raise ValueError("该候选不提供可播放文件")
    return archive_files(item["source_id"], files_catalog)


def make_archive_player(files_catalog: str, file_index: int, output: str) -> Dict[str, Any]:
    """根据文件目录中的用户编号选择生成播放器。"""
    catalog = load_json(files_catalog)
    if catalog.get("source") != "Internet Archive" or not catalog.get("license"):
        raise ValueError("目录不是带开放许可的 Internet Archive 条目")
    files = catalog.get("files") or []
    if file_index < 1 or file_index > len(files):
        raise ValueError(f"文件编号超出范围：1-{len(files)}")
    item = files[file_index - 1]
    parsed = urlparse(item.get("url") or "")
    if parsed.scheme != "https" or parsed.hostname != "archive.org":
        raise ValueError("播放地址不属于 Internet Archive")
    output_path = render_player(catalog.get("title") or "开放视频", item["url"], output)
    return {
        "title": catalog.get("title"),
        "file": item.get("name"),
        "format": item.get("format"),
        "size": item.get("size"),
        "license": catalog.get("license"),
        "output": output_path,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="搜索视频并返回站内观看页")
    commands = parser.add_subparsers(dest="command", required=True)

    search = commands.add_parser("search", help="搜索 ZIP0、采集站、开放视频或影视元数据")
    search.add_argument("keyword")
    search.add_argument(
        "--source", choices=("zip0", "cms", "archive", "tvmaze"), default="zip0"
    )
    search.add_argument("--config", help="采集站配置；省略时从用户配置目录查找")
    search.add_argument("--limit", type=int, default=5, choices=range(1, 21))
    search.add_argument("--page", type=int, default=1)
    search.add_argument("--catalog")

    select = commands.add_parser("select", help="按编号取得 ZIP0 站内观看页")
    select.add_argument("search_catalog")
    select.add_argument("--index", type=int, required=True)
    select.add_argument("--episode", type=int, default=1)

    archive_detail = commands.add_parser("files", help="按搜索候选编号列出开放视频文件")
    archive_detail.add_argument("search_catalog")
    archive_detail.add_argument("--index", type=int, required=True)
    archive_detail.add_argument("--catalog")

    archive_player = commands.add_parser("player", help="按文件编号生成开放视频播放器")
    archive_player.add_argument("files_catalog")
    archive_player.add_argument("--index", type=int, required=True)
    archive_player.add_argument("--output", required=True)

    cms_search = commands.add_parser("cms-search", help="搜索已授权的在线苹果 CMS 来源")
    cms_search.add_argument("keyword")
    cms_search.add_argument("--config", help="采集站配置；省略时从用户配置目录查找")
    cms_search.add_argument(
        "--limit-per-source", type=int, default=5, choices=range(1, 21)
    )
    cms_search.add_argument("--catalog", required=True)

    cms_detail = commands.add_parser("cms-detail", help="按候选编号获取线路和剧集")
    cms_detail.add_argument("search_catalog")
    cms_detail.add_argument("--index", type=int, required=True)
    cms_detail.add_argument("--catalog", required=True)

    cms_player = commands.add_parser("cms-player", help="按线路和剧集编号生成播放器")
    cms_player.add_argument("detail_catalog")
    cms_player.add_argument("--route", type=int, required=True)
    cms_player.add_argument("--episode", type=int, required=True)
    cms_player.add_argument("--output", required=True)

    maccms = commands.add_parser("parse-maccms", help="解析本地苹果 CMS 响应")
    maccms.add_argument("path")
    maccms.add_argument("--source-name", default="示例苹果CMS")

    episodes = commands.add_parser("episodes", help="解析详情中的线路与剧集")
    episodes.add_argument("path")
    episodes.add_argument("--index", type=int, default=1)

    probe = commands.add_parser("probe", help="检查选中剧集的公开播放清单")
    probe.add_argument("path")
    probe.add_argument("--index", type=int, default=1)
    probe.add_argument("--route", type=int, default=1)
    probe.add_argument("--episode", type=int, default=1)

    player = commands.add_parser("make-player", help="为已授权苹果 CMS 响应生成播放页")
    player.add_argument("path")
    player.add_argument("--index", type=int, default=1)
    player.add_argument("--route", type=int, default=1)
    player.add_argument("--episode", type=int, default=1)
    player.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "search":
            if args.source == "zip0":
                result = search_zip0(
                    args.keyword, args.page, args.limit, args.catalog
                )
            elif args.source == "cms":
                result = search_cms(
                    args.keyword, args.config, args.limit, args.catalog
                )
            elif args.source == "archive":
                result = search_archive(args.keyword, args.limit, args.catalog)
            else:
                result = search_tvmaze(args.keyword, args.limit)
                if args.catalog:
                    write_catalog(args.catalog, result)
                    result["catalog"] = str(Path(args.catalog).resolve())
        elif args.command == "select":
            result = select_zip0_result(
                args.search_catalog, args.index, args.episode
            )
        elif args.command == "files":
            result = archive_files_from_search(args.search_catalog, args.index, args.catalog)
        elif args.command == "player":
            result = make_archive_player(args.files_catalog, args.index, args.output)
        elif args.command == "cms-search":
            result = search_cms(
                args.keyword, args.config, args.limit_per_source, args.catalog
            )
        elif args.command == "cms-detail":
            result = cms_detail_from_search(
                args.search_catalog, args.index, args.catalog
            )
        elif args.command == "cms-player":
            result = make_cms_player(
                args.detail_catalog, args.route, args.episode, args.output
            )
        elif args.command == "parse-maccms":
            result = parse_maccms(args.path, args.source_name)
        elif args.command == "episodes":
            result = list_episodes(args.path, args.index)
        elif args.command == "probe":
            result = probe_stream(args.path, args.index, args.route, args.episode)
        else:
            result = make_player(
                args.path, args.index, args.route, args.episode, args.output
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (HTTPError, URLError, TimeoutError, KeyError, ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
