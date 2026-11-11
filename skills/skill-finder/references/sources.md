# 来源配置与接入

## 命令

把 `{baseDir}` 替换为本 Skill 的实际目录。所有输出均为 JSON。

```bash
python3 "{baseDir}/scripts/finder.py" sources list
python3 "{baseDir}/scripts/finder.py" sources add --file "新来源.json"
python3 "{baseDir}/scripts/finder.py" sources disable skills-sh
python3 "{baseDir}/scripts/finder.py" sources enable skills-sh
python3 "{baseDir}/scripts/finder.py" sources remove my-community
python3 "{baseDir}/scripts/finder.py" sources check skillsmp --query "pdf"
python3 "{baseDir}/scripts/finder.py" --config "本地配置.json" search "pdf" --source skillsmp
```

`sources check` 执行一次有限查询；需要宿主工具的来源会返回后续任务，这不代表已完成连接检查。查询 HTTP 源时禁用缓存。禁用内置来源仅写 `{id, enabled}` 覆盖；移除仅用于自定义来源，用户明确要求重配已有源时才用 `add --replace`。

默认配置是 `assets/sources.json`。本地配置使用同样的 `{"version":1,"sources":[...]}` 外层结构。记录按 `id` 浅合并；覆盖 `fields` 等对象时提供完整对象。添加命令的输入文件只包含一条来源对象。

默认启用全部六个来源。没有 API/CLI 访问条件时仍尝试站点检索；不能静默将其从覆盖说明中去掉。

## 类型

| type | 用途 | 必需字段 |
| --- | --- | --- |
| `website` | 任意公开技能站点，Agent 站点检索和读页面 | `id`, `url` |
| `repository` | GitHub 仓库，Agent 用 gh 核对树和文件 | `id`, `url` |
| `directory` | 用户提供的本地 Skill 目录集合 | `id`, `directory` |
| `http` | 可映射 JSON 结果的 REST 搜索 | `id`, `url`, `fields.name` |
| `agent` | 需要宿主 CLI 或专用工具的来源 | `id`, `url` |

所有类型可设置布尔 `enabled`。目录源扫描实际的 SKILL.md，并按名称、description 和正文关键词查找；需要 PyYAML。目录、请求 URL 和自定义认证信息只放用户本地。

`website` 可以发现搜索引擎已索引的内容；仅添加 URL 不承诺拿到全部内容和完整指标。其他 Git 托管平台暂用网站类型发现，再通过该平台工具核验。

## JSON API 示例

以下是配置模板，不是正式收录或经过验证的站点。先替换地址并核对真实返回格式。

```json
{
  "id": "my-api",
  "type": "http",
  "enabled": true,
  "url": "https://example.com/api/skills/search",
  "query_param": "q",
  "limit_param": "limit",
  "items_path": "data.items",
  "fields": {
    "name": "name",
    "id": "id",
    "description": "summary",
    "repository": "githubUrl",
    "path": "skillPath",
    "url": "detailUrl",
    "downloads": "downloads"
  },
  "metric_scopes": {"downloads": "skill"}
}
```

字段路径支持对象的点号路径；结果列表在 JSON 根部时设 `items_path` 为 `""`。数组内部索引、HTML、GraphQL 或特殊签名协议需要单独适配。响应列表或映射名称缺失时返回 `schema_error`，不伪装成空列表。

可映射 `name`、`id`、`display_name`、`description`、`author`、`publisher`、`repository`、`path`、`url`、`installs`、`downloads`、`stars`、`versions`、`reported_updated_at`。

- `repository` 可为 `owner/repo`、GitHub 仓库地址或文件/目录 URL。URL 推导的分支与路径仍须上游验证。
- `path` 是实际仓库内 SKILL.md 相对路径，不能凭惯例拼出不存在的目录。
- `metric_scopes` 使用 `skill`、`repository` 或 `source_reported` 等明确的作用范围。
- `auth_env` 表示必需 Bearer token 的环境变量名称；未设置时返回 `auth_required`。
- `optional_auth_env` 表示可选认证；没有时执行匿名请求。
- URL 不接受内嵌账号密码和常见凭据参数。不要把密钥写进配置、命令参数或公开日志。
- `listing_template` 可包含 `{id}`，仅用于已知的详情页 URL 规则。
- `registry` 与映射的 `publisher`、Skill `name` 一起构成注册表身份；不要跨不同发布者按 slug 合并。

API 请求验证 TLS，限制单响应四 MiB、每来源每关键词有限结果和单请求超时，不自动重试。公共未认证请求本地缓存十分钟；缓存放配置所在目录的 `cache/` 下。读写缓存失败不改变来源认证策略；`--refresh` 重查。

## 首批来源的实际边界

| 来源 | 脚本/Agent 分工 | 核对依据与边界 |
| --- | --- | --- |
| skills.sh | 脚本查询 `/api/search` | Vercel skills 的 `src/find.ts`；返回安装量，通常不返回真实目录路径 |
| SkillsMP | 脚本查询 `/api/v1/skills/search` | 官方 API 文档；匿名和认证配额不同，GitHub stars 为仓库数据 |
| ClawHub | 脚本查询 `/api/v1/search` | 官方 HTTP API；按发布者与 slug 区分，处理 `429` 和版本/指标缺失 |
| LobeHub | Agent 使用官方 CLI 或网站 | 官方市场说明；实例和身份需匹配，原始 JSON 可通过 `import-results` 转换 |
| 腾讯 SkillHub | Agent 使用已有官方 CLI 或网站 | 官方安装说明；CLI 文本需据实提取，核对 Skill、Plugin 和付费服务的不同安装单元 |
| GitHub | Agent 使用 gh 或站点检索发现 | gh code search 通常需要认证；仓库默认分支、实际路径和安装范围需核验 |

来源文档地址保存在默认配置中，更新适配前重新读取；不能从同名本机安装推断其公开来源。站点排行榜、评价和仓库 stars 均不能直接证明业务效果。

## 后续任务

`search` 返回 `followups`，包括来源 ID、官方说明、站点限定查询及可用的 `cli_argv`。它们是供 Agent 核对和执行的检索建议，脚本不执行这些数组。

LobeHub 的 CLI 可以连接多个市场。执行前按当前官方说明限定到 `market.lobehub.com`，使用该实例已有身份，避免把另一个实例的搜索结果归到 LobeHub。缺少身份时公开检索即可完成发现；不要为搜索自动覆写既有注册。

读取可用的 CLI JSON 后：

```bash
python3 "{baseDir}/scripts/finder.py" import-results --source lobehub --input "官方CLI结果.json"
```

没有字段映射时，Agent 生成实际候选 JSON，再用 `merge` 合并。保留 API 与替代查询各自的状态；通过网页发现少数候选不表示 API 已恢复或完整覆盖全站。
