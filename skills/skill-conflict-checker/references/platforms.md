# 平台专用发现与裁决依据

这些说明是 Claude Code、TRAE 和 Codex 的核对入口，不是通用优先级标准。其他平台使用通用适配流程，不能沿用本页目录与裁决规则。编写时核对日期为 2026-11-09；每次审计记录自己的查阅日期、版本和运行形态。网页更新、当前版本行为及可见清单不一致时，分别保留证据。

## 通用格式

[Agent Skills specification](https://agentskills.io/specification) 定义 `SKILL.md`、YAML frontmatter 和资源组织，不定义所有宿主的目录加载和指令层级。

通用名称规则为小写字母、数字和单连字符，最多 64 字符，不能首尾连字符或连续连字符；`description` 非空且最多 1024 字符。目录名称与 `name` 一致。宿主可支持额外字段和历史格式；脚本发现格式偏差时，不将它等同于实际加载失败。

## Claude Code

官方入口：[Skills](https://code.claude.com/docs/en/skills)、[Memory](https://code.claude.com/docs/en/memory)、[Settings](https://code.claude.com/docs/en/settings)、[Plugins](https://code.claude.com/docs/en/plugins)。

- 发现线索包括用户 `~/.claude/skills/`、项目 `.claude/skills/`、祖先/嵌套项目、附加目录、企业来源、插件及当前会话明确提供的其他来源。
- 官方同名选择规则为企业 > 个人 > 项目。适用对象是同名 Skill 的解析，不能据此推导所有正文之间的权限。
- 插件技能通常使用 `plugin-name:skill-name` 命名空间，与未加命名空间的同名技能并存。核对实际插件名称与调用名，不仅比较 frontmatter。
- `disable-model-invocation: true` 控制模型自动调用；`user-invocable: false` 控制用户菜单可见性。两者不等于物理文件被删除、通用禁用或正文绝不加载。
- 按任务核对 `$ARGUMENTS`、`${CLAUDE_SKILL_DIR}`、动态命令、`context: fork`、`agent`、工具授权等扩展。审计时静态读取，不能展开动态命令。
- 兼容的 `.claude/commands/` 可能参与命令名竞争。自带脚本只处理 `SKILL.md`，此类文件需宿主单独枚举和阅读。

默认脚本检查用户目录及当前目录至最近 Git 根的项目技能目录。没有 Git 标记时只检查指定项目。更高祖先、嵌套项目、企业目录、附加目录和插件用 manifest 补充；不要将此保守扫描边界误报为 Claude 的完整发现算法。

同时核对适用 CLAUDE.md、路径规则、设置优先级和启用插件配置。脚本不猜测插件缓存中哪些版本当前启用，也不自动解析整份插件设置。

## Codex

官方入口：[Skills](https://developers.openai.com/codex/skills)、[AGENTS.md](https://developers.openai.com/codex/guides/agents-md)、[Configuration](https://developers.openai.com/codex/config-reference)。

- 项目线索为当前工作目录至仓库根的 `.agents/skills/`，用户线索为 `~/.agents/skills/`；POSIX 管理员线索为 `/etc/codex/skills/`。管理员、内置与插件按目标环境及运行时核对，再用 manifest 纳入，不将本机系统根当成离线目标的来源。
- 当前官方说明：同名技能不会合并，多个副本可以同时供选择。不能套用 Claude 的同名覆盖规则。
- 符号链接可以形成多个入口。保留入口路径、真实路径和来源；同内容不表示同作用域或同调用身份。
- 用户 `~/.codex/config.toml` 可用 `[[skills.config]]` 配置 `path` 和 `enabled = false`。路径可能指向 Skill 目录或 SKILL.md；脚本匹配两种绝对路径及其真实路径。
- `agents/openai.yaml` 中 `policy.allow_implicit_invocation: false` 限制隐式调用，不表示显式调用被禁止。
- 插件、内置、管理员目录及远程来源用当前清单和配置定位。不能因为共享 `.agents/skills/` 与 TRAE 共用文件，就判定两个平台在同一请求中共同加载。

默认脚本仅解析标准用户位置中的技能禁用声明，且仅在启用默认发现时执行。自定义 `CODEX_HOME`、项目配置、管理员配置、配置覆盖、自定义 AGENTS 文件名和其他版本差异，由宿主核对并用 manifest 提供来源、治理文件和状态证据。配置声明不证明运行进程已经应用配置。

AGENTS.md 与 AGENTS.override.md 按适用路径逐级核对；默认脚本枚举仓库路径上的候选和标准用户候选，正文权限仍由宿主规则决定。没有 Git 标记时，仓库边界需补证。Windows 的管理员或安装路径不能从 POSIX 示例推导，使用目标环境的真实入口。

## TRAE 国内版与国际版

官方入口：[国内版文档索引](https://docs.trae.cn/llms.txt)、[国内版 Skills](https://docs.trae.cn/ide_skills)、[国际版文档](https://docs.trae.ai/)。

- 产品身份来自当前会话、产品设置或当前内置路径，不从目录存在推断。不同地区、IDE/Work、版本和远程形态分别记录。
- 项目技能目录是 `<project>/.trae/skills/`。用户目录按产品区分：国内版 `~/.trae-cn/skills/`，国际版 `~/.trae/skills/`。
- `.agents/skills/` 导入需要核对该版本的开关和作用域。默认脚本将其作为可发现候选，启用状态保持未知。
- 官方对 `.trae` 来源与 `.agents` 同名技能有选择说明；按目标版本核对。不能扩大为“所有项目、用户、内置与插件之间已经有完整优先级表”。
- 当前会话可能暴露多个内置 root、平台/模型来源、插件和其他用户目录。只从本次清单提取实际路径，保留来源，不遍历机器上全部历史 scope 或插件缓存。
- 默认脚本只查产品用户目录、项目 `.trae/skills/` 和候选 `.agents/skills/`。内置与插件由 runtime 或 sources 提供；单纯复制文件不能验证路由器加载。

TRAE 全局/项目 Rules、AGENTS.md、当前会话可见约束和导入配置由宿主核对；没有稳定公开配置 schema 时，不猜文件字段或排序。国际版能力差异需复核其官方入口，不能用国内版结论直接覆盖。

## 记录三层不同证据

| 层次 | 要回答的问题 | 可用证据 |
| --- | --- | --- |
| 来源与名称解析 | 哪些入口暴露，调用名指向哪个副本 | 官方规则、当前配置、运行时清单、可见调用 |
| 选择和正文加载 | 描述是否参与选择，正文是否读过 | 路由清单、显式调用或正文读取记录 |
| 正文要求的裁决 | 两条相反要求由谁决定行为 | 可见宿主/用户/治理权限、适用例外与委托 |

被覆盖的副本可能导致修改不生效，属于来源问题；只有共同生效的相反要求才进入冲突裁决。没有隐藏系统提示词也能审计可见范围，明确缺口即可。
