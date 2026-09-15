# 视频搜索 Skill

一个面向 Codex 的视频搜索 Skill。默认调用 ZIP0 无需认证的公开发现 API，列出候选后通过仅监听本机的 HLS 播放器观看；失败时回退到 ZIP0 站内观看页。Internet Archive、TVmaze 和用户自有苹果 CMS 作为兼容来源。

## 安装

对 Agent 说：`请从 https://github.com/xinyu68/video-search-skill 安装 video-search Skill。`

## 功能

- 使用连续编号展示 ZIP0 搜索结果
- 校验 ZIP0 站内观看页并提取当前 HLS 清单
- 使用 HLS.js 和受限本机代理播放，处理媒体分片跨域问题
- 使用系统默认浏览器打开播放器，不依赖 Agent 内嵌浏览器
- 支持方向键短按前进/后退 10 秒和长按 2 倍速快进/倒退
- 播放失败时回退到 ZIP0 站内页面
- 搜索带明确开放许可标记的 Internet Archive 视频
- 使用连续编号选择作品和媒体文件
- 展示格式、分辨率、文件大小与许可信息
- 生成支持 MP4、WebM、OGV 和 HLS 的本地播放器
- 查询 TVmaze 影视元数据
- 解析已授权的苹果 CMS V10 JSON 响应
- 并行搜索多个在线苹果 CMS 来源，单源失败不影响其他来源
- 按候选、线路和剧集编号完成选择与播放

本机代理只接受当前播放清单动态登记的公网 HTTPS 地址，不是任意 URL 代理。项目不向用户暴露或保存媒体直链，不绕过登录、会员、验证码、DRM 或地区限制，也不提供视频下载功能。

## 本地验证

```powershell
python -m unittest discover -s tests -v
python video-search/scripts/video_search.py search "影片名称" --catalog search.json
python video-search/scripts/video_search.py select search.json --index 1 --episode 1
python video-search/scripts/local_hls_player.py --watch-url "https://zip0.com/watch?..." --title "影片名称" --open-browser
```

Skill 入口位于 `video-search/SKILL.md`。

苹果 CMS 兼容模式的配置格式见 `video-search/references/cms-config.md`。

播放器架构参考了 [LunaTV](https://github.com/MoonTechLab/LunaTV) 使用 HLS.js 播放 HLS 内容的思路；本项目未复制 LunaTV 源代码。LunaTV 使用 CC BY-NC-SA 4.0，本项目播放器为独立实现。
