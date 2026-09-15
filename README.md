# 视频搜索 Skill

一个精简的 Codex Skill：在 ZIP0 或剧踪影院搜索影视内容，列出候选供用户按编号选择，然后使用系统默认浏览器打开第三方站内播放页。

## 安装

对 Agent 说：`请从 https://github.com/xinyu68/video-search-skill 安装 video-search Skill。`

## 功能

- 默认搜索 ZIP0，也可指定剧踪影院
- 使用连续编号选择作品、集数、线路或语言版本
- 选择完成后自动调用操作系统默认浏览器
- 只打开经过来源校验的第三方站内播放页

不包含本地播放器、媒体直链解析或视频下载等扩展功能。

## 本地验证

```powershell
python -m unittest discover -s tests -v
python video-search/scripts/video_search.py --help
```

Skill 入口位于 `video-search/SKILL.md`。
