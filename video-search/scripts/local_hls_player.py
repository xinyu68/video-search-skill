#!/usr/bin/env python3
"""为 ZIP0 观看页创建仅监听本机的 HLS 播放器和受限媒体代理。"""

import argparse
import hashlib
import html
import ipaddress
import json
import re
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


USER_AGENT = "video-search-skill/0.2"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
ZIP0_HOSTS = {"zip0.com", "www.zip0.com"}
URI_ATTRIBUTE_PATTERN = re.compile(r'URI="([^"]+)"')
SAFE_MEDIA_EXTENSIONS = {".aac", ".bin", ".key", ".m3u8", ".m4s", ".mp4", ".ts", ".vtt"}
MANIFEST_PATTERN = re.compile(
    r"https?[^\"'< >\\]+\.m3u8(?:\?[^\"'< >\\]+)?", re.IGNORECASE
)


class PublicUrlGuard:
    """只允许解析到公网地址的 HTTPS URL，防止本地代理被用于 SSRF。"""

    def __init__(self) -> None:
        self._approved_hosts = set()
        self._lock = threading.Lock()

    def validate(self, url: str) -> str:
        if len(url) > 4096:
            raise ValueError("媒体地址过长")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("媒体代理仅允许公网 HTTPS 地址")
        if parsed.username or parsed.password:
            raise ValueError("媒体地址不得内嵌账号或密码")
        host = parsed.hostname.lower()
        with self._lock:
            if host in self._approved_hosts:
                return url
        addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        if not addresses:
            raise ValueError("媒体域名无法解析")
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global:
                raise ValueError("媒体域名解析到了非公网地址")
        with self._lock:
            self._approved_hosts.add(host)
        return url


class UrlRegistry:
    """把上游 URL 映射为不可伪造的本地令牌，避免形成任意开放代理。"""

    def __init__(self, guard: PublicUrlGuard) -> None:
        self._guard = guard
        self._urls: Dict[str, str] = {}
        self._lock = threading.Lock()

    def register(self, url: str) -> str:
        checked = self._guard.validate(url)
        token = hashlib.sha256(checked.encode("utf-8")).hexdigest()
        with self._lock:
            self._urls[token] = checked
        return token

    def get(self, token: str) -> str:
        with self._lock:
            url = self._urls.get(token)
        if not url:
            raise KeyError("未知媒体令牌")
        return url


def local_media_path(token: str, upstream_url: str) -> str:
    """生成保留安全扩展名的本地 URL，兼容依赖扩展名识别 HLS 类型的客户端。"""
    path = urlparse(upstream_url).path.lower()
    extension = next((item for item in SAFE_MEDIA_EXTENSIONS if path.endswith(item)), ".bin")
    return f"/media/{token}{extension}"


def validate_zip0_watch_url(url: str) -> str:
    """校验 ZIP0 站内观看页。"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ZIP0_HOSTS:
        raise ValueError("只支持 ZIP0 HTTPS 观看页")
    if parsed.path != "/watch":
        raise ValueError("URL 不是 ZIP0 观看页")
    return url


def fetch_bytes(url: str, referer: str, range_header: str = None):
    """请求上游内容；不向上游发送浏览器 Origin。"""
    headers = {"User-Agent": USER_AGENT, "Referer": referer, "Accept": "*/*"}
    if range_header:
        headers["Range"] = range_header
    request = Request(url, headers=headers)
    return urlopen(request, timeout=25)


def extract_manifest_url(watch_url: str, guard: PublicUrlGuard) -> str:
    """从 ZIP0 服务端渲染页面中读取公开的 HLS 清单地址。"""
    watch_url = validate_zip0_watch_url(watch_url)
    request = Request(watch_url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=25) as response:
        page = response.read(MAX_MANIFEST_BYTES + 1)
    if len(page) > MAX_MANIFEST_BYTES:
        raise ValueError("ZIP0 观看页响应过大")
    text = html.unescape(page.decode("utf-8", errors="replace"))
    match = MANIFEST_PATTERN.search(text)
    if not match:
        raise ValueError("ZIP0 观看页暂未提供 HLS 播放地址")
    manifest_url = match.group(0).replace("\\/", "/").replace("\\u0026", "&")
    return guard.validate(manifest_url)


def rewrite_manifest(text: str, base_url: str, registry: UrlRegistry) -> str:
    """把清单内的 URI 改写为本机令牌地址。"""
    output = []
    for original_line in text.splitlines():
        line = original_line.strip()
        if line and not line.startswith("#"):
            upstream_url = urljoin(base_url, line)
            token = registry.register(upstream_url)
            output.append(local_media_path(token, upstream_url))
            continue

        def replace_uri(match):
            upstream_url = urljoin(base_url, match.group(1))
            token = registry.register(upstream_url)
            return f'URI="{local_media_path(token, upstream_url)}"'

        output.append(URI_ATTRIBUTE_PATTERN.sub(replace_uri, original_line))
    return "\n".join(output) + "\n"


def render_player(title: str) -> bytes:
    """生成简洁的深色 HLS 播放页。"""
    safe_title = html.escape(title or "视频播放")
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{safe_title}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #090b10; color: #f4f5f7; font-family: system-ui, sans-serif; }}
    main {{ min-height: 100vh; display: grid; place-items: center; padding: 24px; }}
    section {{ width: min(1200px, 100%); }}
    h1 {{ margin: 0 0 14px; font-size: 20px; font-weight: 600; }}
    video {{ width: 100%; max-height: calc(100vh - 100px); background: #000; border-radius: 12px; }}
    #status {{ margin-top: 10px; color: #a8b0bd; font-size: 14px; }}
    #shortcuts {{ margin-top: 6px; color: #6f7886; font-size: 13px; }}
    #gesture {{ position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%);
      padding: 12px 18px; border-radius: 10px; background: rgba(0, 0, 0, .72); color: #fff;
      font-size: 20px; font-weight: 600; opacity: 0; transition: opacity .12s; pointer-events: none; }}
    #gesture.visible {{ opacity: 1; }}
  </style>
</head>
<body>
  <main><section>
    <h1>{safe_title}</h1>
    <video id="video" controls autoplay playsinline tabindex="0"></video>
    <div id="status">正在加载播放清单…</div>
    <div id="shortcuts">←/→ 短按后退/前进 10 秒，长按 2 倍速倒退/快进</div>
    <div id="gesture" aria-live="polite"></div>
  </section></main>
  <script src="https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"></script>
  <script src="/player.js"></script>
</body>
</html>"""
    return document.encode("utf-8")


def render_player_script() -> bytes:
    """生成独立播放器脚本，避免 CSP 阻止内联 JavaScript。"""
    script = """
    const video = document.getElementById('video');
    const status = document.getElementById('status');
    const gesture = document.getElementById('gesture');
    const source = '/master.m3u8';
    const describeMediaError = () => {
      const code = video.error && video.error.code;
      return code ? `浏览器无法播放该媒体（错误 ${code}）` : '浏览器无法播放该媒体';
    };
    video.addEventListener('playing', () => { status.textContent = '正在播放'; });
    video.addEventListener('canplay', () => {
      if (video.paused) status.textContent = '视频已缓冲，点击播放按钮开始观看';
    });
    video.addEventListener('waiting', () => { status.textContent = '正在缓冲视频数据…'; });
    video.addEventListener('stalled', () => { status.textContent = '网络读取较慢，正在继续缓冲…'; });
    video.addEventListener('error', () => { status.textContent = describeMediaError(); });

    // 方向键短按跳转，长按进入方向对应的 2 倍速操作。
    const SEEK_SECONDS = 10;
    const HOLD_DELAY_MS = 450;
    let keyHold = null;
    let gestureTimer = null;

    const showGesture = (message, persistent = false) => {
      window.clearTimeout(gestureTimer);
      gesture.textContent = message;
      gesture.classList.add('visible');
      if (!persistent) {
        gestureTimer = window.setTimeout(() => gesture.classList.remove('visible'), 700);
      }
    };

    const seekBy = (seconds) => {
      const duration = Number.isFinite(video.duration) ? video.duration : Infinity;
      video.currentTime = Math.min(duration, Math.max(0, video.currentTime + seconds));
      status.textContent = seconds > 0 ? '已前进 10 秒' : '已后退 10 秒';
      showGesture(seconds > 0 ? '→ 10 秒' : '← 10 秒');
    };

    const beginArrowHold = (direction) => {
      if (keyHold) return;
      // Chrome 原生视频控件可能吞掉 keyup，因此短按动作在首次 keydown 立即执行。
      seekBy(direction * SEEK_SECONDS);
      keyHold = {
        direction,
        held: false,
        wasPaused: video.paused,
        originalRate: video.playbackRate,
        reverseTimer: null,
        holdTimer: null,
      };
      keyHold.holdTimer = window.setTimeout(() => {
        if (!keyHold) return;
        keyHold.held = true;
        if (direction > 0) {
          video.playbackRate = 2;
          video.play().catch(() => {});
          status.textContent = '长按快进：2 倍速';
          showGesture('快进 2×', true);
        } else {
          video.pause();
          keyHold.reverseTimer = window.setInterval(() => {
            video.currentTime = Math.max(0, video.currentTime - 0.2);
          }, 100);
          status.textContent = '长按倒退：2 倍速';
          showGesture('倒退 2×', true);
        }
      }, HOLD_DELAY_MS);
    };

    const finishArrowHold = (performShortPress) => {
      if (!keyHold) return;
      const state = keyHold;
      keyHold = null;
      window.clearTimeout(state.holdTimer);
      if (!state.held) {
        if (performShortPress) seekBy(state.direction * SEEK_SECONDS);
        return;
      }
      if (state.reverseTimer) window.clearInterval(state.reverseTimer);
      gesture.classList.remove('visible');
      video.playbackRate = state.originalRate;
      if (state.wasPaused) {
        video.pause();
        status.textContent = '已暂停';
      } else {
        video.play().catch(() => {});
        status.textContent = '正在播放';
      }
    };

    const arrowDirection = (event) => {
      if (event.key === 'ArrowRight' || event.code === 'ArrowRight' || event.keyCode === 39) return 1;
      if (event.key === 'ArrowLeft' || event.code === 'ArrowLeft' || event.keyCode === 37) return -1;
      return 0;
    };
    window.addEventListener('keydown', (event) => {
      const direction = arrowDirection(event);
      if (!direction) return;
      if (event.target && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
      event.preventDefault();
      event.stopPropagation();
      if (!event.repeat) beginArrowHold(direction);
    }, true);
    window.addEventListener('keyup', (event) => {
      if (!arrowDirection(event)) return;
      event.preventDefault();
      event.stopPropagation();
      finishArrowHold(false);
    }, true);
    window.addEventListener('blur', () => finishArrowHold(false));
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) finishArrowHold(false);
    });
    video.addEventListener('click', () => video.focus({ preventScroll: true }));
    document.body.tabIndex = -1;
    document.body.focus({ preventScroll: true });
    try {
    if (window.Hls && Hls.isSupported()) {
      const hls = new Hls({ enableWorker: false });
      let networkRetries = 0;
      let mediaRetries = 0;
      window.__videoSearchHls = hls;
      hls.loadSource(source);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        status.textContent = '播放清单已加载，正在缓冲…';
        video.play().catch(() => { status.textContent = '点击播放按钮开始观看'; });
      });
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (!data.fatal) return;
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR && networkRetries++ < 2) {
          status.textContent = '线路请求失败，正在重试…';
          hls.startLoad();
        } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR && mediaRetries++ < 2) {
          status.textContent = '媒体解码异常，正在恢复…';
          hls.recoverMediaError();
        } else {
          status.textContent = `播放线路不可用：${data.details || data.type || '未知错误'}`;
          hls.destroy();
        }
      });
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      // Safari 等真正提供原生 HLS 的浏览器才使用此回退路径。
      video.src = source;
      status.textContent = '播放清单已加载，正在缓冲…';
      video.play().catch(() => { status.textContent = '点击播放按钮开始观看'; });
    } else {
      status.textContent = 'HLS.js 加载失败或当前浏览器不支持 HLS';
    }
    } catch (error) {
      status.textContent = '播放器初始化失败：' + String(error && error.message || error);
    }
"""
    return script.encode("utf-8")


def create_handler(title: str, watch_url: str, master_url: str, registry: UrlRegistry, activity):
    """创建绑定当前播放会话的 HTTP 处理器。"""
    master_token = registry.register(master_url)

    class PlayerHandler(BaseHTTPRequestHandler):
        server_version = "VideoSearchPlayer/0.2"

        def log_message(self, _format, *_args):
            return

        def send_common_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")

        def do_OPTIONS(self):
            activity["last"] = time.monotonic()
            self.send_response(204)
            self.send_common_headers()
            self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.end_headers()

        def do_HEAD(self):
            self._handle(send_body=False)

        def do_GET(self):
            self._handle(send_body=True)

        def _handle(self, send_body: bool):
            activity["last"] = time.monotonic()
            if self.path == "/":
                body = render_player(title)
                self.send_response(200)
                self.send_common_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
                    "media-src 'self' blob:; connect-src 'self'; style-src 'unsafe-inline'",
                )
                self.end_headers()
                if send_body:
                    self.wfile.write(body)
                return
            if self.path == "/player.js":
                body = render_player_script()
                self.send_response(200)
                self.send_common_headers()
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if send_body:
                    self.wfile.write(body)
                return
            if self.path == "/master.m3u8":
                token = master_token
            elif self.path.startswith("/media/"):
                media_name = self.path[len("/media/") :].split("?", 1)[0]
                token = media_name.split(".", 1)[0]
            else:
                self.send_error(404)
                return
            try:
                self._proxy(token, send_body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # 浏览器切换清晰度、拖动进度或关闭页面时可能主动中断旧请求。
                return
            except (HTTPError, URLError, TimeoutError, KeyError, ValueError, OSError):
                # HTTP 状态原因必须使用 latin-1 可编码文本。
                self.send_error(502, "Bad Gateway")

        def _proxy(self, token: str, send_body: bool):
            upstream_url = registry.get(token)
            range_header = self.headers.get("Range")
            with fetch_bytes(upstream_url, watch_url, range_header) as response:
                final_url = registry._guard.validate(response.geturl())
                content_type = response.headers.get("Content-Type", "application/octet-stream")
                is_manifest = (
                    "mpegurl" in content_type.lower()
                    or urlparse(final_url).path.lower().endswith(".m3u8")
                )
                if is_manifest:
                    raw = response.read(MAX_MANIFEST_BYTES + 1)
                    if len(raw) > MAX_MANIFEST_BYTES:
                        raise ValueError("HLS 清单响应过大")
                    body = rewrite_manifest(
                        raw.decode("utf-8", errors="replace"), final_url, registry
                    ).encode("utf-8")
                    self.send_response(response.status)
                    self.send_common_headers()
                    self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if send_body:
                        self.wfile.write(body)
                    return

                self.send_response(response.status)
                self.send_common_headers()
                for header in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                    value = response.headers.get(header)
                    if value:
                        self.send_header(header, value)
                self.end_headers()
                if send_body:
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        try:
                            self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            return

    return PlayerHandler


def open_system_browser(url: str) -> bool:
    """使用操作系统默认浏览器打开页面，不依赖 Agent 内嵌浏览器。"""
    try:
        return bool(webbrowser.open(url, new=2))
    except (OSError, webbrowser.Error):
        return False


def serve(
    title: str,
    watch_url: str,
    idle_seconds: int,
    max_seconds: int,
    open_browser: bool = False,
) -> None:
    """启动本地播放器，空闲或达到最长时间后自动关闭。"""
    guard = PublicUrlGuard()
    master_url = extract_manifest_url(watch_url, guard)
    registry = UrlRegistry(guard)
    activity = {"last": time.monotonic()}
    handler = create_handler(title, watch_url, master_url, registry, activity)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.timeout = 1
    started = time.monotonic()
    local_url = f"http://127.0.0.1:{server.server_port}/"
    print(json.dumps({"title": title, "local_url": local_url}, ensure_ascii=False), flush=True)
    if open_browser and not open_system_browser(local_url):
        print("无法自动打开系统浏览器，请手动打开 local_url", file=sys.stderr, flush=True)
    try:
        while time.monotonic() - started < max_seconds:
            server.handle_request()
            if time.monotonic() - activity["last"] > idle_seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 ZIP0 本地 HLS 播放器")
    parser.add_argument("--watch-url", required=True)
    parser.add_argument("--title", default="视频播放")
    parser.add_argument("--idle-seconds", type=int, default=900)
    parser.add_argument("--max-seconds", type=int, default=14400)
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="使用操作系统默认浏览器打开播放器",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.idle_seconds < 30 or args.max_seconds < 30:
            raise ValueError("运行时限不能少于 30 秒")
        serve(
            args.title,
            validate_zip0_watch_url(args.watch_url),
            args.idle_seconds,
            args.max_seconds,
            args.open_browser,
        )
        return 0
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
