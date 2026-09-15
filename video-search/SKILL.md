---
name: video-search
description: 使用 ZIP0 公开发现 API 搜索电影、电视剧、综艺或动漫，按编号选择后通过本机 HLS 播放器观看，失败时回退到 ZIP0 站内页面；也可显式搜索开放视频或用户配置的苹果 CMS。不用于下载或绕过访问限制。
---

# ZIP0 视频搜索

使用 `scripts/video_search.py` 搜索和选择结果，使用 `scripts/local_hls_player.py` 启动本机播放器。播放器采用类似 LunaTV 的 HLS.js 架构，但代码为本项目独立实现。

## 搜索并打开播放页

1. 搜索并保存候选：

   ```powershell
   python scripts/video_search.py search "关键词" --limit 10 --catalog <search.json>
   ```

2. 展示连续编号、标题、年份、分类、地区、评分、更新状态、集数和线路标识。存在多个合理候选时，等待用户回复显示编号；多集内容还要询问集数。
3. 按用户选择取得经过校验的 ZIP0 观看页：

   ```powershell
   python scripts/video_search.py select <search.json> --index <编号> --episode <集数>
   ```

4. 用 `open_url` 启动本机播放器，并让脚本调用操作系统默认浏览器；从命令第一行读取 `local_url`，进程保持运行：

   ```powershell
   python scripts/local_hls_player.py --watch-url <open_url> --title <标题> --open-browser
   ```

5. 不要默认调用 Agent、IDE 或 Codex 的内嵌浏览器。脚本会使用操作系统默认浏览器；如果自动打开失败，向用户提供 `local_url` 供手动打开。这是用户已明确要求的最终动作，无需再次确认。保留播放器进程；播放器空闲 15 分钟或运行 4 小时后自动关闭，开始新播放前停止旧进程。

本机服务必须只监听 `127.0.0.1`。不要向用户显示、保存或下载页面底层的 `.m3u8`、分片及其他媒体直链。播放器只代理当前清单动态登记的公网 HTTPS 地址，不得改成接受任意 URL 的开放代理。不要尝试处理登录、验证码、会员、DRM 或地区限制。

如果本机播放器无法提取清单或上游返回错误，使用操作系统默认浏览器打开 `open_url`，让 ZIP0 站内播放器作为回退；不要默认调用 Agent 内嵌浏览器。仍失败时返回候选列表选择其他线路。播放器只移除站点页面界面，不承诺移除已嵌入视频流的广告。

## 兼容来源

Internet Archive 开放视频：

1. 在临时目录或用户指定目录创建搜索目录：

   ```powershell
   python scripts/video_search.py search "关键词" --source archive --limit 8 --catalog <search.json>
   ```

2. 向用户展示连续编号、标题、作者、年份和许可。只要存在多个合理候选，就等待用户回复列表中的显示编号。
3. 按用户选择读取可播放文件：

   ```powershell
   python scripts/video_search.py files <search.json> --index <编号> --catalog <files.json>
   ```

4. 展示连续编号、格式、分辨率和大小，等待用户选择文件编号。
5. 生成播放器：

   ```powershell
   python scripts/video_search.py player <files.json> --index <编号> --output <player.html>
   ```

6. 通过仅监听 `127.0.0.1` 的临时 HTTP 服务打开播放器。播放结束或测试完成后停止服务；不要把播放器暴露到局域网。

若用户只需要作品资料，使用 `--source tvmaze`，并明确说明结果是元数据页面而不是播放源。用户需要自有苹果 CMS 时，阅读 [苹果 CMS 配置](references/cms-config.md)，并使用 `--source cms`。

## 本地苹果 CMS 数据

使用 `parse-maccms`、`episodes`、`probe` 和 `make-player` 解析用户提供的本地响应。先列影视候选，再列线路和剧集，并分别等待用户按显示编号选择。

ZIP0 及其上游内容由第三方提供；搜索成功不代表内容获得授权。只协助用户访问其有权观看的内容，不自动提取浏览器 Cookie，不破解签名、验证码、会员接口或 DRM，也不下载视频。
