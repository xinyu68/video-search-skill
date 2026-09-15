# 苹果 CMS 采集站配置

普通 `search` 命令默认使用采集站。配置文件按以下顺序查找：

1. 命令行 `--config` 指定的文件
2. 环境变量 `VIDEO_SEARCH_SOURCES` 指向的文件
3. Windows 的 `%APPDATA%\video-search\sources.json`
4. `~/.config/video-search/sources.json`

配置文件顶层使用 `sources` 数组。每个来源包含：

- `name`：结果中显示的唯一来源名称
- `api_url`：苹果 CMS V10 `provide/vod` 接口地址；远程接口必须使用 HTTPS
- `enabled`：是否参与搜索，默认启用
- `authorized`：必须明确为 `true`，表示用户有权访问该接口和其中的内容

参考 `sources.example.json` 创建用户自己的配置文件。这里需要的是返回苹果 CMS V10 JSON 的 `provide/vod` 接口，不是影视站首页、导航页或播放器页面。不要将包含私有地址、令牌或账号信息的配置提交到 Git；API 地址不得内嵌账号密码。

脚本一次最多启用 8 个来源。单个来源超时或失败不会中断其他来源，错误会出现在 `source_errors` 中。搜索结果统一重新编号，后续必须使用输出中实际显示的编号。

配置采集站并不代表内容获得授权。只添加你有权访问和播放的来源；不要从第三方导航仓库批量导入未知配置。
