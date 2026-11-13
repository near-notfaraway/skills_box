# skills_box

Skills 工具箱，用于收藏好用的 AI Agent Skills，方便更换机器、工具或工作区后找回并安装。原创 Skills 保存完整文件，第三方 Skills 保存来源和安装说明；第三方默认安装上游最新内容。

## Skills 列表

### 原创 Skills

| Skill | 分类 | 用途 |
| --- | --- | --- |
| [agent-creator](skills/agent-creator/SKILL.md) | 开发 | 按需求创建或维护 Agent prompt，并在支持的平台生成 Agent 配置。 |
| [skill-finder](skills/skill-finder/SKILL.md) | 开发 | 跨技能市场、仓库和自定义来源检索，核验上游并比较功能、热度与维护情况。 |
| [skill-conflict-checker](skills/skill-conflict-checker/SKILL.md) | 开发 | 检查共享 `skills/` 目录与 Claude Code、TRAE、Codex 专用来源的技能冲突。 |
| [multi-search-engine](skills/multi-search-engine/SKILL.md) | 研究 | 从 16 个中外搜索入口按任务配置选择并聚合结果，支持多语言检索、失败诊断和来源核验。 |

分类、标签与用途见[原创索引](skills/catalog.yaml)。

`agent-creator` 可用于“生成或优化 Agent prompt”“把已有 prompt 配置成 Agent”及“从需求到配置连续完成”。它区分 prompt 生成、文件式配置和已有 Agent 维护：对 TRAE CLI、Claude Code、Cursor、GitHub Copilot 等已支持的文件式平台，可创建或更新真实配置并核对保存与发现状态；平台不支持或尚未确认文件式创建时，则交付完整 prompt 和必要配置说明，不会把 prompt 已生成误报为 Agent 已创建。完整的平台路径、字段差异、验证边界和维护流程见其 `SKILL.md`。

`skill-finder` 可用于“找一个能做某事的 Skill”“比较这些 Skill”及“添加技能源”。脚本直接检索 skills.sh、SkillsMP、ClawHub；LobeHub、腾讯 SkillHub、GitHub 由 Agent 使用官方 CLI 或公开检索补充。自定义来源保存在本地配置，不覆盖默认列表。脚本需要 Python 3.9+，本地 Skill 读取与 YAML 校验另需 PyYAML；完整流程和限制见其 `SKILL.md`。

`skill-conflict-checker` 可用于“检查不同目录的 Skill 有没有指令冲突”“审计同名技能覆盖”及“比较多平台技能行为”。默认检查指定项目下存在的共享 `skills/`，并结合 Claude Code、TRAE、Codex 的专用来源；其他共享库可用 `--skills-dir` 指定。配套脚本需要 Python 3.11+ 和 PyYAML，负责本地 Agent Skills 格式的只读枚举、解析和候选关系；Agent 结合正文、治理规则与加载证据作语义判断。标准规定单个技能的包格式，不规定所有平台自动读取共享 `skills/`。来源清单、报告模板和验证场景随完整目录一起提供；文件检查不代表目标产品加载行为已验证。

`multi-search-engine` 用于多引擎公开搜索和结果聚合。脚本支持按名称调用全部 16 个中外目录入口，另有 Google News 和 Bing RSS 新闻/网页 feed；`global-web`、`cn-web`、`news`、`privacy`、`computation` 五种任务配置用于减少无关请求。HTTP 解析与可选的浏览器渲染负责搜索，失败回退、结果去重、覆盖诊断和来源正文核验作为增强能力。正文证据满足后停止额外探测；偏隐私入口、WolframAlpha 和具名引擎要求不能由未知宿主后端冒充完成。浏览器路径需要 `agent-browser` CLI 和 Chrome/Chromium；`--check-all` 可逐入口检查，验证页、异常跳转和失败会如实报告，不能把已适配理解为当前环境保证可用。完整用法见 `SKILL.md`。

### 第三方 Skills

| Skill | 分类 | 用途 |
| --- | --- | --- |
| [skill-creator](https://skills.sh/anthropics/skills/skill-creator) | 开发 | 创建、测试和迭代改进 Skill，来源为 `anthropics/skills`。 |
| [agent-browser](https://github.com/vercel-labs/agent-browser) | 开发 | 浏览器自动化，支持网页交互、截图、数据提取和 Web 应用测试，来源为 `vercel-labs/agent-browser`。 |
| [diagram-design](https://github.com/cathrynlavery/diagram-design) | 可视化 | 生成多种适合文档发布的架构图、流程图、时序图和数据图示，来源为 `cathrynlavery/diagram-design`。 |
| [humanizer-zh](https://github.com/op7418/Humanizer-zh) | 写作 | 润色中文文章、评论和文档，减少空话与模板化表达，保留事实、确定程度和作者声音。 |
| [uzi](https://github.com/wbh604/UZI-Skill) | 投资 | A 股、港股和美股深度研究、估值、投资者视角评审、龙虎榜分析及风险识别。 |

来源和安装命令见[单 Skill 索引](third-party-skills/catalog.yaml)。

### 第三方技能集合

| 集合 | 上游形式 | 分类 | 用途 |
| --- | --- | --- | --- |
| [ponytail](https://github.com/DietrichGebert/ponytail) | Claude Code Plugin | 开发 | 包含 `ponytail`、`ponytail-review`、`ponytail-audit`、`ponytail-debt`、`ponytail-gain` 和 `ponytail-help` 六个成员，并可选启用插件 Hook。 |

来源、上游形式、成员名称和安装说明见[集合索引](third-party-skills/collections.yaml)。每集合一条记录，成员不重复加入单 Skill 索引；查找具体成员时搜索集合索引。

Collection 是本仓库的分组约定，不代表一种标准安装格式。上游实际形式与安装方式分别记录，集合中的成员是否能独立安装取决于其依赖关系。

索引用于找回来源和安装，不锁定提交或记录历史测试版本。集合成员路径由 `source.skill_root` 和成员名称确定，实际成员与依赖以安装时的上游说明为准。

## 安装与使用

### 原创 Skills

将 `skills/<name>/` 整个目录复制到目标工具支持的 Skills 目录，保留参考资料、模板和脚本。不要只复制 `SKILL.md`，也不要将 `skills/catalog.yaml` 作为 Skill 安装。

例如，在本仓库根目录执行以下命令，将 `agent-creator` 安装到 Claude Code 用户级目录。该操作需要已安装 Claude Code，且目标目录尚不存在：

```bash
mkdir -p "$HOME/.claude/skills"
cp -R skills/agent-creator "$HOME/.claude/skills/agent-creator"
```

更新已有安装时先比较差异，再替换对应文件。其他工具的安装目录、加载方式与作用范围以其官方说明为准。安装后按对应 `SKILL.md` 的触发条件使用；复制文件本身不代表目标工具已经加载或验证行为。

### 第三方 Skills

从[第三方索引](third-party-skills/catalog.yaml)选择来源，按对应安装命令安装。例如：

```bash
npx --registry=https://registry.npmjs.org --yes skills@latest add https://github.com/anthropics/skills --skill skill-creator -g
```

此命令需要 Node.js 和 npm；目标工具及安装位置由安装器选择流程确定。已有同名安装时，先确认目标和覆盖方式。

安装器和 Skill 均获取最新内容；已有安装不会因索引变更自动更新，需要重新执行安装操作。

`agent-browser` 还需要安装同名 CLI 和浏览器，并通过 CLI 加载匹配版本的操作指南；依赖命令与加载方式见[单 Skill 索引](third-party-skills/catalog.yaml)。

`uzi` 以仓库根目录的 `SKILL.md` 为统一入口，需要保留完整上游仓库。安装时配置独立 Python 环境及 Chromium，运行时使用该环境的解释器；子工作流与共享脚本的工作目录见上游说明。

### 第三方技能集合

按[集合索引](third-party-skills/collections.yaml)的安装方式获取上游最新文件。可独立安装的成员按其说明处理；需要整体安装的集合保留成员目录、共享脚本及参考资料。

本仓库提供 Skill 文件和来源索引。`skill-finder` 可指导 Agent 在用户要求时完成安装；仓库没有统一的自动安装或更新检测服务。

## 仓库结构

```text
skills_box/
├── .gitignore
├── AGENTS.md
├── README.md
├── skills/
│   ├── catalog.yaml
│   ├── agent-creator/
│   │   ├── SKILL.md
│   │   ├── references/
│   │   └── assets/
│   ├── skill-finder/
│   │   ├── SKILL.md
│   │   ├── scripts/
│   │   ├── references/
│   │   └── assets/
│   ├── multi-search-engine/
│   │   ├── SKILL.md
│   │   ├── scripts/
│   │   ├── tests/
│   │   ├── references/
│   │   └── evals/
│   └── skill-conflict-checker/
│       ├── SKILL.md
│       ├── scripts/
│       ├── references/
│       └── assets/
└── third-party-skills/
    ├── catalog.yaml
    └── collections.yaml
```

每个原创 Skill 的目录直接位于 `skills/` 下，并包含带有 `name`、`description` 元数据的 `SKILL.md`。脚本、参考资料和模板按需放入 `scripts/`、`references/`、`assets/`；分类只影响索引，不改变目录位置。`third-party-skills/` 只保存来源索引，不保存第三方文件副本。

## 维护规范

新增与修改内容须遵循 [AGENTS.md](AGENTS.md)：

- 原创 Skills 按 `skill-creator` 的适用规范编写、校验，再同步原创索引与本页列表。
- 第三方单个 Skill 与技能集合分别收录；先核实来源、路径和必要依赖，确认安装方式可用，再加入索引。
- 安装默认获取上游最新内容；来源或安装方式变化时重新确认，不把历史版本写成安装限制。

第三方内容的使用与分发遵循上游许可证。
