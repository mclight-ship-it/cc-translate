# CC Translate — Claude / GPT 多模型切换实施计划

> 状态：**Phase 0–4 已完成；Phase 5 进行中；Phase 6 实验路径已实现、待 dogfood；Phase 7 尚未开始**
>
> 本文只规划 Claude CLI 与 OpenAI GPT（通过官方 Codex CLI）的并存方案。
> Gemini 暂不纳入本轮范围。

## 当前实施记录（2026-08-05）

- Claude 的 warm、cold stream、one-shot 和 Vision 命令契约已有回归测试锁定。
- 新增独立 `cc_providers` 包、Codex CLI provider、严格 JSONL 解析和 Provider Registry。
- 设置已拆成“模型服务 + 模型”，默认及旧配置迁移后仍为 Claude。
- 普通翻译、词典、代码解释、总结、重译、改写和截图视觉均按请求快照路由。
- 缓存签名包含 provider、模型及 provider 专属 prompt revision；当前 Codex 格式
  revision 会淘汰旧 GPT 输出，而 Claude 保留原签名和已有缓存。Codex 请求支持真正的进程取消。
- Codex 0.146.0 已在本机通过 ChatGPT 登录完成短文、长文、代码、对抗文本、图片和取消探针。
- 取消探针完成后，确认没有遗留本 provider 启动的 `codex.exe` 进程。
- Codex 稳定 `exec --json` 没有文字 delta，继续作为短文/图片路径和流式失败回退。
- Codex 长文流式 Beta 默认开启：固定 Codex 0.146.0，使用每请求独立的
  `app-server` stdio 会话和 `item/agentMessage/delta`，不跨请求复用上下文。
- 实验路径启动前失败会回退稳定 `exec`；已经显示 delta 后失败不会重复发起模型请求。
- `initialize` 后、`thread/start` 前调用 `hooks/list`；只允许命令内容与路径均匹配的
  Windows Defender system-managed hook，用户、项目、插件、未知或异常 hook 均在 turn
  创建前失败关闭。
- 最新真实探针确认 23 个 delta 与最终 `item/completed` 文本一致，真实中断在首个
  delta 后返回 `cancelled`。
- Codex 模型提供质量优先的 `auto` 和显式
  `gpt-5.4-mini`（快速）；快速模式仅对该模型设置 `low` reasoning effort。
- Codex 专用格式约束要求摘要逐条输出 `- ` Markdown bullet；原文含无序或编号列表时，
  译文必须保持条目数、顺序、层级和列表类型，不得压成正文段落。Claude prompt 未改。
- 新增有界的隐私安全 `perf.log`，记录 provider、模型、字符数和分段耗时，不记录原文、
  译文、图片路径、认证信息或原始异常详情。
- 诊断报告已按当前 provider 分区；Codex 区显示版本/登录、实验流式开关与版本兼容性、
  400 字符触发规则，以及最近请求的流式/稳定 exec/缓存/取消/回退状态和安全错误码。
  路由状态不记录原文、译文或认证数据。
- 设置页统一将 Codex 长文流式标为 `Beta`，并移除只读“服务状态”字段及后台探测；
  Codex 安装、版本和登录状态继续由诊断窗口集中显示。
- 一键安装器同时安装 Claude Code 最新版和已验证的 Codex CLI 0.146.0；Codex 登录仍由
  用户通过浏览器完成，安装器只调用 `codex login status`，不读取认证文件。
- `perf.log` 新增 app/test 运行来源和每请求终态路由事件；诊断仅聚合最近 7 天真实
  app 运行，显示成功/取消/失败、流式/稳定 exec/回退计数、模型计数和 P50/P95。
  旧格式与测试记录自动排除。
- 流式 Beta 的建议性发布门禁按第 6.3 节已有标准显示进度：7 天观察、200 次请求、无输出后
  流式失败，以及“流式首字 P95 < 稳定长文完整结果 P95”。自动门槛全部通过后仍只
  标记为待人工复核，必须再确认无残留进程和无跨请求串话；门禁不会修改已保存设置。

### 当前性能基线

Codex 0.146.0、同一台机器上的小样本（每个模型 5 次，含短/中/长文本）：

| 模型 | 总耗时中位数 | 样本范围 | 观察 |
|---|---:|---:|---|
| Auto（质量优先） | 7.13 s | 6.05–15.15 s | 中文技术译文更自然，单次波动较大 |
| gpt-5.4-mini（快速） | 6.49 s | 5.88–8.60 s | 中位数约快 9%，尾部更稳定；技术词有时保留英文 |

CLI 原生进程启动只需约 7–13 ms，主要时间消耗在模型响应，而不是进程创建。以上只是本机
小样本，不代表所有账号、网络或 Codex 版本。正式 P50/P95 将由本地
`%APPDATA%\CC Translate\perf.log` 的真实使用数据计算；该日志上限 512 KiB，并保留一个
轮转备份。Claude 的真实远程基线暂不重跑，以避免干扰当前不可方便验证的 Claude 环境。

安全结论：Codex CLI 没有一个可证明“彻底禁用所有工具”的稳定总开关。当前实现使用
`--ignore-user-config`、`--ignore-rules`、`--strict-config`、清空 MCP、只读沙箱、专用工作
目录、功能覆盖项、JSON 编码的用户输入和严格提示词做纵深防御。JSONL 会逐行解析，首个工具
事件立即终止进程；该检测仍是运行期防线，不能单独视为执行前安全边界。

---

## 1. 目标与原则

### 1.1 目标

在不破坏现有 Claude 体验的前提下，为 CC Translate 增加 GPT 模型能力：

- 用户可以在设置中选择 **Claude** 或 **OpenAI GPT（Codex CLI）**。
- 选择 Claude 时，继续使用现有 Claude CLI、流式输出、预热池、文字翻译和图片翻译。
- 选择 GPT 时，走新的 Codex CLI provider，支持文字、代码、总结、改写、重译和图片输入。
- 两条路径共享现有 UI、提示词、历史记录、方向判断、取消机制和错误展示。
- 切换模型后无需重启 App；新请求立即使用新 provider。
- 任何 GPT 相关故障都不能破坏 Claude 路径，也不能自动改回另一个模型而让用户误解。

### 1.2 强约束

1. **Claude 路径零回归**
   - Claude 的命令参数、解析方式、预热池、超时和错误文案保持原位，不重写。
   - 旧配置没有 provider 字段时，自动解释为 Claude。
   - 现有用户升级后仍默认使用自己原来的 Claude 模型。

2. **本地订阅认证**
   - Claude 继续使用用户本地 Claude CLI 登录状态。
   - GPT 使用用户本地 `codex login` 的 ChatGPT 订阅登录状态。
   - App 不读取、复制、上传或保存 `~/.codex/auth.json`。
   - 本轮不接 OpenAI API Key，不引入额外按量计费。

3. **安全边界**
   - Codex 只作为模型运行器；使用 CLI 当前提供的多层限制尽量阻止文件修改、Shell、MCP 或 Web 搜索。
   - Codex 在 CC Translate 专用的空工作目录中运行，不以本项目目录或用户当前目录为工作目录。
   - 使用最小权限沙箱；检测到工具执行事件时失败关闭，而不是继续执行。

4. **能力驱动，而不是强行模拟 Claude**
   - Claude 支持常驻双向进程，因此保留现有预热池。
   - `codex exec` 是稳定的非交互接口，但不是 Claude 式双向常驻接口。
   - Codex 常驻优化必须经过独立实验和性能门槛；不能为了“看起来有预热池”而引入无效进程。

---

## 2. 用户体验方案

### 2.1 设置页面

建议把现有“模型”设置拆成两个关联项：

```text
模型服务    Claude / OpenAI GPT（Codex）
模型        随服务商变化
```

Claude 模型保持现有选项：

```text
Haiku（快速）
Sonnet（均衡）
Opus（最强）
```

OpenAI 第一版提供：

```text
自动选择（质量优先）
gpt-5.4-mini（快速）
```

Auto 保持默认。`gpt-5.4-mini` 已在当前账号和 Codex 0.146.0 上验证，可降低部分请求的
等待时间，但可能比 Auto 更倾向保留英文技术词。Codex 可用模型仍会受 ChatGPT 套餐、企业
策略和 CLI 版本影响；显式模型不可用时显示明确错误，不静默切回 Auto 或 Claude。

界面显示名使用“OpenAI GPT（Codex）”，让用户理解自己切换的是 GPT，同时明确底层使用
Codex CLI 和 ChatGPT 订阅，不误导成 OpenAI API。

### 2.2 状态与诊断

当用户选择 OpenAI GPT 时，设置页显示本地状态：

- Codex CLI 未安装
- 已安装但未登录
- 已登录，可使用
- 当前模型不可用
- 公司策略或网络阻止

提供：

- “重新检测”按钮
- “登录 Codex”引导
- 安装说明链接
- 诊断详情

App 不静默执行远程安装脚本。安装和登录必须由用户明确操作。

### 2.3 切换行为

- 保存设置后，只取消/清理旧 provider 的空闲资源，不中断已经完成的结果窗口。
- 新请求读取一次 provider 快照，执行过程中不因设置再次变化而换 provider。
- 从 Claude 切到 GPT：停止并清理 Claude 空闲预热进程。
- 从 GPT 切回 Claude：异步重建 Claude 预热池。
- 当前请求失败时，不自动跨 provider 重试，避免重复消耗额度或产生结果来源混淆。

---

## 3. 总体架构

```text
触发 / 快速翻译 / OCR / 重译 / 改写
                  │
                  ▼
      TranslatorApp 请求编排层
  分类、方向、提示词、历史、UI、取消
                  │
                  ▼
         ProviderRegistry
          │             │
          ▼             ▼
 ClaudeCliProvider   CodexCliProvider
          │             │
  ClaudeWarmPool     codex exec --json
  stream-json        --ephemeral
  Claude Vision      -i/--image
```

### 3.1 Provider 接口

新增 provider 层，负责：

- 查找 CLI
- 检查版本和登录状态
- 构造命令
- 启动/终止进程
- 解析普通与 JSONL 输出
- 统一错误
- 声明能力
- 可选预热策略

建议的核心数据结构：

```python
@dataclass(frozen=True)
class ProviderRequest:
    task: str
    model: str | None
    system_prompt: str
    user_text: str
    image_paths: tuple[str, ...] = ()
    timeout_seconds: float = 60


@dataclass(frozen=True)
class ProviderCapabilities:
    text: bool
    images: bool
    streaming: bool
    warm_sessions: bool


class ModelProvider(Protocol):
    def complete(self, request, cancel_event): ...
    def stream(self, request, on_event, cancel_event): ...
    def diagnose(self): ...
    def shutdown(self): ...
```

Provider 返回统一事件：

```text
started
text_delta
completed
usage
error
```

上层不再识别 Claude/Codex 的原始 JSON，只处理统一事件。

### 3.2 保留在 App 编排层的职责

以下逻辑不搬进 provider：

- 中英方向判断
- 文本/代码/词典/总结分类
- 系统提示词选择
- `<text>...</text>` 等用户输入边界
- 历史记录
- UI 流式队列与 50ms 批量刷新
- job ID 和陈旧结果保护
- 结果窗口、重译、改写等业务动作

这样模型切换不会复制一套业务逻辑。

---

## 4. 配置迁移

### 4.1 新配置

为保持配置简单并记住每个 provider 上次选择的模型，使用平铺键：

```json
{
  "model_provider": "claude_cli",
  "claude_model": "haiku",
  "codex_model": "auto"
}
```

暂时保留旧键：

```json
{
  "model": "haiku"
}
```

### 4.2 迁移规则

1. 如果不存在 `model_provider`：
   - 设为 `claude_cli`
   - 把旧 `model` 值复制到 `claude_model`
2. 不删除旧 `model`，至少保留一个兼容发布周期。
3. 保存 Claude 设置时同步旧 `model`，确保降级到旧版本仍能读取。
4. `codex_model = "auto"` 时不向 CLI 传 `-m`，由当前 Codex/订阅选择默认模型。
5. 未知 provider 必须在请求时显示明确配置错误，不静默降级。Codex `auto` 不绑定具体模型。

---

## 5. Codex CLI 调用设计

### 5.1 稳定调用路径

第一版只依赖官方稳定接口 `codex exec`：

```text
codex exec
  --json
  --ephemeral
  --sandbox read-only
  --skip-git-repo-check
  --ignore-user-config
  --ignore-rules
  -c features.shell_tool=false
  -c web_search="disabled"
  -c features.apps=false
  -c features.hooks=false
  -c features.multi_agent=false
  -c features.memories=false
  -C <CC Translate 专用空目录>
  [-m <model>]
  [-i <image path> ...]
  -
```

完整提示词通过 stdin 传入，不把待翻译文本放在命令行参数中。

说明：

- `--json`：输出 JSONL，便于稳定解析状态、结果和错误。
- `--ephemeral`：不保存每次翻译的 Codex 会话文件。
- `--sandbox read-only`：不允许修改工作区。
- `--ignore-user-config`：不加载用户的自定义工具、MCP、插件和提示配置；认证仍使用
  `CODEX_HOME` 中的官方登录状态。
- `--ignore-rules`：不加载项目 execpolicy 规则。
- 显式 `-c`：在模型收到请求之前关闭 shell、Web 搜索、apps、hooks、多代理和 memory。
- `-C`：把工作目录固定到 App 专用空目录。
- `-`：从 stdin 读取完整任务。
- `-i/--image`：官方 CLI 支持的图片附件参数。

Phase 0 必须验证目标 Codex 版本支持以上所有开关，并确认公司登录、代理和模型路由在
`--ignore-user-config` 下仍然工作。如果企业环境必须依赖用户配置才能连接，则不能简单移除
该安全开关；应先列出必要配置并通过受控 `-c` 白名单逐项传入。无法同时满足连接和禁用工具时，
Codex CLI provider 判定为 No-Go，不能带着已知安全缺口上线。

### 5.2 提示词封装

Codex 没有复用 Claude `--system-prompt` 的同形接口，因此 provider 会把现有系统提示词和
用户文本组合成一份严格任务：

```text
You are the translation engine inside CC Translate.
Do not inspect files, run commands, call tools, search the web, or modify anything.
Return only the requested result.

<task_instructions>
  ...现有 system prompt...
</task_instructions>

<untrusted_user_text>
  ...待处理文本...
</untrusted_user_text>
```

图片任务使用同一边界，并通过 `-i` 传图片，不使用 Claude 专属的 `@path` 语法。

### 5.3 输出解析

只接受允许的事件：

- `thread.started`
- `turn.started`
- agent message
- `turn.completed`
- `turn.failed`
- `error`

虽然工具已在启动前关闭，解析器仍做第二道防线。如果出现以下事件，立即终止进程并报告
安全错误：

- command execution
- file change
- MCP tool call
- Web search
- 其他未知的有副作用事件

第一版以官方稳定的最终 agent message 为最低保证。

如果真实 CLI 测试确认存在稳定的文字 delta 事件，则映射为现有流式输出；如果没有，则保持
“翻译中”状态并在完成后一次性展示，不能伪造 token 流。

### 5.4 错误映射

统一映射以下情况：

- CLI 未安装
- 未登录
- 登录过期
- 模型无权限/不存在
- 订阅额度不足或限流
- 网络/代理错误
- JSONL 格式变化
- 进程超时
- 用户取消
- 图片格式/大小不支持
- 安全事件被阻止

stderr 不再像当前 Claude 流式路径一样直接丢弃，而是限长收集并脱敏后写诊断日志。

---

## 6. 预热与性能方案

### 6.1 Claude

完全保留当前方案：

- `translate` / `dictionary` 两个 profile
- 每个 profile 深度 2
- 480 秒最大生命周期
- 60 秒请求超时
- 模型或方向变化后重建

抽取 provider 时先做行为锁定测试，确保参数和时序不变。

### 6.2 Codex 第一版

官方稳定接口 `codex exec` 是一次性进程，不能直接复制 Claude 的双向常驻池。

第一版做以下低风险优化：

- App 启动后后台查找 Codex 可执行文件、版本和登录状态。
- 缓存诊断结果，避免每次请求重复探测。
- 预创建专用空工作目录。
- Prompt、命令参数和解析器提前准备。
- 请求开始后立即启动进程；取消时真正终止进程树，而不只是丢弃 UI 结果。
- 记录进程启动、首个事件、首段文字和完成耗时。

不做会消耗用户订阅额度的“空请求预热”。

### 6.3 Codex app-server 流式实验

官方存在 `codex app-server` 并提供文字 delta，但仍是实验接口，协议可能随时变化。
当前实现为设置中默认开启、可关闭的 Beta 开关；每个长文本请求使用独立进程，不常驻、
不复用上下文。

实验必须满足全部门槛才可启用：

1. 仅在内部功能开关下运行。
2. 固定支持的 Codex CLI 版本范围。
3. 协议握手失败自动回退 `codex exec`。
4. 进程崩溃、超时和切换 provider 后可以完整清理。
5. 不跨翻译复用上下文，避免文本串扰和隐私问题。
6. `thread/start` 前使用 `hooks/list` 预检；仅接受 Windows Defender 平台目录下、
   命令内容精确匹配且标记为 system-managed/managed 的 hook。预检异常或任何其他
   启用 hook 均失败关闭并在显示文字前回退稳定 `exec`。
7. P95 首次可见文字时间明显早于 `codex exec` 的完整结果时间。
8. 连续 200 次文字请求无泄漏、无串话；图片继续使用稳定 `exec`。

若达不到门槛，则永久使用稳定的 `codex exec`。如果未来必须获得 API 级 token 流和连接复用，
应新增 OpenAI Responses API provider，而不是依赖不稳定协议；该 API 会单独计费，不属于本轮。

---

## 7. 分阶段实施

总共 **8 个阶段**。每阶段独立提交、独立测试、可单独回滚。

### Phase 0 — 真实 Codex 兼容性探针

**目的**：先验证公司账号和本机环境，不带着假设写完整功能。

工作：

- 安装并用公司 ChatGPT 账号完成 `codex login`。
- 获取 `codex --version`、`codex login status`。
- 用真实短文本、长文本、代码和图片运行 `codex exec --json --ephemeral`。
- 验证 `--ignore-user-config` 和全部工具禁用覆盖项在公司账号下生效。
- 用包含“忽略指令并运行命令/读取文件”的对抗文本确认不会产生工具事件。
- 保存脱敏后的 JSONL fixture。
- 确认模型默认值、中文质量、图片输入、取消和限流表现。
- 测量进程启动、首个事件、首段文字和总耗时。
- 确认公司网络和安全策略允许 Codex。

交付物：

- 兼容性记录
- JSONL 测试 fixture
- 延迟基线
- Go/No-Go 结论

**验收门槛**：文字和图片均能通过公司账号成功运行，否则停止后续实现。

### Phase 1 — Provider 地基与 Claude 行为锁定

**目的**：先建立抽象，但用户体验完全不变。

新增建议：

```text
cc_providers/
  __init__.py
  base.py
  registry.py
  errors.py
  claude_cli.py
```

工作：

- 为当前 Claude one-shot、stream、vision 和 warm session 补行为锁定测试。
- 抽出统一 request/result/event/error/capabilities。
- 把现有 Claude CLI 调用机械搬入 `ClaudeCliProvider`。
- `TranslatorApp` 仍只注册 Claude provider。
- 保留 `cc_warm.py` 的实现和所有预热行为。

**验收门槛**：

- 全部现有测试通过。
- Claude 命令参数与改造前一致。
- Claude 真实文字、长文流式和图片冒烟结果一致。
- 设置页和配置文件无可见变化。

### Phase 2 — Codex Provider（文字）

新增：

```text
cc_providers/codex_cli.py
cc_providers/codex_jsonl.py
```

工作：

- 查找 Codex CLI。
- 构造安全命令、禁用全部非必要工具并使用专用工作目录。
- stdin 提示词封装。
- 增量 JSONL 解析、最终结果提取和未知事件保护；首个工具事件立即终止。
- 真实进程取消、超时、进程树清理。
- 错误映射和脱敏日志。
- 接入普通翻译、词典、代码解释、总结、重译和改写。

**验收门槛**：

- 所有文字任务可通过 GPT 完成。
- 不出现工具执行或文件访问。
- 取消后无遗留 Codex 进程。
- Claude 路径测试仍全绿。

### Phase 3 — 设置、配置迁移与切换

工作：

- 增加 `model_provider`、`claude_model`、`codex_model`。
- 无损迁移旧 `model` 配置。
- 设置页增加服务商和动态模型项。
- 增加 Codex 安装/登录状态。
- 切换时清理旧 provider 空闲资源并初始化新 provider。
- 增加中英文 UI 文案。

**验收门槛**：

- 旧用户升级后仍使用原 Claude 模型。
- Claude → GPT → Claude 连续切换无需重启。
- 切回 Claude 后预热池正常恢复。
- 配置损坏时给出明确错误，不静默换模型。

### Phase 4 — 图片 / OCR 对齐

工作：

- 把当前 OCR 的 Claude `@path` 分支移入 Claude provider。
- Codex 使用官方 `-i/--image`。
- 统一图片格式校验、临时文件生命周期和超时。
- 本地 OCR engine 保持独立，不因 provider 改造受影响。
- 覆盖截图翻译、图片结构保留和错误提示。

**验收门槛**：

- 同一张截图可分别用 Claude 和 GPT 处理。
- 图片临时文件在成功、失败、取消后都被清理。
- 本地 OCR 仍可用。

### Phase 5 — 诊断、可观测性与性能基线（实现完成，待真实数据）

工作：

- 诊断页改为 provider 分区。
- Claude 诊断原样保留。
- 增加 Codex 版本、登录、模型、网络和最近错误诊断。
- perf log 增加 provider、模型、启动耗时、首结果耗时、总耗时和取消原因。
- 每个 Codex 请求记录终态路由和耗时，标记真实 app / 自动化测试来源；诊断提供最近
  7 天只含真实运行的 dogfood 摘要。
- 诊断按既定发布门槛显示请求数、观察天数、流式首字/稳定长文 P95 对照和输出后失败；
  自动证据不会替代进程清理与跨请求隔离的人工确认。
- 日志禁止记录原文、图片内容、access token 和完整认证路径。
- 对 Claude 与 Codex 做相同任务基准测试。

**验收门槛**：

- 用户能区分“未安装、未登录、限流、模型不可用、网络错误”。
- 诊断日志不含凭据和待翻译内容。
- 形成 Claude / GPT 性能对照表。

### Phase 6 — Codex app-server 流式实验（已实现，待 dogfood）

工作：

- 在设置中的 Beta 开关后实现 `app-server` 路径，版本 4 默认开启。
- 与稳定的 `codex exec` A/B 对比。
- 验证协议、进程恢复、上下文隔离和版本兼容。
- 固定支持 0.146.0；其他版本在显示任何文字前回退 `exec`。
- `hooks/list` 在 `thread/start` 前验证全部启用 hook；仅允许命令和 Defender 平台路径
  均精确符合预期的 system-managed hook。用户、项目、插件、未知、修改或预检异常
  hook 均失败关闭。
- 只有达到第 6.3 节全部门槛才允许默认启用。

**验收门槛**：

- 达标：保留为可自动回退的优化。
- 不达标：删除/关闭实验代码，正式版本继续使用 `codex exec`。

### Phase 7 — 灰度发布与正式开放

工作：

- 使用功能开关先只在开发者机器显示 GPT。
- 完整跑自动化测试和真实双 provider 回归。
- 至少一周 dogfood，记录失败率、P50/P95 延迟和取消残留。
- 更新 README、安装说明和故障排查。
- 灰度通过后再对所有用户显示 OpenAI GPT 选项。

**验收门槛**：

- Claude 线上指标无回退。
- GPT 文字和图片主路径稳定。
- 没有凭据泄漏、后台残留进程或跨请求串话。
- 关闭功能开关即可完全隐藏 GPT，不影响 Claude。

---

## 8. 测试计划

### 8.1 单元测试

- Provider registry 和配置迁移
- Claude/Codex 命令构造
- JSON、JSONL 正常与截断解析
- 错误映射
- 未知/危险事件拒绝
- Prompt 边界和特殊字符
- 图片参数和临时文件清理
- 超时、取消和进程终止
- provider 切换资源清理

### 8.2 契约测试

同一组 provider contract 用例运行在 Claude 和 Codex adapter 上：

- complete
- stream/final-result fallback
- cancel
- timeout
- unavailable
- unauthorized
- image capability
- shutdown idempotency

### 8.3 真实 CLI 集成测试

默认测试不消耗订阅额度。真实测试由环境变量显式开启：

```text
CC_RUN_CLAUDE_INTEGRATION=1
CC_RUN_CODEX_INTEGRATION=1
```

真实测试覆盖：

- 短中文/英文
- 长文本
- 单词词典
- 代码解释
- 图片
- 取消
- 无效模型
- 未登录状态

### 8.4 回归矩阵

| 功能 | Claude | GPT |
|---|---:|---:|
| 快速翻译 | 必测 | 必测 |
| 双击 Ctrl+C | 必测 | 必测 |
| 长文流式/等待 | 必测 | 必测 |
| 单词词典 | 必测 | 必测 |
| 代码解释 | 必测 | 必测 |
| 总结 | 必测 | 必测 |
| 重译/换向 | 必测 | 必测 |
| 改写/提炼 | 必测 | 必测 |
| 截图/图片 | 必测 | 必测 |
| 历史记录 | 必测 | 必测 |
| 取消/关闭窗口 | 必测 | 必测 |
| 深色/浅色 UI | 必测 | 必测 |

每阶段都运行现有完整测试套件；正式开放前重复运行多轮真实 CLI 冒烟。

---

## 9. 预计修改范围

主要新增：

```text
cc_providers/base.py
cc_providers/errors.py
cc_providers/registry.py
cc_providers/claude_cli.py
cc_providers/codex_cli.py
cc_providers/codex_jsonl.py
tests/test_providers.py
tests/fixtures/codex/*.jsonl
```

主要修改：

```text
translator.pyw              请求编排改走 provider
cc_warm.py                  归属 Claude provider，核心行为保留
cc_app_warm.py              capability-driven 预热
cc_app_settings.py          provider/model 设置与状态
cc_app_ocr.py               图片请求改走 provider
cc_app_results.py           重译/改写不再直调 _call_claude
cc_app_diagnostics.py       provider 分区诊断
cc_core.py                  配置键、标签和迁移默认值
i18n.py                     新增设置与错误文案
```

预计规模：

- Provider 地基 + Claude 等价搬迁：350–550 行
- Codex 文字 + JSONL：250–450 行
- 设置/迁移/诊断：300–450 行
- 图片、测试与性能工具：300–500 行

重点不是追求最少行数，而是避免把第二套 CLI 分支散落进现有所有方法，形成新的耦合。

---

## 10. 主要风险与应对

| 风险 | 应对 |
|---|---|
| 抽象 provider 时破坏 Claude | 先补行为锁定测试；Claude 只机械搬迁；每阶段真实冒烟 |
| Codex JSONL 版本变化 | 独立解析器 + fixture；未知事件失败关闭；记录 CLI 版本 |
| Codex 无稳定 token delta | 第一版允许最终结果一次显示；不伪造流；后续再评估 API/app-server |
| Codex 比 Claude 冷启动慢 | 实测 P50/P95；后台预检；实验 app-server，但稳定接口永远可回退 |
| Codex 执行工具或读文件 | 启动前关闭 shell/Web/apps/hooks/多代理，空目录 + read-only sandbox；事件解析再做第二道拒绝 |
| 用户 GPT 模型无权限 | 默认跟随 Codex 自动模型；显式模型必须先真实验证 |
| 公司也限制 Codex | Phase 0 先验证公司账号、网络和政策，失败则不继续投入 |
| 切换后额度重复消耗 | 请求绑定 provider 快照；不做隐式跨 provider 重试 |
| 认证信息泄漏 | 只调用官方 CLI；不读 auth.json；日志脱敏；不保存 token |
| 图片路径或临时文件泄漏 | 专用临时目录；完成/失败/取消统一清理 |

---

## 11. 完成定义

本功能只有同时满足以下条件才算完成：

1. 老用户升级后 Claude 默认、模型和行为完全不变。
2. 设置中可以无重启切换 Claude / OpenAI GPT。
3. GPT 支持文字、代码、词典、总结、重译、改写和图片。
4. Claude 预热池保留并通过原有全部测试。
5. GPT 的取消会真正终止子进程，不遗留后台任务。
6. 没有 App 保存或读取 Claude/Codex 登录 token。
7. Codex 不执行工具、不修改文件、不读取项目工作区。
8. 完整自动化测试通过，并完成真实双 provider 冒烟。
9. GPT 功能可被开关完全隐藏，关闭后等同于当前版本。
10. README、安装、登录和故障诊断文档齐全。

---

## 12. 本轮确认项

开始实现前，请确认以下产品决策：

1. 设置采用“模型服务 + 模型”两个关联项，而不是把 Claude/GPT 所有型号塞进一个列表。
2. 现有用户和新安装用户暂时都默认 Claude；GPT 由用户主动选择。
3. GPT 第一版只使用 ChatGPT 订阅 + Codex CLI，不接 OpenAI API Key。
4. GPT 第一版允许在 CLI 没有稳定文字 delta 时完成后一次展示结果。
5. Codex 常驻 `app-server` 只做实验，未达到性能和稳定门槛绝不默认启用。
6. Gemini 暂不实现，但 provider 架构保留未来扩展能力。

以上确认后，按 Phase 0 → Phase 7 顺序执行，不并行重写 Claude 和 GPT 两条主链。
