# 来源清单与脚本使用

## 环境和调用

脚本使用 Python 3.11+（标准库 `tomllib`）和 PyYAML。依赖缺失时明确退出，不用正则替代 YAML 解析。推荐在仓库外或目标环境允许的本地临时目录建立虚拟环境：

```bash
python3 -m venv "<local-dir>/venv"
"<local-dir>/venv/bin/python" -m pip install --index-url https://pypi.org/simple PyYAML
"<local-dir>/venv/bin/python" "<skill-dir>/scripts/checker.py" --platform codex --project "<project-dir>" --output "<local-dir>/inventory.json"
```

替换所有占位路径，先确认输出父目录存在。Windows 使用 `python` 和 `venv\Scripts\python.exe`，其他参数不变；脚本没有 Bash、Ruby、Git CLI 或平台 API 依赖。上述安装只提供审计解析器，不安装被审计技能。

| 参数 | 含义 |
| --- | --- |
| `--platform` | 小写字母、数字和单连字符的标识，最多 64 字符，可重复；专用适配为 `claude-code`、`codex`、`trae-cn`、`trae`，其他标识或 `generic` 使用通用适配；未提供时从 manifest 推导 |
| `--project` | 目标项目/工作目录，默认当前目录；TRAE 需给实际项目根，Codex 可给仓库内工作目录 |
| `--home` | 被审计环境的用户目录，默认当前用户 home；用于离线挂载或隔离测试 |
| `--manifest` | 外部本地 UTF-8 JSON 来源清单；相对路径以此文件的父目录为基准 |
| `--skills-dir` | 共享技能根，可重复；相对路径以命令 cwd 为基准，应用到所有目标平台，状态仍需逐平台确认 |
| `--no-defaults` | 只查显式 sources、runtime、governance 和 `--skills-dir`，不自动发现项目 `skills/`、平台目录或读用户 Codex 配置 |
| `--output` | 新 JSON 文件；省略时输出到 stdout，不覆盖已有文件、不创建父目录 |
| `--max-depth` | 每个来源的目录递归上限，默认 6 |
| `--max-files` | 每个来源最多 2000 个 Skill 入口 |
| `--max-bytes` | 每个被审计文件最多读取 1 MiB；manifest 独立限制为 1 MiB |

默认将指定项目下存在的 `skills/` 纳入共享目录候选；有专用适配时另外读取对应候选根和治理文件，具体边界见平台规则。其他来源通过 `--skills-dir` 或 manifest 明确提供，详见 [通用目录与平台适配](generic-platforms.md)。目标在远程主机/容器时，在目标环境执行，或明确挂载路径和局限；不能使用本机 home 代替远程状态。运行前检查来源范围，避免把用户整个 home 或仓库根当成技能 root。

## Manifest v1

从 `assets/sources.example.json` 复制模板到本地输出目录，再填写观察到的来源。只接受以下顶层字段，不认识的字段直接报错，防止静默漏项：

- `version`：固定为 1。
- `sources`：技能 root、单个技能目录或单个 SKILL.md；路径需来自可见配置、用户指定或运行时。
- `runtime`：当前会话提供的**精确 SKILL.md 路径**和状态。路径不属于 sources 时也会纳入枚举。
- `governance`：需阅读的规则/配置文件，脚本只报告存在性和内容哈希，不导出正文。
- `runtime_complete`：平台到布尔值的映射。省略表示未知，false 表示已知不完整；只有确有完整本次选择清单时才填写 true。

来源条目字段：

| 字段 | 要求 |
| --- | --- |
| `id` | 唯一非空字符串；`default:`、`runtime:` 为脚本保留前缀 |
| `platform` | 与 `--platform` 相同格式的平台标识；自定义标识保留其独立身份，不自动映射到三个产品 |
| `path` | 非空字符串；允许 `~`、相对路径和绝对路径 |
| `kind` | 自述来源类别，例如 project、personal、builtin、plugin、explicit；这不是行业枚举标准 |
| `namespace`、`scope` | 可选非空字符串；填写真实插件命名空间和路径/环境作用域 |
| `enabled` | 可选 true/false/null；有明确布尔声明时必须填写 `evidence` |
| `evidence`、`note` | 可选非空字符串，记录来源依据或覆盖限制，不放凭据 |

runtime 条目必须有 `platform`、`path`、`evidence`；可选 `enabled`、`listed`、`loaded`（布尔或 null），及 `invocation_name`（实际调用名）。每条状态要有本次会话证据。治理条目必须有 `platform`、`path`，可选 `kind`、`scope`、`evidence`。

以下是填写结构的例子，使用前替换路径和证据，不能将示例断言当成真实运行状态：

```json
{
  "version": 1,
  "sources": [
    {
      "id": "current-plugin",
      "platform": "claude-code",
      "path": "../plugin/skills",
      "kind": "plugin",
      "namespace": "example-plugin",
      "scope": "current project",
      "evidence": "Enabled plugin configuration and visible session list"
    }
  ],
  "runtime": [
    {
      "platform": "claude-code",
      "path": "../plugin/skills/example/SKILL.md",
      "listed": true,
      "invocation_name": "/example-plugin:example",
      "evidence": "Exact path and invocation from this session"
    }
  ],
  "governance": [
    {
      "platform": "claude-code",
      "path": "../target-project/CLAUDE.md",
      "evidence": "Visible project rule"
    }
  ]
}
```

手动状态、配置声明和当前运行证据互相矛盾时，脚本保留双方证据，将相应状态置为 null 并报 `state-evidence-conflict`。调用名断言矛盾时保留 `invocation_evidence`，调用名置为 null 并报 `invocation-evidence-conflict`。不能删掉一方证据来制造确定性。

## 输出与复核

JSON 的 `discovery` 记录每个平台使用的专用或 generic 适配，status 为 builtin-candidates、shared-candidates、explicit-only 或 unconfirmed；这些是脚本报告约定，不是宿主加载状态。没有技能来源时输出具体覆盖缺口，不能据零条结果断言没有冲突。

`sources` 记录 checked、partial、missing、unreadable 和具体 gaps；checked 只表示限定扫描完成。`skills` 保留每个平台/来源/入口的独立 ID、路径、真实路径、SHA-256、名称、描述、正文标题行号、格式 issues、资源引用及状态证据。

状态分别为：

- `present`：该入口被枚举到；不可读和超限文件仍保留。
- `enabled`：有证据的声明；省略、配置未确认或互相矛盾时为 null。不能由文件存在或 listed=true 推导。
- `listed`：本次清单是否暴露该入口；只对精确路径匹配，避免将别名误算为当前调用。
- `loaded`：本次正文是否读取；不是“审计脚本读取过文件”。清单不完整或没有观察时为 null。

`runtime_complete=true` 且某入口不在 runtime 中时，仅将 listed 设为 false，不推导 loaded。不存在的 runtime 文件通过对应 missing 来源暴露覆盖缺口。

`candidates` 包括同平台同名、同内容、同调用名、共享本地引用及跨平台同名差异。候选不包含任何冲突裁决；触发语义、正文矛盾和上级治理关系须按 SKILL.md 对完整清单补充分析。禁用/覆盖入口仍保留，以便区分维护问题与共同生效问题。

脚本安全解析 YAML，拒绝重复 key 和可执行构造器；解析错误不回显原始配置或错误片段。平台扩展字段只记字段名称和必要布尔调用策略，不将额外字段一概判为不兼容。

## 资源与覆盖限制

- 遍历在找到某技能的 SKILL.md 后停止进入其资源子树；支持目录符号链接，按当前遍历祖先检测循环，保留不同别名入口。
- 跳过 `.git`、node_modules、虚拟环境和 Python 缓存。遇到文件/深度上限、断链或不可读子目录，来源标为 partial，不丢掉缺口。
- 资源检查提取 SKILL.md 的 Markdown 内联链接及常见代码路径。URL 不访问，动态变量不展开，代码不执行。脚本不递归阅读参考文件，也不把示例中的缺失路径自动判为必需依赖。
- `outside-skill` 表示相对引用或符号链接指向技能根外；共享资源可能合法，需核对安装单元。这里只解析路径和存在性，不读取根外正文。
- 检查脚本中的相对路径、参考文档的二级引用、Markdown 引用式链接、必需工具、环境变量和共享工作目录，由宿主按候选判断需要静态阅读的材料。
- 大小限制控制单个文件读取，不代表目标机器所有恶意文件的资源隔离保证。针对不可信来源优先在目标授权范围和隔离环境中审计。
- 输出包含路径、名称、描述和状态证据，应留在本地授权范围。脚本不联网、不上传、不读取隐藏提示词，也不导出治理正文或整份配置。

退出码 0 表示清单生成完成，仍可能有覆盖缺口；1 表示 PyYAML 缺失并附安装提示；2 表示参数、manifest 或输出位置需要处理。任何退出码都不表示“没有冲突”。
