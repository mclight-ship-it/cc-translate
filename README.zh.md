# CC Translate

[English](README.md) | [简体中文](README.zh.md)

> ⚠️ **使用前必看（必需）**：CC Translate 至少需要一个可用的模型 CLI：官方 Codex CLI（ChatGPT 登录、API key 或兼容的自定义 provider），或 Claude Code（订阅或兼容本地代理）。默认使用 OpenAI GPT 智能路由。

这是一个主打**高质量翻译**的划词翻译 App：**双击 Ctrl+C** 翻译当前选中的文字，弹窗显示译文。它结合离线本地词典，以及 Claude Code 与 OpenAI GPT（通过官方 Codex CLI）两套平行 provider。使用 ChatGPT 或 Claude 订阅登录时无需另配 API key，也支持 API key 和兼容的自定义 provider。

## 界面预览

<p align="center">
  <img src="docs/screenshots/popup-translate.png" alt="划词翻译弹窗" width="520"><br>
  <sub><b>双击 Ctrl+C</b> —— 选中文字，鼠标旁立刻弹出译文</sub>
</p>

<table>
<tr>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/popup-code.png" alt="代码解释模式" width="360"><br>
  <sub><b>代码解释模式</b>：选中代码不硬翻，用中文讲清它做什么</sub>
</td>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/popup-summary.png" alt="长文摘要" width="420"><br>
  <sub><b>长文摘要（Beta）</b>：长文本先给要点摘要，再展示完整译文</sub>
</td>
</tr>
<tr>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/popup-dict.png" alt="本地词典即时结果与 AI 补充" width="420"><br>
  <sub><b>词典模式</b>：有来源的本地释义即时显示，并可在后台补充 AI 信息</sub>
</td>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/quick-input.png" alt="快速输入翻译" width="420"><br>
  <sub><b>快速输入翻译</b>：没选中文字时双击 Ctrl+C，弹出输入框手动输入</sub>
</td>
</tr>
<tr>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/screenshot-ocr.png" alt="截图翻译框选" width="420"><br>
  <sub><b>截图翻译</b>：按 <code>Win+Shift+C</code> 框选屏幕任意区域，直接翻译图中文字（支持视觉模型或离线本地 OCR）</sub>
</td>
<td width="50%" valign="top" align="center">
  <img src="docs/screenshots/history.png" alt="翻译历史" width="420"><br>
  <sub><b>翻译历史</b>：托盘打开，左侧列表、右侧原文与结果</sub>
</td>
</tr>
</table>

## 功能

- **双击 Ctrl+C** 翻译剪贴板/选中文字，鼠标旁弹窗显示
- **Claude / OpenAI GPT 切换**：可在设置里选择模型服务；Claude 保留原有预热池和流式路径，GPT 跟随本机 Codex CLI 的配置与认证
- **截图翻译**：按 `Win+Shift+C` 框选屏幕任意区域，直接翻译图中文字；支持视觉模型或离线本地 OCR
- **快速输入翻译**：没有选中文字时双击 Ctrl+C，弹出输入框，手动输入要翻译的内容
- **代码解释模式**：调用模型前先在本地识别代码、图文混排与普通文字，覆盖 Python、JSON、YAML 和常见配置结构，不增加额外 AI 请求。代码不会被硬翻，而是用中文解释用途；文字与代码混排时保留代码原样，普通表单、日期、路径或包含 `foo()` 的句子仍按文字处理。
- **长文摘要（Beta）**：默认开启；翻译较长的自然语言文本时，先给出一段要点摘要，再展示完整译文，可在「实验室」中关闭
- **本地加速词典模式**：可在设置中按需一键下载。安装并启用后，中英文短词优先查询当前用户目录中的只读 SQLite。高置信精确匹配、来源明确提供或构建时审核过的英文词形、简繁别名及 Unihan 单字会立即显示，并在词头旁以紧凑的方形闪电徽标标识极速结果；未启用、未安装、弱命中或数据库故障会无缝走原有 AI 词典路径，运行时不做有风险的词干猜测。来源中的数字声调拼音会显示为标准声调符号；中文多音字按读音分组，明确标为冷门或过长的释义后置，长词条首屏显示五条并可原位**展开更多**。本地结果出现后，后台 AI 只补充缺失信息且不延迟首屏；缓存命中直接出现，查询中提示更轻，失败时安静保留完整本地结果。补充结果使用独立、限长的本地缓存，不产生可见历史记录。**重新用 AI 查询**仍保持原有的完整替换查询；每条本地结果都可查看来源与许可。本地字段和署名始终以来源为准，不虚构例句、音标或词性。
- **粘贴为纯文本（Beta）**：可选择用 `Ctrl+Shift+K` 去掉剪贴板格式并立即粘贴；只有图片或文件的剪贴板不会被改动
- **富文本排版**：结果弹窗支持轻量 Markdown，并像代码编辑器一样对代码分色显示；复制出的仍是纯文本
- **多目标语言**：自动检测中↔英，或固定译成中/英/日/韩/法/德/西
- **弹窗内换向重译**：弹窗提供「重译」菜单，一键把选中内容重译成其他语言
- **改写与提炼**：弹窗内可把译文改写为口语 / 正式 / 专业风格，或提炼要点
- **长文流式**：Claude 会逐步显示长文结果；Codex app-server 流式输出始终开启，保留原文列表并将摘要要点输出为 Markdown bullet，启动模型 turn 前会预检可执行 hook，输出前失败会安全回退到稳定的 `codex exec`
- **按 Provider 分区的诊断**：诊断窗口会显示 Codex 版本/登录、流式兼容性和触发条件，以及最近请求路由和最近 7 天真实运行的成功/取消/失败、模型、路由及 P50/P95 摘要；建议性发布门禁会显示 7 天 / 200 次请求进度和流式首字对稳定长文完成的 P95 对照，但不会修改已保存设置
- **智能选区识别**：自动判断是否真的选中了文字，避免在输入框里没选中时误翻整框内容（含 VS Code 等跨进程应用）
- **翻译历史**：托盘打开历史窗口，可搜索、按类型筛选
- **弹窗布局**：经典（屏幕居中）或动态（跟随鼠标），可在设置中切换
- **主题**：跟随系统 / 浅色 / 深色
- **系统托盘**：左键点击可自定义（默认设置，也可选历史 / 截图翻译 / 快速翻译），右键快速翻译 / 截图翻译 / 历史 / 检查更新 / 暂停 / 退出
- **自动更新**：app 本身即 `git clone` 部署，可从 GitHub 检查并更新，支持手动「检查更新」与夜间自动更新
- 可设开机自启

## 离线词典数据与许可证

应用启动时绝不联网下载词典。用户可通过**设置 → 离线词典加速（推荐） →
下载并启用**，明确下载固定版本、约 65 MB 的 Release 制品。应用会校验精确
大小、SHA-256、SQLite schema 和数据版本，再原子安装到
`%APPDATA%\CC Translate\dictionary\cc_dictionary.sqlite3`。设置中既可关闭快速
路径而保留数据，也可确认后**删除本地数据**；两种操作都不影响 AI 词典模式。
诊断窗口会显示数据库版本和健康状态。
诊断窗口还会显示仅限本次运行的聚合命中率、查询 P50/P95 和 AI 回退原因；
这些指标绝不包含查询文字。

可选词典制品来自固定输入：WikDict eng-zho 2025.11.21（CC BY-SA 3.0）、
自身文件头明确采用 CC BY-SA 3.0 的 CC-CEDICT 2017-04-28 immutable
快照、通过 OMW 2.0 提供的 Chinese Open Wordnet / Princeton WordNet
（分别保留其 WordNet 许可证），以及 Unihan 17.0.0（Unicode License v3）。
应用代码许可与词典数据许可相互分离。完整署名、上游 URL、SHA-256、修改与
索引说明、制品 hash 和原始许可证文本可从**关于 → 数据许可**、每条本地结果
中收纳的圆角**来源与许可**卡片、[THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES) 与
[`data/dictionary/licenses/`](data/dictionary/licenses/)。

开发者可用仅依赖标准库的构建器复现数据库：

```powershell
python tools\build_dictionary.py --cache <source-cache> --download
```

显式 `--download` 仅供开发构建使用；用户明确执行一次下载后，正常应用查询始终
离线。构建时会校验所有输入 hash，并为每个 entry 与 sense 分别保留 source ID
和 provenance。可复现的发行暂存制品为
`data/dictionary/cc_dictionary.sqlite3`；发布引用它的应用代码前，必须按
`cc_dictionary_artifact.py` 声明的固定 Release tag 与 asset 名称上传该制品。

## 运行环境

- Windows（用到 Windows API 做 DPI 感知、多屏定位、注册表读主题）
- Python 3.12+
- Node.js（用于安装 Claude Code 与 Codex CLI）
- 至少一个 provider：
  - Claude Code：已登录 Claude 订阅（Pro/Max），或兼容的本地代理端点（例如 Agent Maestro）
  - OpenAI GPT：官方 Codex CLI，已配置 ChatGPT 登录、API key 或可用的兼容自定义 provider
- ⚠️ **务必先把 Claude Code CLI 升级到最新版本**——旧版 CLI 的参数不兼容会导致翻译报错或结果异常，这是最常见的安装踩坑，装之前一定要更新到最新

## 快速安装（推荐）

在 **PowerShell** 里跑这一行，脚本会自动装好 git / Python / Node、拉取代码、安装 Claude CLI、兼容的 Codex CLI 与 Python 依赖，并启动程序：

```powershell
irm https://raw.githubusercontent.com/mclight-ship-it/cc-translate/master/install.ps1 | iex
```

它会自动完成安装，**不会代办账号授权**。OpenAI GPT 是默认模型服务。
使用 ChatGPT 订阅时，请通过以下命令完成官方 Codex CLI 的浏览器登录；
已经使用 API key 或自定义 provider 的用户，可以继续使用现有可用的 Codex 配置：

```powershell
codex login
codex login status
```

**原生 Codex 配置：** exec、流式、预热和诊断统一使用原生 Codex 配置与认证目录
（`CODEX_HOME`，默认 `~/.codex`）。请在 Codex 中配置 ChatGPT 登录、API key
或兼容的自定义 provider；CC Translate 不实现认证，也不复制凭据。
Claude 仍可在**设置**中作为备用模型服务选择。

以下兼容性覆盖变量可选，仅为 CC Translate 指定独立 Codex 目录；
使用自定义 provider **不再需要**设置它：

```powershell
setx CC_TRANSLATE_CODEX_HOME "$env:APPDATA\CC Translate\codex-provider"
```

修改环境变量或路由配置后请重启。显式覆盖目录必须包含 `config.toml`；
无效配置会明确失败，不会悄悄切换目录或账号。移除此变量后恢复原生 `CODEX_HOME`。

仅翻译限制**只作用于子进程**：不继承个人指令、技能提示、记忆、通知命令、
插件或用户 hook。通过 Codex 原生配置读取接口发现合并后的 MCP 条目，并逐个明确
禁用（仅设置空 MCP 表不能清空它们）。继续保留临时会话、只读沙箱、hook 预检和
工具事件失败即停止的保护；绝不改写全局 Codex 文件，也不绕过强制管理策略。
诊断显示实际选择的后端；自定义认证显示为**尚未验证**，不会误报缓存的 ChatGPT
登录，也不代表端点或模型可用。诊断不会执行自定义凭据命令或提交模型请求。

**自动管理本地模型目录：** 对于普通自定义 Provider 配置
（包括全局配置中仅含 `trust_level` 的项目记录），CC Translate
从每位用户自己安装的 Codex 导出实际生效的模型信息，存到
`%APPDATA%\CC Translate\codex-catalogs`。不分发统一模型列表、不复制凭据，
也不切换 Provider、模型或推理强度。这可以避免自定义端点上反复失败的
`/models` 查询。目录描述模型能力，不代表账号权限或模型一定可用。

启动 `exec` 或流式进程（包括预热）之前会检查目录。快照按 CLI 程序、
Codex 配置目录、配置内容和原生模型缓存区分；超过 24 小时的快照在下次
启动子进程时刷新，不在正在进行的翻译中途刷新。新快照由 Codex 自身验证，
App 新实例首次使用已有快照时也会重新验证，写入采用原子替换。
目录丢失、损坏或过期会重建；导出或验证失败时不添加托管覆盖设置，恢复
Codex 原生目录加载，并在 `%APPDATA%\CC Translate\error.log` 记录仅含诊断
信息的 `codex_catalog` 警告。不会重发已经提交的翻译。
首次生成可能增加启动时间，通常由预热完成。

目前验证支持 Codex **0.146.0**。未验证的 CLI 版本保留原生加载，不沿用旧快照。
官方 OpenAI 配置、分层 provider/model/catalog 设置、用户自行设置的
`model_catalog_json` 均保留原生行为；App 不修复或覆盖用户自己维护的目录。
请求的模型不在导出目录中时，也保留原生加载，不会悄悄替换成其他模型。
设置用户环境变量 `CC_TRANSLATE_CODEX_CATALOG=off` 并重启可关闭自动管理。
模型下线或账号无权限仍可能独立导致失败。

GPT 默认使用**智能路由（极速）**并增量显示文字；如果更看重翻译质量，可切换到
**自动选择（优质）**。具体模型是否可用取决于 ChatGPT 套餐、组织策略和 Codex CLI
版本。

> 可选环境变量（运行前设置）：`$env:CC_TRANSLATE_DIR` 指定安装目录（默认 `%USERPROFILE%\cc-translate`）；`$env:CC_TRANSLATE_DRYRUN="1"` 先“空跑”一遍，只显示每步会做什么、不做任何改动。

> 如果手动运行 `claude` 时报 **“running scripts is disabled on this system”**，是 PowerShell 默认执行策略（`Restricted`）挡住了 npm 的 `.ps1` 快捷方式。安装脚本会自动把当前用户策略设为 `RemoteSigned` 修复它；若仍遇到，手动执行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`（回答 Y），或改用 `claude.cmd` 登录。这不影响 app 翻译，但会挡住手动登录，而没登录就无法翻译。

想更透明地手动逐步安装，见下面的[安装（人工步骤）](#安装人工步骤)。

## 卸载

右键托盘 CC 图标 → **设置** → 滚到最底部 → 点左下角的**「卸载 CC Translate」**按钮。弹出确认框选择是否保留配置和历史记录，确认后会删除程序文件和快捷方式。

> Python、Node.js 和 Claude CLI 等公共工具**不会被卸载**，这些可能被其他程序使用。

## 安装（人工步骤）

```bash
# 1. 获取项目代码
git clone https://github.com/mclight-ship-it/cc-translate.git
cd cc-translate

# 2. 安装 Node.js 和 Python（若已装可跳过）
winget install OpenJS.NodeJS.LTS
winget install Python.Python.3.12

# 3. 安装/升级 Claude Code CLI 并登录（走浏览器 OAuth，用你的订阅，不额外收费）
#    ⚠️ 即使之前装过，也务必跑这条升级到最新版——版本过旧会导致翻译失败或结果异常
npm install -g @anthropic-ai/claude-code@latest
claude --version   # 确认已是最新版；若明显偏旧，重跑上一行强制更新
claude   # 首次运行按提示在浏览器登录，然后 Ctrl+C 退出交互模式

# 可选：安装 GPT provider，并用 ChatGPT 登录
npm install -g @openai/codex@0.146.0
codex login
codex login status

# 4. 安装 Python 依赖
pip install pynput pyperclip pystray Pillow
# 可选增强（缺失时对应功能自动降级/关闭，不影响核心翻译）：
pip install Pygments   # 代码块语法高亮（缺失时降级为单色代码样式）
pip install winsdk     # 截图翻译的离线本地 OCR 引擎（缺失时仍可用视觉模型）
pip install comtypes   # 智能选区识别，避免输入框内无选中时误翻整框（含 VS Code 等跨进程应用）
# 或一键装全部（等价于上面所有包）：pip install -r requirements.txt

# 5. 首次运行（确保当前目录是项目根目录 cc-translate）
python -c "import cc_update,subprocess; subprocess.Popen([cc_update.ensure_branded_launcher() or cc_update.PYTHONW, cc_update.SCRIPT_PATH], cwd=cc_update.APP_DIR)"
```

> ⚠️ **务必更新到最新版 Claude Code CLI**：本工具依赖较新的 `claude -p` 命令行参数，
> 旧版会导致翻译报错或结果异常。**即使你之前已经装过 `claude`，安装本工具前也请再跑一次
> `npm install -g @anthropic-ai/claude-code@latest` 升级到最新版**，并用 `claude --version` 确认。

> 提示：`translator.pyw` 会自动探测两个 CLI，也会识别 npm 全局安装目录。若找不到，
> 请确认对应的 `.cmd` 启动器在 PATH 中。App 调用 Codex 时使用临时会话、只读沙箱和
> 专用工作目录；一旦 JSONL 报告工具事件就会失败关闭。

## 启动方式

首次运行会在本地生成一个很小的品牌启动器，并在开始菜单创建
**CC Translate** 图标。应用仍直接运行当前源码目录，但 Windows 任务管理器会显示
**CC Translate**，不再显示通用的 **Python**。后续直接从开始菜单启动即可。

## 开机自启（可选）

在应用的**设置**里勾选“开机自动启动”即可（会在启动文件夹创建快捷方式）。

## 给 AI 助手的一键安装说明

见 [INSTALL_FOR_LLM.md](docs/INSTALL_FOR_LLM.md)：把该文件内容交给新机器上的 Claude/AI 助手，它会按步骤完成依赖安装、登录、依赖库安装并启动。

## 开发 / 测试

改动流程与约定见 [AGENTS.md](AGENTS.md)。要点：

- 跑测试：`python -m unittest discover -s tests`（标准库，无需额外依赖）。
- 仓库自带 pre-push 钩子，推送前会自动跑测试、失败即阻止推送。
- **新 clone 后启用一次**：`git config core.hooksPath .githooks`。
