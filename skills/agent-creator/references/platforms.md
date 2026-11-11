# 平台配置

文档基线：2026-11-06，TRAE CLI 0.213.1 的 traex-guide。将基线用作定位线索，实际写入前仍核对当前宿主指南、版本和资源发现方式。不要把一个平台的 frontmatter 当成跨平台统一格式。

## 选择适配方式

| 平台 | 配置方式 | 进入点 |
| --- | --- | --- |
| TRAE CLI | 本地 Markdown 角色文件 | 下方 TRAE CLI；按需使用当前 traex-guide |
| Claude Code | Markdown + YAML frontmatter | 官方 [Subagents](https://code.claude.com/docs/en/sub-agents) |
| Cursor | Markdown + YAML frontmatter | 官方 [Subagents](https://cursor.com/docs/subagents) |
| GitHub Copilot | `.agent.md` 文件 | 官方 [Custom agents](https://code.visualstudio.com/docs/agent-customization/custom-agents) |
| TRAE IDE | 未确认受支持的文件式创建方式时，输出 prompt 供用户自行配置 | 官方 [自定义智能体](https://docs.trae.ai/ide/agent) |
| GPTs | 输出 prompt 供用户自行配置 | 官方 [Creating and editing GPTs](https://help.openai.com/en/articles/8554397-creating-and-editing-gpts) |
| Gemini Gems | 输出 prompt 供用户自行配置 | 官方 [Creating custom Gems](https://support.google.com/gemini/answer/15235603?hl=en) |
| Coze | 未确认文件式适配时，输出 prompt 供用户自行配置 | 官方 [人设与回复逻辑](https://docs.coze.cn/guides/agent_prompt) |
| Dify | 未确认文件式适配时，输出 prompt 供用户自行配置 | 官方 [Agent node](https://docs.dify.ai/en/cloud/use-dify/nodes/agent) |

配置前确认平台能直接读取并加载的文件格式、位置和发现方式。不支持或尚未确认文件式创建时，输出完整 prompt 供用户自行配置；不自动改走浏览器或 API。字段映射仅作为用户手动配置提示。不要把存在 API、支持导出或文件上传等同于已有可完成创建的文件式适配。

## TRAE CLI：默认适配

### 定位文件

- 用户级：共享资源根下 `agents/<name>.md`。共享根由 `TRAE_HOME` 决定，未配置时为 `~/.trae`。
- 项目级：已确认项目根下 `.trae/agents/<name>.md`。
- `TRAECLI_HOME` 是私有运行目录，不是共享 Agent 和 Skill 根。
- 创建前核对目标目录、同名 Markdown/TOML 角色和可能的主配置声明。只读取定位需要的信息，避免展示包含凭据的配置全文。

普通角色优先使用自动发现的 Markdown，通常无需修改 `traecli.toml`。使用小写字母、数字与连字符组成明确标识，使文件名与 `name` 一致。

### 写入格式

下面是可解析的最小示例；按需求替换三个字段和正文：

```markdown
---
name: technical-researcher
description: "需要比较技术方案或查证产品能力时调用，依据需求与约束返回有来源的对比和建议。"
model: inherit
---

负责技术方案调研。依据用户需求和约束比较可行方案，关键判断提供来源，明确未确认的信息。
```

使用结构化文件写入或补丁工具。对包含冒号、引号、换行的元数据使用正确的 YAML 引用，不把 prompt 插入会执行命令替换的 shell 字符串。完整业务 prompt 放在 frontmatter 之后。

### 能力与边界

- `description` 填写非空调用条件。
- 默认使用 `model: inherit`；不凭空指定模型或推理强度。
- `tools`、`disallowed_tools` / `disallowedTools` 在文档基线中会被忽略，不能据此承诺硬性工具限制。
- 需要真实执行限制时，读取当前权限说明并核对生效配置；不要把“只读”正文、Plan 协作模式或其它平台字段当成只读沙箱。
- Agent 的权限模式字段在不同指南与版本中有差异，只有当前实现支持且本次需求需要时才配置，不在最小文件中自动加入 `permission_mode` / `permissionMode`。
- `skills` 不会自动接入资源、执行技能，或重新启用父会话禁用的技能。要求角色使用某技能时核对共享来源与启用状态，再在正文写明使用条件。
- 只有明确要求隔离且项目是有效 Git 仓库时使用受支持的 `isolation: worktree`。
- 不为创建普通角色改动并发、模型提供商、全局权限或未涉及的 MCP 设置。

### 发现与使用

保存后重新读取文件并核对元数据与正文。新会话中使用 `/agents` 检查角色发现；无法直接查看列表时报告“文件已写入，平台发现待新会话确认”。

非交互 `traecli exec` 会话可能采用 `never` 审批策略，且项目 `.trae` 目录可能受只读保护。遇到此情况报告真实写入限制，并在允许审批的运行环境中完成授权写入；不要自动降低沙箱限制，也不要将其误判为不支持文件式创建。`debug prompt-input` 不是角色列表，不能以其中未出现角色名称断言角色未被发现。

`/agents` 展示可用角色；`/subagents` 展示已启动线程。创建文件不启动子 Agent，`@` 引用也不等于启动角色。用户授权试运行后才安排对应任务。

维护时沿用实际生效的定义。在共享根下 `backups/agent-creator/` 等非 Agent 扫描目录保存原文件，不把备份 `.md` 放进 `agents/`。对已存在的 TOML 角色按原格式维护，不默认迁移成 Markdown。

## Claude Code

- 项目级：`.claude/agents/<name>.md`；用户级：`~/.claude/agents/<name>.md`。
- 最小元数据采用 `name` 和非空 `description`，正文作为系统 prompt；模型可省略以沿用平台默认值。
- 工具与权限字段按当前官方文档核对，不能复制 TRAE CLI 的字段行为。
- 对明确只读的代码审查角色，可使用经过当前文档核对的 `tools: Read, Grep, Glob`；需要其它工具时逐项匹配真实任务和可用工具。
- 维护时保留未涉及的技能、MCP、hooks、模型等配置；不要用其它平台模板覆盖现有 frontmatter。
- 按当前版本通过 `/agents` 或角色管理界面核对发现，新建目录或更新需要重载时说明操作。

## Cursor

- 项目级：`.cursor/agents/<name>.md`；用户级：`~/.cursor/agents/<name>.md`。
- 最小元数据采用 `name`、`description`，正文放执行指令；默认可用 `model: inherit`。
- `description` 用于委派选择。只有任务明确需要只读且当前版本支持时设置 `readonly: true`。
- 不因示例来自 Claude Code 就假定其 `tools`、`permissionMode` 或其它字段在 Cursor 生效。
- 优先修改原生路径。兼容目录和同名优先级随版本核对，避免创建第二个同名角色导致覆盖或歧义。
- 核对文件发现和实际工具行为；未经授权不为测试委派而启动子任务。

## GitHub Copilot

- 项目级常用 `.github/agents/<name>.agent.md`；用户级目录、字段与工具约束取决于所选 harness，按当前官方文档核对。
- 采用 `name`、`description` 与正文；工具列表只使用该 harness 当前支持的名称。
- 在支持相应别名的 harness 中，只读角色可选 `tools: ['read', 'search']`；不要把此示例视为所有 harness 通用限制。
- 默认不添加模型固定、handoff、自动发送或子 Agent 配置，除非用户要求。
- 在 Agent Customizations 编辑器和新会话的 Agent 选择器核对发现。仅能解析文件时如实报告验证范围。

## 不支持文件式创建的平台：手动配置交付

1. 根据当前官方说明核对文件式能力。没有文件式入口或尚未确认适配时，进入正常的 prompt 交付流程，准确区分“不支持”与“尚未确认”。
2. 根据需求生成完整 prompt；已有 prompt 时保留其语义。输出正文之外的名称、简介或调用描述，以及确实需要的工具和知识配置提示。
3. 在回答中给出可直接复制的完整正文，不能只给摘要、字段清单或文件路径。用户要求文件时另行保存 prompt 文件，该文件不代表平台已经创建 Agent。
4. 明确说明“Prompt 已生成，需用户自行配置”，给出简短的粘贴位置和保存提示；保存后重新打开条目，核对名称和完整 Instructions。只描述已确认的入口，不猜测按钮或登录流程。
5. 不要求登录、工作空间权限、凭据或账号信息；不使用浏览器、API 或非官方内部文件创建、更新、发布或分享。
6. 手动维护时交付更新后的完整 prompt 和变更说明；平台实际写回由用户完成。

### 字段映射与平台差异

| 平台 | Prompt 与元数据 | 配置注意事项 |
| --- | --- | --- |
| TRAE IDE | 名称、提示词；开启“可被其他智能体调用”后填写英文标识与“何时调用” | MCP 与内置工具分别配置；是否开启调用由角色用途决定 |
| GPTs | 名称、简介、Instructions；开场问题属于用户展示 | 知识文件、能力和 Actions 单独配置；保存和分享流程以当前编辑器为准 |
| Gemini Gems | 名称、Instructions | 知识文件单独添加；预览不会自动保存，必须执行 Save |
| Coze | 当前版本的人设与回复逻辑、名称与描述 | 低代码和新版入口可能不同；prompt 引用的插件或工作流需绑定真实资源 |
| Dify | Agent 的角色、目标、上下文指令；Query 传入具体任务 | 先确认是独立 Agent 应用还是工作流节点；节点不能当成已部署的独立 Agent |

GPTs 帮助中心正文在基线调研时遇到验证页，字段说明结合官方搜索摘要整理，提示用户以当前编辑器字段为准。Dify 支持策略、工具、迭代与记忆等独立配置；未核实文件式创建方式时交付 prompt 和节点配置提示，不把此交付描述为已部署的独立 Agent。

## 不支持或未知的平台

先完成通用 prompt，再核对当前官方文件格式和加载方式。只有已确认能通过受支持文件完成创建时才写入配置；否则输出完整 prompt，说明文件式能力未确认并供用户自行配置。不要猜测专有格式，也不自动切换到浏览器或 API。
