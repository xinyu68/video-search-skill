---
name: video-search
description: 在 ZIP0 或剧踪影院搜索电影、电视剧、综艺和动漫，按编号选择后用系统默认浏览器打开第三方网站播放页。不用于下载视频或绕过访问限制。
---

# 视频搜索

只完成三件事：搜索第三方影视网站、让用户按显示编号选择、用操作系统默认浏览器打开第三方站内播放页。不要提取媒体直链，不要创建本地播放器，也不要使用 Agent、IDE 或 Codex 的内嵌浏览器。

## 默认来源：ZIP0

搜索并保存候选：

```powershell
python scripts/video_search.py search "关键词" --catalog <search.json>
```

展示连续编号、标题、年份、分类、语言、更新状态和集数。存在多个候选时等待用户回复编号；多集内容还要确认集数。随后选择结果，命令会自动调用系统默认浏览器：

```powershell
python scripts/video_search.py select <search.json> --index <编号> --episode <集数>
```

## 剧踪影院

仅在用户明确指定剧踪，或 ZIP0 没有合适结果时使用：

```powershell
python scripts/video_search.py search "关键词" --source juzong --catalog <search.json>
python scripts/video_search.py juzong-detail <search.json> --index <编号> --catalog <detail.json>
python scripts/video_search.py juzong-select <detail.json> --index <播放项编号>
```

先展示作品候选并等待用户选择，再展示线路、语言或剧集选项并再次等待用户选择。最后一个命令会自动调用系统默认浏览器。

剧踪的匿名搜索可能按站点要求等待约 6 秒。不要缩短间隔、并发请求、复用浏览器 Cookie、处理验证码或逆向站点令牌。

## 失败处理

浏览器无法自动打开时，把脚本返回的 `open_url` 提供给用户手动打开。页面无法播放时，让用户换候选或线路；不要尝试解析底层视频地址。

第三方内容的授权、广告、可用性和兼容性由相应站点负责。不破解登录、会员、验证码、地区限制或 DRM，不下载视频。
